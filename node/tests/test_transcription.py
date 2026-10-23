import base64
import functools
import hashlib
import http.client
import io
import json
import struct
import threading
import time
import unittest
import uuid
import wave
from unittest.mock import patch
import test_node as fixtures
from store import Store, StoreError, file_hash
from server import Server
from speech_audio import AudioError, chunks, pcm_layout, wav_part
from analysis_http_fixture import LocalModelFixture


def caf_bytes(seconds=2, little=True):
    raw = struct.pack(('<' if little else '>') + 'h', 700) * (16000 * seconds)
    desc = struct.pack('>d4sIIIII', 16000.0, b'lpcm', 2 if little else 0, 2, 1, 1, 16)
    return (b'caff\x00\x01\x00\x00' + b'desc' + struct.pack('>q', len(desc)) + desc +
            b'free' + struct.pack('>q', 3) + b'abc' + b'data' + struct.pack('>q', len(raw)+4) + b'\0'*4 + raw)


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        fixtures.NodeTests.setUp(self)
        self.model = LocalModelFixture(self.root/'speech-service')
        self.config = {'endpoint': self.model.endpoint, 'model': 'synthetic-speech-fixture', 'key': 'synthetic-model-key'}
        self.store.transcription.configure(self.config)
        self.model.set_response(text='Synthetic speech transcript, not real recognition.')

    def tearDown(self):
        self.store.close()
        self.model.close()
        self.temp.cleanup()

    def receive(self, seconds=2):
        rid, manifest, audio = fixtures.NodeTests.fixture(self)
        audio.write_bytes(caf_bytes(seconds))
        manifest['audioSHA256'] = file_hash(audio)
        manifest['recording']['byteCount'] = audio.stat().st_size
        manifest['recording']['duration'] = seconds
        self.store.accept(manifest, audio)
        return rid, manifest, audio

    def generate(self, rid):
        self.store.transcription.enqueue(rid)
        self.store.transcription.run_once()
        return self.store.transcription.status(rid)

    def test_caf_pcm_roundtrip_both_endianness_and_complete_chunk_coverage(self):
        path = self.root/'input.caf'
        for little in (True, False):
            path.write_bytes(caf_bytes(3, little))
            plan = list(chunks(path, seconds=1))
            self.assertEqual([(a,b) for _,a,b in plan], [(0,16000),(16000,32000),(32000,48000)])
            output = b''
            for layout, start, end in plan:
                with wave.open(io.BytesIO(wav_part(path, layout, start, end)), 'rb') as wav:
                    self.assertEqual((wav.getframerate(),wav.getnchannels(),wav.getsampwidth()), (16000,1,2))
                    output += wav.readframes(wav.getnframes())
            self.assertEqual(output, struct.pack('<h',700)*48000)
        for raw in [b'wrong', caf_bytes()[:-1], caf_bytes().replace(b'lpcm',b'aac '), caf_bytes()+b'truncated']:
            path.write_bytes(raw)
            with self.assertRaises(AudioError): pcm_layout(path)

    def test_provider_request_file_delivery_and_deduplication(self):
        rid, manifest, audio = self.receive()
        self.assertEqual(self.generate(rid)['status'], 'ready')
        request = self.model.requests[0]
        self.assertEqual(request['path'], '/v1/audio/transcriptions')
        self.assertTrue(request['hasAuthorization'])
        self.assertEqual(request['body']['response_format'], 'json')
        self.assertEqual(request['body']['audio']['sha256'], hashlib.sha256(struct.pack('<h',700)*32000).hexdigest())
        text = self.store.read_document('transcripts/'+rid+'.md')['text']
        self.assertIn('Synthetic speech transcript', text)
        self.assertIn(rid, text)
        self.assertIn('尚未人工校对', text)
        self.assertIn('Synthetic speech transcript', self.store.read_document('daily/2026-10-19.md')['text'])
        self.store.accept(manifest, audio)
        self.assertEqual(self.generate(rid)['status'], 'ready')
        self.assertEqual(len(self.model.requests), 1)
        self.assertEqual(file_hash(self.store.audio_path(rid)), manifest['audioSHA256'])
        self.assertEqual(self.store.candidates(), [])
        for path in self.store.data.rglob('*'):
            if path.is_file(): self.assertNotIn(b'synthetic-model-key', path.read_bytes())

    def test_partial_completion_restart_only_sends_remaining_chunks(self):
        rid, _, _ = self.receive(3)
        original = self.store.transcription.request
        count = [0]
        def fail_second(config, audio):
            count[0] += 1
            self.model.set_response(mode='unavailable' if count[0] == 2 else 'valid', text='Synthetic part')
            return original(config, audio)
        split = functools.partial(chunks, seconds=1)
        with patch('transcription.chunks', split), patch.object(self.store.transcription, 'request', fail_second):
            state = self.generate(rid)
        self.assertEqual((state['status'],state['completed'],state['total']), ('retry',1,3))
        self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'], '')
        self.store.close(); self.store = Store(self.root/'node')
        self.assertFalse(self.store.transcription.run_once())
        self.store.transcription.configure(self.config)
        with self.store.db: self.store.db.execute('UPDATE speech_jobs SET next_run=0')
        self.model.set_response(text='Synthetic remaining part')
        with patch('transcription.chunks', split): self.store.transcription.run_once()
        self.assertEqual(self.store.transcription.status(rid)['status'], 'ready')
        self.assertEqual(len(self.model.requests), 4)  # success, failed request, two remaining parts

    def test_manual_edit_even_empty_is_never_overwritten(self):
        for manual in ('Keep my edit.', ''):
            rid, _, _ = self.receive()
            self.store.transcription.enqueue(rid)
            doc = self.store.read_document('transcripts/'+rid+'.md')
            self.store.save_transcript(rid, manual, doc['etag'])
            self.store.transcription.run_once()
            self.assertEqual(self.store.transcription.status(rid)['status'], 'conflict')
            self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'], manual)
            self.assertIn('Synthetic speech transcript', self.store.transcription.generated(rid)['text'])
        self.store.configure({'outputDirectory': str(self.root/'new-library')})
        self.assertIn('Synthetic speech transcript', self.store.transcription.generated(rid)['text'])

    def test_pause_in_flight_keeps_result_and_resume_does_not_resend(self):
        rid, _, _ = self.receive()
        self.model.set_response(text='Synthetic paused result', delay=.3)
        self.store.transcription.enqueue(rid)
        thread = threading.Thread(target=self.store.transcription.run_once)
        thread.start()
        for _ in range(100):
            if self.model.requests: break
            time.sleep(.01)
        self.store.transcription.pause(rid)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.store.transcription.status(rid)['status'], 'paused')
        self.assertEqual(self.generate(rid)['status'], 'ready')
        self.assertEqual(len(self.model.requests), 1)

    def test_network_retry_is_bounded_and_manual_retry_recovers(self):
        rid, _, _ = self.receive()
        self.model.set_response(mode='rate_limit')
        self.store.transcription.enqueue(rid)
        for i in range(3):
            self.store.transcription.run_once()
            state = self.store.transcription.status(rid)
            self.assertEqual(state['status'], 'retry' if i < 2 else 'failed')
            self.assertEqual(state['attempts'], i+1)
            with self.store.db: self.store.db.execute('UPDATE speech_jobs SET next_run=0')
        self.assertFalse(self.store.transcription.run_once())
        self.model.set_response(text='Recovered synthetic transcript')
        self.assertEqual(self.generate(rid)['status'], 'ready')

    def test_provider_failure_empty_response_redirect_and_corrupt_audio(self):
        for mode in ['unauthorized','malformed','empty','redirect']:
            rid, _, _ = self.receive()
            self.model.set_response(mode=mode)
            before = len(self.model.requests)
            self.assertEqual(self.generate(rid)['status'], 'failed')
            self.assertEqual(len(self.model.requests), before+1)
            self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'], '')
        rid, _, _ = self.receive()
        self.store.audio_path(rid).write_bytes(b'corrupted')
        before = len(self.model.requests)
        self.assertEqual(self.generate(rid)['status'], 'failed')
        self.assertEqual(len(self.model.requests), before)

    def test_automatic_only_new_audio_and_service_changes_do_not_redirect_jobs(self):
        old, _, _ = self.receive()
        self.store.transcription.set_automatic({'automatic':True})
        fresh, _, _ = self.receive()
        self.store.transcription.run_once()
        self.assertEqual(self.store.transcription.status(old)['status'], 'idle')
        self.assertEqual(self.store.transcription.status(fresh)['status'], 'ready')
        self.store.transcription.enqueue(old)
        self.store.transcription.configure(dict(self.config,model='different-model'))
        self.assertFalse(self.store.transcription.state()['automatic'])
        self.assertFalse(self.store.transcription.run_once())
        self.assertTrue(self.store.transcription.status(old)['serviceChanged'])
        self.store.transcription.configure(self.config)
        self.store.transcription.set_automatic({'automatic':False})
        self.assertEqual(self.store.transcription.status(old)['status'], 'paused')
        self.assertFalse(self.store.transcription.run_once())

    def test_configuration_never_uses_text_service_or_insecure_transport(self):
        self.store.transcription.configure({'clear':True})
        self.store.analysis.configure(self.config)
        rid, _, _ = self.receive()
        with self.assertRaises(StoreError): self.store.transcription.enqueue(rid)
        for override in [{'endpoint':'http://localhost/v1'}, {'endpoint':'https://example.com/?key=x'},
                         {'endpoint':'https://user:secret@example.com'}, {'key':'bad\r\nHeader'}]:
            with self.assertRaises(StoreError): self.store.transcription.configure(dict(self.config,**override))
        self.assertFalse(self.store.status()['transcriptionConfigured'])

    def test_publish_failure_reuses_durable_result_and_secret_echo_is_rejected(self):
        rid, _, _ = self.receive()
        with patch.object(self.store, 'export', side_effect=OSError('disk unavailable')):
            self.assertEqual(self.generate(rid)['status'], 'failed')
        self.assertEqual(self.store.transcription.status(rid)['completed'], 1)
        self.assertEqual(self.generate(rid)['status'], 'ready')
        self.assertEqual(len(self.model.requests), 1)
        another, _, _ = self.receive()
        self.model.set_response(text='echo synthetic-model-key')
        self.assertEqual(self.generate(another)['status'], 'failed')
        self.assertEqual(self.store.transcription.status(another)['completed'], 0)
        self.assertEqual(self.store.read_document('transcripts/'+another+'.md')['text'], '')

    def test_correcting_service_before_any_completed_parts_allows_explicit_retry(self):
        rid, _, _ = self.receive()
        self.model.set_response(mode='unauthorized')
        self.assertEqual(self.generate(rid)['status'], 'failed')
        self.store.transcription.configure(dict(self.config, model='corrected-speech-model'))
        self.assertFalse(self.store.transcription.run_once())
        self.model.set_response(text='Synthetic corrected configuration')
        self.assertEqual(self.generate(rid)['status'], 'ready')
        self.assertEqual(self.model.requests[-1]['body']['model'], 'corrected-speech-model')

    def test_http_audio_to_job_to_file_and_readonly_permissions(self):
        server = Server(('127.0.0.1',0),self.store,'synthetic-node-token')
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        def request(method,path,body=None,headers=None):
            connection = http.client.HTTPConnection(*server.server_address,timeout=5)
            connection.request(method,path,body=body,headers=headers or {'Authorization':'Bearer '+server.token})
            response=connection.getresponse(); result=(response.status,json.loads(response.read()));connection.close();return result
        try:
            rid, manifest, audio = fixtures.NodeTests.fixture(self)
            audio.write_bytes(caf_bytes()); manifest['audioSHA256']=file_hash(audio)
            manifest['recording']['byteCount']=audio.stat().st_size
            code,_=request('PUT','/v1/recordings/'+rid,audio.read_bytes(),{'Authorization':'Bearer '+server.token,
                'X-Recording-Manifest':base64.b64encode(json.dumps(manifest).encode()).decode()})
            self.assertEqual(code,200)
            self.assertEqual(request('POST','/v1/transcriptions/'+rid,b'{}')[0],202)
            for _ in range(150):
                code,state=request('GET','/v1/transcriptions/'+rid)
                if state['status']=='ready': break
                time.sleep(.02)
            self.assertEqual(state['status'],'ready')
            self.assertIn('Synthetic speech transcript',request('GET','/v1/recordings/'+rid+'/transcript')[1]['text'])
            self.assertEqual(request('POST','/v1/assistant/transcriptions/'+rid,b'{}')[0],401)
        finally:
            server.shutdown();server.server_close();thread.join()

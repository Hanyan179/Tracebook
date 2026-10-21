import base64
import datetime as dt
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import Store, StoreError, file_hash
from server import Server

class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'node')
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def fixture(self, day='2026-10-19T04:00:00Z'):
        rid = str(uuid.uuid4()).upper()
        audio = self.root / (rid + '.caf')
        audio.write_bytes(b'caff\x00\x01\x00\x00' + b'local-synthetic-fixture' * 32)
        m = {'schemaVersion': 1, 'audioFilename': rid + '.caf', 'audioSHA256': file_hash(audio), 'recording': {
            'id': rid, 'duration': 2, 'byteCount': audio.stat().st_size, 'startedAt': day,
            'timeZone': 'Asia/Shanghai', 'trigger': 'button', 'status': 'saved', 'sourceDevice': 'phone', 'title': '测试录音'}}
        return rid, m, audio
    def receive(self, day='2026-10-19T04:00:00Z'):
        rid, m, audio = self.fixture(day);self.store.accept(m,audio);return rid,m,audio
    def text_etag(self, rid):
        return self.store.read_document('transcripts/' + rid + '.md')['etag']
    def test_retries_and_restart_keep_one_record_and_audio(self):
        rid,m,audio=self.receive()
        self.assertTrue(self.store.accept(m,audio)['durable'])
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(len(self.store.list_recordings()),1)
        self.assertEqual(self.store.audio_path(rid).read_bytes(),audio.read_bytes())
        self.assertEqual(self.store.receipt(rid)['audioSHA256'],m['audioSHA256'])
    def test_checksum_mismatch_and_id_conflict_never_overwrite(self):
        rid,m,audio=self.receive();original=self.store.audio_path(rid).read_bytes()
        audio.write_bytes(b'x'*audio.stat().st_size)
        with self.assertRaises(StoreError):self.store.accept(m,audio)
        m['audioSHA256']=file_hash(audio)
        with self.assertRaises(StoreError) as error:self.store.accept(m,audio)
        self.assertEqual(error.exception.status,409)
        self.assertEqual(self.store.audio_path(rid).read_bytes(),original)
    def test_path_traversal_is_rejected(self):
        rid,m,audio=self.fixture();m['audioFilename']='../../outside.caf'
        with self.assertRaises(StoreError):self.store.accept(m,audio)
        self.assertEqual(self.store.list_recordings(),[])
    def test_receive_recovers_directory_committed_before_database(self):
        rid,m,audio=self.receive()
        with self.store.db:self.store.db.execute('DELETE FROM recordings WHERE id=?',(rid,))
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.list_recordings()[0]['id'],rid)
    def test_manual_text_daily_memory_and_prompts_work_without_models(self):
        rid,m,audio=self.receive()
        d=self.store.save_transcript(rid,'今天讨论了项目交付。下周五回顾进展。',self.text_etag(rid))
        self.assertTrue(d['etag'])
        daily=self.store.read_document('daily/2026-10-19.md')
        self.assertIn(rid,daily['text']);self.assertIn('下周五',daily['text'])
        cid=self.store.add_candidate('我每周五回顾项目进展。',[rid])['id']
        self.assertNotIn('每周五',self.store.read_document('memory.md')['text'])
        self.store.decide_candidate(cid,'confirmed')
        self.assertIn('每周五',self.store.read_document('memory.md')['text'])
        prompt=self.store.read_document('prompts/daily-summary.md')
        self.store.edit_document(prompt['path'],'自定义规则',prompt['etag'])
        self.assertEqual(self.store.read_document(prompt['path'])['text'],'自定义规则')
    def test_manual_edits_survive_late_recordings_and_are_reported(self):
        rid,_,_=self.receive();self.store.save_transcript(rid,'第一条',self.text_etag(rid))
        path=self.store.output/'daily/2026-10-19.md';path.write_text('人工修改内容')
        other,_,_=self.receive();self.store.save_transcript(other,'迟到的第二条',self.text_etag(other))
        self.assertEqual(path.read_text(),'人工修改内容')
        conflict=self.store.status()['conflicts'][0]
        generated=self.store.output/conflict['generatedPath']
        self.assertIn('迟到的第二条',generated.read_text())
    def test_concurrent_editor_rejects_stale_etag(self):
        rid,_,_=self.receive();d=self.store.save_transcript(rid,'一',self.text_etag(rid))
        self.store.save_transcript(rid,'二',d['etag'])
        with self.assertRaises(StoreError) as error:self.store.save_transcript(rid,'过期覆盖',d['etag'])
        self.assertEqual(error.exception.status,409)
        self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'],'二')
    def test_memory_needs_source_and_decision_is_idempotent(self):
        with self.assertRaises(StoreError):self.store.add_candidate('无来源建议',[])
        rid,_,_=self.receive();cid=self.store.add_candidate('候选事实',[rid])['id']
        self.store.decide_candidate(cid,'confirmed');self.store.decide_candidate(cid,'confirmed')
        self.assertEqual(self.store.read_document('memory.md')['text'].count('候选事实'),1)
        with self.assertRaises(StoreError):self.store.decide_candidate(cid,'rejected')
    def test_http_auth_upload_download_and_persistent_requests(self):
        server=Server(('127.0.0.1',0),self.store,'test-token-never-used-outside-tests')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection(*server.server_address,timeout=5)
            conn.request('GET','/v1/recordings');r=conn.getresponse();self.assertEqual(r.status,401);r.read();conn.close()
            rid,m,audio=self.fixture()
            headers={'Authorization':'Bearer '+server.token,'X-Recording-Manifest':base64.b64encode(json.dumps(m).encode()).decode()}
            conn=http.client.HTTPConnection(*server.server_address,timeout=5)
            conn.request('PUT','/v1/recordings/'+rid,body=audio.read_bytes(),headers=headers)
            response=conn.getresponse();self.assertEqual(response.status,200);receipt=json.loads(response.read());self.assertTrue(receipt['durable'])
            conn.request('GET','/v1/recordings/'+rid+'/audio',headers={'Authorization':'Bearer '+server.token})
            r=conn.getresponse();self.assertEqual(r.read(),audio.read_bytes())
            conn.request('POST','/v1/daily/2026-10-19',body=b'{}',headers={'Authorization':'Bearer '+server.token})
            r=conn.getresponse();self.assertEqual(r.status,200);r.read()
            conn.request('GET','/v1/recordings',headers={'Authorization':'Bearer '+server.token})
            r=conn.getresponse();self.assertEqual(r.status,200);self.assertEqual(len(json.loads(r.read())),1);conn.close()
        finally:server.shutdown();server.server_close();thread.join()

    def test_output_change_keeps_transcripts_and_old_folder(self):
        rid,_,_=self.receive();self.store.save_transcript(rid,'需要保留的文字',self.text_etag(rid))
        old=self.store.output
        chosen=self.root/'chosen-folder'
        self.store.configure({'outputDirectory':str(chosen)})
        self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'],'需要保留的文字')
        self.assertEqual((old/'transcripts'/ (rid+'.md')).read_text(),'需要保留的文字')
    def test_daily_uses_node_timezone_and_does_not_invent_transcript(self):
        rid,_,_=self.receive('2026-10-19T16:30:00Z')
        self.assertTrue((self.store.output/'transcripts'/(rid+'.md')).exists())
        self.assertEqual(self.store.list_recordings()[0]['textState'], 'awaitingText')
        self.store.build_daily('2026-10-20')
        text=self.store.read_document('daily/2026-10-20.md')['text']
        self.assertIn(rid,text);self.assertIn('等待添加文字',text)
        self.assertIn('按来源整理的原文，不代表模型分析',text)
    def test_memory_conflict_preserves_human_file_and_keeps_confirmed_state(self):
        rid,_,_=self.receive();cid=self.store.add_candidate('经用户确认的事实',[rid])['id']
        (self.store.output/'memory.md').write_text('人工记忆')
        self.store.decide_candidate(cid,'confirmed')
        self.assertEqual((self.store.output/'memory.md').read_text(),'人工记忆')
        conflict=next(c for c in self.store.status()['conflicts'] if c['path']=='memory.md')
        self.assertIn('经用户确认的事实',(self.store.output/conflict['generatedPath']).read_text())
        self.assertEqual(self.store.candidates()[0]['state'],'confirmed')
    def test_output_symlink_cannot_escape_chosen_root(self):
        outside=self.root/'outside';outside.mkdir()
        (self.store.output/'transcripts').symlink_to(outside,target_is_directory=True)
        rid,_,_=self.receive()
        with self.assertRaises(StoreError):self.store.save_transcript(rid,'不得写到外部','')
        self.assertEqual(list(outside.iterdir()),[])

if __name__=='__main__':unittest.main()

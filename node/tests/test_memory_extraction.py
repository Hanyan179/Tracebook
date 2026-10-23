import http.client
import json
import threading
import unittest
from unittest.mock import patch
import test_memories as fixtures
from server import Server
from store import Store, StoreError
from memory_extraction import MAX_CHARACTERS
from analysis_http_fixture import LocalModelFixture


class MemoryExtractionTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    tearDown = fixtures.MemoryTests.tearDown
    note = fixtures.MemoryTests.note
    reference = fixtures.MemoryTests.reference
    receive = fixtures.MemoryTests.receive
    fixture = fixtures.MemoryTests.fixture
    text_etag = fixtures.MemoryTests.text_etag
    folder = fixtures.MemoryTests.folder
    write = fixtures.MemoryTests.write
    meta = fixtures.MemoryTests.meta
    user = fixtures.MemoryTests.user
    collect = fixtures.MemoryTests.collect

    def configure(self):
        self.store.analysis.configure({'endpoint': 'https://synthetic.invalid/v1', 'model': 'synthetic-model', 'key': 'synthetic-key-only'})

    def prepare(self, kind='note', rid=None):
        self.configure()
        rid = rid or self.note('I prefer a weekly Friday orchard review.')['id']
        ref = self.reference(kind, rid)
        return kind, rid, {'contentVersion': ref['contentVersion'], 'sendToModel': True,
                           'model': 'synthetic-model', 'endpoint': 'https://synthetic.invalid/v1'}, ref['quote']

    def response(self, quote, text='Prefers a weekly Friday orchard review.'):
        return {'candidates': [{'text': text, 'quote': quote, 'subject': 'user', 'basis': 'explicit'}]}

    def run_source(self, prepared, response=None):
        kind, rid, values, text = prepared
        with patch.object(self.store.analysis, 'request_json', return_value=response if response is not None else self.response(text)) as call:
            state = self.store.memory_extraction.start(kind, rid, values, background=False)
        return state, call

    def test_one_selected_source_to_pending_files_and_history_with_no_secret(self):
        p = self.prepare(); self.note('An unrelated source must not be sent.')
        before = self.store.read_document('notes/' + p[1] + '.md')['text']
        state, call = self.run_source(p)
        self.assertEqual(state['status'], 'ready')
        sent = call.call_args[0][2]
        self.assertEqual(sent['id'], p[1]); self.assertNotIn('unrelated source', json.dumps(sent))
        self.assertNotIn('synthetic-key-only', json.dumps(sent))
        cid = state['candidateIDs'][0]
        detail = self.store.memories.detail(cid)
        self.assertEqual(detail['state'], 'pending'); self.assertEqual(detail['proposal']['model'], 'synthetic-model')
        self.assertEqual(detail['evidence'][0]['quote'], p[3])
        self.assertEqual(detail['evidence'][0]['contentVersion'], p[2]['contentVersion'])
        self.assertEqual(self.store.memories.history(cid)['items'][0]['proposal'], detail['proposal'])
        self.assertNotIn(cid, self.store.read_document('memory.md')['text'])
        self.assertIn(cid, self.store.read_document('memory-inbox.md')['text'])
        portable = self.store.read_document('memories/' + cid + '.json')['text']
        self.assertIn('synthetic-model', portable)
        self.assertEqual(before, self.store.read_document('notes/' + p[1] + '.md')['text'])
        for path in self.store.data.rglob('*'):
            if path.is_file():self.assertNotIn(b'synthetic-key-only', path.read_bytes())

    def test_requires_selection_configuration_current_version_and_bounded_text(self):
        p = self.prepare()
        with self.assertRaises(StoreError):self.store.memory_extraction.start(p[0], p[1], {'contentVersion': p[2]['contentVersion']}, False)
        self.store.analysis.configure({'clear': True})
        with self.assertRaises(StoreError):self.store.memory_extraction.start(*p[:3], background=False)
        self.configure()
        note = self.note('x' * (MAX_CHARACTERS + 1)); ref = self.reference('note', note['id'])
        with self.assertRaises(StoreError) as large:self.store.memory_extraction.start('note', note['id'], {**p[2], 'contentVersion': ref['contentVersion']}, False)
        self.assertEqual(large.exception.status, 413)
        with self.assertRaises(StoreError):self.store.memory_extraction.start('note', p[1], {**p[2], 'contentVersion': '0'*64}, False)
        with self.assertRaises(StoreError):self.store.memory_extraction.start(*p[:2], {**p[2], 'endpoint': 'https://another.invalid'}, False)
        self.assertEqual(self.store.candidates(), [])

    def test_duplicate_response_and_repeated_requests_restart_do_not_duplicate(self):
        p = self.prepare(); response = self.response(p[3]); response['candidates'] *= 2
        first, _ = self.run_source(p, response)
        self.store.close(); self.store = Store(self.root / 'node')
        with patch.object(self.store.analysis, 'request_json') as call:
            second = self.store.memory_extraction.start(*p[:3], background=False)
            call.assert_not_called()
        self.assertEqual(first['candidateIDs'], second['candidateIDs']); self.assertEqual(len(self.store.candidates()), 1)

    def test_invalid_quote_or_attribution_rejects_entire_batch(self):
        p = self.prepare()
        for invalid in [{'quote': 'invented'}, {'subject': 'other'}, {'basis': 'inference'}, {'quote': ''}, {'text': 1}]:
            response = self.response(p[3]); response['candidates'].append({**response['candidates'][0], **invalid})
            state, _ = self.run_source(p, response)
            self.assertEqual(state['status'], 'failed'); self.assertEqual(self.store.candidates(), [])

    def test_ambiguous_repeated_quote_is_rejected(self):
        note = self.note('I like tea. A quote says: I like tea.')
        p = self.prepare(rid=note['id'])
        state, _ = self.run_source(p, self.response('I like tea.'))
        self.assertEqual(state['status'], 'failed'); self.assertEqual(self.store.candidates(), [])

    def test_empty_response_is_a_persisted_valid_result(self):
        p = self.prepare(); state, _ = self.run_source(p, {'candidates': []})
        self.assertEqual(state['status'], 'ready'); self.assertEqual(state['candidateIDs'], [])
        with patch.object(self.store.analysis, 'request_json') as call:
            self.store.memory_extraction.start(*p[:3], background=False); call.assert_not_called()

    def test_source_or_rules_change_during_request_cannot_save_stale_proposals(self):
        p = self.prepare()
        def change(*args):
            self.store._path('notes/' + p[1] + '.md').write_text('A manual edit during request.')
            return self.response(p[3])
        with patch.object(self.store.analysis, 'request_json', side_effect=change):
            state = self.store.memory_extraction.start(*p[:3], background=False)
        self.assertEqual(state['status'], 'failed'); self.assertEqual(self.store.candidates(), [])
        p = self.prepare()
        def change_rules(*args):
            self.store._path('prompts/memory-extraction.md').write_text('New rules.')
            return self.response(p[3])
        with patch.object(self.store.analysis, 'request_json', side_effect=change_rules):
            state = self.store.memory_extraction.start(*p[:3], background=False)
        self.assertEqual(state['status'], 'failed'); self.assertEqual(self.store.candidates(), [])

    def test_failure_is_safe_and_retry_succeeds(self):
        p = self.prepare()
        with patch.object(self.store.analysis, 'request_json', side_effect=RuntimeError('synthetic-key-only')):
            failed = self.store.memory_extraction.start(*p[:3], background=False)
        self.assertEqual(failed['status'], 'failed'); self.assertNotIn('synthetic-key-only', json.dumps(failed))
        state, _ = self.run_source(p); self.assertEqual(state['status'], 'ready')

    def test_export_retry_uses_saved_batch_and_preserves_human_memory_file(self):
        p = self.prepare()
        self.store._path('memory-inbox.md').write_text('Human memory edits.')
        with patch.object(self.store, 'export_memory', side_effect=OSError('disk unavailable')):
            state, _ = self.run_source(p)
        self.assertEqual(state['status'], 'failed'); self.assertEqual(len(self.store.candidates()), 1)
        self.store.close(); self.store = Store(self.root / 'node')
        with patch.object(self.store.analysis, 'request_json') as call:
            state = self.store.memory_extraction.start(*p[:3], background=False); call.assert_not_called()
        self.assertEqual(state['status'], 'ready'); self.assertEqual(len(self.store.candidates()), 1)
        self.assertEqual(self.store.read_document('memory-inbox.md')['text'], 'Human memory edits.')
        self.assertEqual(len(self.store.memories.history(state['candidateIDs'][0])['items']), 1)

    def test_interrupted_request_does_not_auto_send_after_restart(self):
        p = self.prepare(); state, _ = self.run_source(p)
        with self.store.db:self.store.db.execute("UPDATE memory_extractions SET status='running' WHERE id=?", (state['id'],))
        self.store.close(); self.store = Store(self.root / 'node')
        state = self.store.memory_extraction.status(p[0], p[1], p[2]['contentVersion'])
        self.assertEqual(state['status'], 'interrupted'); self.assertFalse(state['configured'])
        with patch.object(self.store.analysis, 'request_json') as call:
            self.store.memory_extraction.start(*p[:3], background=False); call.assert_not_called()

    def test_four_source_kinds_keep_exact_evidence(self):
        rid, _, _ = self.receive(); self.store.save_transcript(rid, 'I prefer tea.', self.text_etag(rid))
        preview = self.store.documents.preview({'files': [{'name': 'journal.txt', 'text': 'Synthetic user-authored journal: I prefer tea.'}]})
        self.store.documents.commit(preview['id']); doc = self.store.db.execute('SELECT id FROM documents').fetchone()[0]
        root, path = self.folder(); self.write(path, [self.meta(), self.user('I prefer tea.')]); self.collect('codex', root)
        inp = self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        for kind, identifier in [('recording', rid), ('document', doc), ('input', inp), ('note', self.note()['id'])]:
            p = self.prepare(kind, identifier); state, _ = self.run_source(p)
            self.assertEqual(state['status'], 'ready')
            evidence = self.store.memories.detail(state['candidateIDs'][0])['evidence'][0]
            self.assertEqual((evidence['kind'], evidence['id'], evidence['quote']), (kind, identifier, p[3]))

    def test_atomic_batch_rolls_back_every_candidate_if_history_write_fails(self):
        p = self.prepare(); original = self.store.memories.record_version
        def fail_second(cid, action, now):
            if self.store.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0] == 2:raise OSError('disk')
            original(cid, action, now)
        response = self.response(p[3]); response['candidates'].append({**response['candidates'][0], 'text': 'Another synthetic proposal.'})
        with patch.object(self.store.memories, 'record_version', side_effect=fail_second):
            state, _ = self.run_source(p, response)
        self.assertEqual(state['status'], 'failed'); self.assertEqual(self.store.candidates(), [])
        state, _ = self.run_source(p, response); self.assertEqual(len(state['candidateIDs']), 2)

    def test_read_only_grant_cannot_start_or_read_tasks(self):
        p = self.prepare()
        grant = self.store.knowledge.create_grant({'name': 'reader', 'scopes': ['notes'], 'expiresInDays': 1})
        server = Server(('127.0.0.1', 0), self.store, 'synthetic-connection')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            path = '/v1/memory-extractions/note/' + p[1]
            for prefix in ('', '/assistant'):
                conn = http.client.HTTPConnection(*server.server_address)
                conn.request('POST', path.replace('/v1', '/v1' + prefix), json.dumps(p[2]),
                             {'Authorization': 'Bearer ' + grant['token'], 'Content-Type': 'application/json'})
                response = conn.getresponse(); response.read(); self.assertIn(response.status, (401, 403))
                conn.close()
        finally:server.shutdown(); server.server_close(); thread.join()
        self.assertEqual(self.store.candidates(), [])

    def test_real_loopback_https_transport_rejects_redirect_and_bad_quote_then_recovers(self):
        p = self.prepare()
        fixture = LocalModelFixture(self.root / 'model')
        self.store.analysis.configure({'endpoint': fixture.endpoint, 'model': 'synthetic-model', 'key': 'synthetic-model-key'})
        values = {**p[2], 'endpoint': fixture.endpoint}
        try:
            for mode in ('unauthorized', 'redirect', 'bad_citation'):
                fixture.set_response(mode=mode)
                state = self.store.memory_extraction.start(p[0], p[1], values, background=False)
                self.assertEqual(state['status'], 'failed'); self.assertEqual(self.store.candidates(), [])
            fixture.set_response(text='Synthetic protocol response, not real model evidence.')
            state = self.store.memory_extraction.start(p[0], p[1], values, background=False)
            self.assertEqual(state['status'], 'ready'); self.assertEqual(len(fixture.requests), 4)
            self.assertTrue(all(r['path'] == '/v1/chat/completions' for r in fixture.requests))
            self.assertTrue(all(r['hasAuthorization'] for r in fixture.requests))
        finally:fixture.close()

    def test_running_task_is_reused_without_starting_second_request(self):
        p = self.prepare(); entered, release = threading.Event(), threading.Event()
        def wait_for_release(*args):
            entered.set(); release.wait(timeout=5)
            return self.response(p[3])
        with patch.object(self.store.analysis, 'request_json', side_effect=wait_for_release) as call:
            first = self.store.memory_extraction.start(*p[:3])
            self.assertTrue(entered.wait(timeout=2))
            second = self.store.memory_extraction.start(*p[:3])
            self.assertEqual(first['id'], second['id']); self.assertEqual(second['status'], 'running')
            release.set(); self.store.memory_extraction.worker.join(timeout=5)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(len(self.store.candidates()), 1)

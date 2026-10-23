import http.client
import json
import re
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch
import test_node as fixtures
import test_local_sources as local_fixtures
from store import Store, StoreError
from server import Server


class MemoryTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown
    receive = fixtures.NodeTests.receive
    fixture = fixtures.NodeTests.fixture
    text_etag = fixtures.NodeTests.text_etag
    folder = local_fixtures.LocalSourceTests.folder
    write = local_fixtures.LocalSourceTests.write
    meta = local_fixtures.LocalSourceTests.meta
    user = local_fixtures.LocalSourceTests.user
    collect = local_fixtures.LocalSourceTests.collect

    def note(self, text='Synthetic Friday orchard review. Private source detail.'):
        return self.store.knowledge.save_note({'title': 'Synthetic note', 'text': text})

    def reference(self, kind, rid, quote=None):
        _, body, version = self.store.memories.source(kind, rid)
        return {'kind': kind, 'id': rid, 'contentVersion': version, 'quote': body if quote is None else quote}

    def propose(self, note=None, text='Orchard review on Friday.'):
        note = note or self.note()
        return self.store.add_candidate(text, evidence=[self.reference('note', note['id'])])['id']

    def update(self, cid, **values):
        return self.store.memories.update(cid, {'memoryRevision': self.store.memories.detail(cid)['memoryRevision'], **values})

    def test_all_four_sources_have_versioned_excerpts_and_legacy_recording_ids(self):
        rid, _, _ = self.receive()
        self.store.save_transcript(rid, 'Synthetic voice orchard plan.', self.text_etag(rid))
        note = self.note()
        preview = self.store.documents.preview({'files': [{'name': 'orchard.txt', 'text': 'Synthetic document orchard plan.'}]})
        self.store.documents.commit(preview['id'])
        doc = self.store.db.execute('SELECT id FROM documents').fetchone()[0]
        root, path = self.folder(); self.write(path, [self.meta(), self.user('Synthetic tool orchard plan.')])
        self.collect('codex', root)
        inp = self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        refs = [self.reference(kind, source) for kind, source in [('recording', rid), ('note', note['id']), ('document', doc), ('input', inp)]]
        cid = self.store.add_candidate('Synthetic multi-source proposal.', evidence=refs + refs[:1])['id']
        result = self.store.memories.detail(cid)
        self.assertEqual(result['sources'], [rid])
        self.assertEqual(len(result['evidence']), 4)
        self.assertEqual(result['state'], 'pending')
        self.assertTrue(all(e['sourceStatus'] == 'current' and len(e['contentVersion']) == 64 for e in result['evidence']))
        self.assertEqual(self.store.catalog.detail('memory', cid)['memoryRevision'], 1)
        self.update(cid, state='confirmed')
        portable = self.store.read_document('memory.md')['text']
        for target in re.findall(r'\]\(([^)]+)\)', portable):
            self.assertTrue((self.store.output / target).is_file(), target)

    def test_evidence_requires_existing_supported_source_exact_quote_and_current_version(self):
        note = self.note(); ref = self.reference('note', note['id'])
        for invalid in [{**ref, 'quote': 'Invented evidence'}, {**ref, 'kind': 'analysis'},
                        {**ref, 'kind': ['note']}, {**ref, 'kind': 'memory'}, {**ref, 'quote': ''},
                        {**ref, 'id': '../escape'}, {**ref, 'quoteStart': True}]:
            with self.assertRaises(StoreError): self.store.add_candidate('No write', evidence=[invalid])
        self.store.knowledge.save_note({'title': note['title'], 'text': 'Changed original', 'etag': note['etag']}, note['id'])
        with self.assertRaises(StoreError) as stale: self.store.add_candidate('No write', evidence=[ref])
        self.assertEqual(stale.exception.status, 409)
        self.assertEqual(self.store.candidates(), [])

    def test_source_changes_or_archival_do_not_rewrite_saved_evidence(self):
        note = self.note(); cid = self.propose(note)
        saved = self.store.memories.detail(cid)['evidence'][0]
        self.store.knowledge.save_note({'title': note['title'], 'text': 'Changed original', 'etag': note['etag']}, note['id'])
        changed = self.store.memories.detail(cid)['evidence'][0]
        self.assertEqual(changed['quote'], saved['quote'])
        self.assertEqual(changed['contentVersion'], saved['contentVersion'])
        self.assertEqual(changed['sourceStatus'], 'changed')
        self.store.catalog.update('note', note['id'], {'revision': 0, 'archived': True})
        self.assertEqual(self.store.memories.detail(cid)['evidence'][0]['sourceStatus'], 'unavailable')
        self.store.close(); self.store = Store(self.root / 'node')
        self.assertEqual(self.store.memories.detail(cid)['evidence'][0]['quote'], saved['quote'])

    def test_explicit_repeated_quote_position_survives_confirmation_and_restart(self):
        quote = '我每周五回顾工作。'
        prefix = '同事说：' + quote + '\n本人 👩🏽‍💻 cafe\u0301：'
        note = self.note(prefix + quote)
        ref = {**self.reference('note', note['id'], quote), 'quoteStart': len(prefix)}
        cid = self.store.add_candidate('Synthetic personal decision', evidence=[ref])['id']
        self.update(cid, state='confirmed')
        with self.assertRaises(StoreError):
            self.store.add_candidate('Wrong UTF-16 position', evidence=[{**ref, 'quoteStart': len(prefix.encode('utf-16-le')) // 2}])
        self.store.close(); self.store = Store(self.root / 'node')
        value = self.store.memories.detail(cid)
        self.assertEqual(value['state'], 'confirmed')
        self.assertEqual(value['evidence'][0]['quoteStart'], len(prefix))
        self.assertEqual(value['evidence'][0]['quote'], quote)
        self.assertEqual(value['evidence'][0]['contentVersion'], ref['contentVersion'])
        self.assertEqual(len(self.store.candidates()), 1)

    def test_confirm_edit_disable_restore_and_history_control_new_retrieval(self):
        cid = self.propose()
        def matches(): return self.store.knowledge.query({'query': 'orchard'}, ['confirmedMemory'])['sources']
        self.assertEqual(matches(), [])
        self.update(cid, state='confirmed')
        self.assertEqual([r['id'] for r in matches()], [cid])
        self.update(cid, text='Orchard review on Monday.')
        self.assertEqual(matches()[0]['excerpt'], 'Orchard review on Monday.')
        self.update(cid, state='inactive')
        self.assertEqual(matches(), [])
        with self.assertRaises(StoreError): self.store.knowledge.read_source('memory', cid, ['confirmedMemory'], {})
        self.assertEqual(self.store.catalog.query('memory', {'state': 'inactive'})['total'], 1)
        self.assertNotIn(cid, self.store.read_document('memory.md')['text'])
        self.assertIn(cid, self.store.read_document('memory-inactive.md')['text'])
        self.update(cid, state='confirmed')
        self.assertEqual(len(matches()), 1)
        events = self.store.memories.history(cid)['items']
        self.assertEqual([r['action'] for r in events], ['restored', 'disabled', 'edited', 'confirmed', 'created'])
        self.assertEqual(events[-1]['text'], 'Orchard review on Friday.')
        self.assertEqual(self.store.memories.detail(cid)['memoryRevision'], 5)

    def test_missing_source_keeps_snapshot_and_reports_unavailable(self):
        note = self.note(); cid = self.propose(note)
        file = self.store.output / note['path']; original = file.read_bytes()
        file.unlink()
        evidence = self.store.memories.detail(cid)['evidence'][0]
        self.assertEqual(evidence['sourceStatus'], 'unavailable')
        self.assertIn('orchard', evidence['quote'])
        file.write_bytes(original)
        self.assertEqual(self.store.memories.detail(cid)['evidence'][0]['sourceStatus'], 'current')

    def test_pending_never_expires_into_fact_and_rejected_can_be_reviewed(self):
        cid = self.propose()
        with self.store.db: self.store.db.execute("UPDATE candidates SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (cid,))
        self.store.close(); self.store = Store(self.root / 'node')
        self.assertEqual(self.store.memories.detail(cid)['state'], 'pending')
        self.update(cid, state='rejected'); self.update(cid, state='pending')
        self.assertEqual(self.store.knowledge.query({'query': 'orchard'}, ['confirmedMemory'])['sources'], [])

    def test_stale_edits_and_invalid_transitions_do_not_overwrite(self):
        cid = self.propose(); original = self.store.memories.detail(cid)
        self.update(cid, text='Orchard revised.')
        with self.assertRaises(StoreError) as stale:
            self.store.memories.update(cid, {'memoryRevision': original['memoryRevision'], 'text': 'Stale overwrite'})
        self.assertEqual(stale.exception.status, 409)
        for state in ['inactive', 'invented', []]:
            with self.assertRaises(StoreError): self.update(cid, state=state)
        with self.assertRaises(StoreError): self.store.memories.update(cid, {'text': 'Missing revision'})
        self.assertEqual(self.store.memories.detail(cid)['text'], 'Orchard revised.')
        self.assertEqual(len(self.store.memories.history(cid)['items']), 2)

    def test_retries_after_file_failure_or_restart_do_not_duplicate_memory_or_revision(self):
        note = self.note(); operation = str(uuid.uuid4()); ref = self.reference('note', note['id'])
        with patch.object(self.store, 'export_memory', side_effect=OSError('Synthetic disk unavailable')):
            with self.assertRaises(OSError): self.store.add_candidate('Orchard', evidence=[ref], request_id=operation)
        self.store.close(); self.store = Store(self.root / 'node')
        result = self.store.add_candidate('Orchard', evidence=[ref], request_id=operation)
        self.assertTrue(result['reused']); self.assertEqual(len(self.store.candidates()), 1)
        cid = result['id']; update = {'memoryRevision': 1, 'state': 'confirmed', 'requestID': str(uuid.uuid4())}
        self.store.memories.update(cid, update); self.store.memories.update(cid, update)
        self.assertEqual(self.store.memories.detail(cid)['memoryRevision'], 2)
        with self.assertRaises(StoreError): self.store.add_candidate('Different', evidence=[ref], request_id=operation)
        self.assertEqual(len(self.store.memories.history(cid)['items']), 2)

    def test_old_database_migration_preserves_links_and_does_not_invent_historic_text(self):
        rid, _, _ = self.receive(); cid = str(uuid.uuid4()).upper()
        with self.store.db:
            self.store.db.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?)',
                                  (cid, 'Legacy orchard', json.dumps([rid]), 'confirmed', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z'))
        self.store.save_transcript(rid, 'New text must not be presented as old evidence', self.text_etag(rid))
        self.store.close(); self.store = Store(self.root / 'node')
        result = self.store.memories.detail(cid)
        self.assertEqual(result['sources'], [rid]); self.assertEqual(result['state'], 'confirmed')
        self.assertEqual(result['evidence'][0]['snapshotState'], 'legacy')
        self.assertEqual(result['evidence'][0]['quote'], '')
        self.assertEqual(result['evidence'][0]['contentVersion'], '')
        self.store.close(); self.store = Store(self.root / 'node')
        self.assertEqual(len(self.store.memories.history(cid)['items']), 1)

    def test_history_is_bounded_and_can_be_read_to_completion(self):
        cid = self.propose()
        for i in range(43): self.update(cid, text='Revision ' + str(i))
        first = self.store.memories.history(cid)
        second = self.store.memories.history(cid, first['nextBefore'])
        revisions = [r['memoryRevision'] for r in first['items'] + second['items']]
        self.assertEqual(revisions, list(range(44, 0, -1)))
        self.assertIsNone(second['nextBefore'])

    def test_manual_files_survive_and_text_export_includes_all_states_and_history(self):
        cid = self.propose(); self.update(cid, state='confirmed')
        path = self.store.output / 'memories' / (cid + '.json')
        path.write_text('Human edited evidence file')
        (self.store.output / 'memory.md').write_text('Human edited memory file')
        self.update(cid, state='inactive')
        self.assertEqual(path.read_text(), 'Human edited evidence file')
        self.assertEqual(self.store.read_document('memory.md')['text'], 'Human edited memory file')
        conflict = next(r for r in self.store.status()['conflicts'] if r['path'] == 'memories/' + cid + '.json')
        generated = conflict['generatedPath']
        with zipfile.ZipFile(self.store.exchange.export()) as bundle:
            self.assertIn(cid, bundle.read('memory-inactive.md').decode())
            self.assertIn('disabled', bundle.read(generated).decode())
            self.assertIn('Private source detail.', bundle.read(generated).decode())
            self.assertNotIn('connection-token', bundle.namelist())
        self.store.configure({'outputDirectory': str(self.root / 'moved')})
        self.assertEqual(self.store.read_document('memories/' + cid + '.json')['text'], 'Human edited evidence file')
        self.assertTrue((self.store.output / generated).is_file())

    def test_memory_only_reader_does_not_gain_source_excerpts_or_history(self):
        cid = self.propose(); self.update(cid, state='confirmed')
        grant = self.store.knowledge.create_grant({'name': 'Memory reader', 'scopes': ['confirmedMemory'], 'days': 1})
        server = Server(('127.0.0.1', 0), self.store, 'memory-synthetic-node-token')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            client = http.client.HTTPConnection(*server.server_address, timeout=5)
            headers = {'Authorization': 'Bearer ' + grant['token']}
            client.request('GET', '/v1/assistant/items/memory/' + cid, headers=headers)
            response = client.getresponse(); self.assertEqual(response.status, 200)
            raw = response.read().decode(); self.assertNotIn('Private source detail.', raw); self.assertNotIn('evidence', json.loads(raw))
            client.request('GET', '/v1/candidates/' + cid + '/history', headers=headers)
            response = client.getresponse(); self.assertEqual(response.status, 401); response.read(); client.close()
        finally: server.shutdown(); server.server_close(); worker.join()

    def test_authenticated_http_create_detail_edit_history_flow(self):
        note = self.note(); server = Server(('127.0.0.1', 0), self.store, 'memory-synthetic-node-token')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            client = http.client.HTTPConnection(*server.server_address, timeout=5)
            def call(method, path, body=None):
                client.request(method, path, body=json.dumps(body) if body is not None else None,
                               headers={'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json'})
                response = client.getresponse(); value = json.loads(response.read())
                self.assertLess(response.status, 300, value); return value
            body = {'text': 'Orchard Friday', 'evidence': [self.reference('note', note['id'])], 'requestID': str(uuid.uuid4())}
            created = call('POST', '/v1/candidates', body); cid = created['id']
            self.assertEqual(call('POST', '/v1/candidates', body)['id'], cid)
            detail = call('GET', '/v1/candidates/' + cid)
            call('PUT', '/v1/candidates/' + cid, {'memoryRevision': detail['memoryRevision'], 'state': 'confirmed'})
            self.assertEqual(len(call('GET', '/v1/candidates/' + cid + '/history')['items']), 2)
            client.close()
        finally: server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__':
    unittest.main()

import http.client
import json
import threading
import unittest
from unittest.mock import patch
import test_memories as fixtures
import test_memory_resolution as resolution
from store import Store
from server import Server


class MemoryDeliveryTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    tearDown = fixtures.MemoryTests.tearDown
    note = fixtures.MemoryTests.note
    reference = fixtures.MemoryTests.reference
    propose = fixtures.MemoryTests.propose
    update = fixtures.MemoryTests.update
    pair = resolution.MemoryResolutionTests.pair
    values = resolution.MemoryResolutionTests.values

    def restart(self):
        self.store.close()
        self.store = Store(self.root / 'node')

    def test_committed_decision_survives_crash_before_export_without_request_id(self):
        new, old = self.pair()
        with patch.object(self.store, 'export_memory', side_effect=OSError('crash before export')):
            with self.assertRaises(OSError): self.store.memories.resolve(new, self.values(new, old))
        self.restart()
        self.assertEqual(self.store.memories.delivery_status()['state'], 'pending')
        self.assertEqual(self.store.memories.deliver_pending()['state'], 'ready')
        self.assertIn(new, self.store.read_document('memory.md')['text'])
        self.assertNotIn(old, self.store.read_document('memory.md')['text'])
        self.assertIn(old, self.store.read_document('memory-inactive.md')['text'])
        before = self.store.db.execute('SELECT count(*) FROM memory_versions').fetchone()[0]
        with patch.object(self.store, 'export_memory', side_effect=AssertionError('healthy files must not be rewritten')):
            self.store.memories.deliver_pending()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_versions').fetchone()[0], before)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_relations').fetchone()[0], 1)

    def test_real_filesystem_failure_persists_and_retry_completes_portable_history(self):
        cid = self.propose()
        folder = self.store.output / 'memories'; saved = self.root / 'saved-history'
        folder.rename(saved); folder.write_text('synthetic directory blocker')
        with self.assertRaises(OSError): self.update(cid, state='confirmed')
        failed = self.store.memories.delivery_status()
        self.assertEqual(failed['state'], 'failed')
        self.assertNotIn(str(self.root), failed['problem'])
        self.restart()
        self.assertEqual(self.store.memories.delivery_status()['state'], 'failed')
        self.assertEqual(self.store.memories.deliver_pending()['state'], 'failed')
        folder.rename(self.root / 'preserved-blocker'); saved.rename(folder)
        self.assertEqual(self.store.memories.deliver_pending()['state'], 'ready')
        portable = json.loads((folder / (cid + '.json')).read_text())
        self.assertEqual([v['document']['state'] for v in portable['versions']], ['pending', 'confirmed'])
        self.assertEqual(len(self.store.candidates()), 1)

    def test_manual_memory_and_history_survive_restart_recovery_and_explicit_retry(self):
        cid = self.propose(); self.update(cid, state='confirmed')
        human = {'memory.md': 'Human memory edits.', 'memories/' + cid + '.json': 'Human history edits.'}
        for relative, text in human.items(): (self.store.output / relative).write_text(text)
        with patch.object(self.store, 'export_memory', side_effect=OSError('crash')):
            with self.assertRaises(OSError): self.update(cid, text='A revised synthetic memory.')
        self.restart()
        result = self.store.memories.deliver_pending()
        self.assertEqual(result['state'], 'review'); self.assertEqual(len(result['conflicts']), 2)
        for conflict in result['conflicts']:
            self.assertEqual((self.store.output / conflict['path']).read_text(), human[conflict['path']])
            self.assertIn('A revised synthetic memory.', (self.store.output / conflict['generatedPath']).read_text())
        paths = [c['generatedPath'] for c in result['conflicts']]
        with patch.object(self.store, 'export_memory', side_effect=AssertionError('review is not an automatic retry')):
            self.assertEqual(self.store.memories.deliver_pending()['state'], 'review')
        self.assertEqual([c['generatedPath'] for c in self.store.memories.deliver_pending(force=True)['conflicts']], paths)

    def test_rolled_back_change_does_not_leave_delivery_pending(self):
        new, old = self.pair()
        with patch.object(self.store.catalog, 'index_memory', side_effect=OSError('rollback')):
            with self.assertRaises(OSError): self.store.memories.resolve(new, self.values(new, old))
        self.assertEqual(self.store.memories.delivery_status()['state'], 'ready')
        self.assertEqual(self.store.memories.detail(new)['state'], 'pending')
        self.assertEqual(self.store.memories.detail(old)['state'], 'confirmed')

    def test_organization_and_category_changes_mark_dirty_before_other_file_writes(self):
        cid = self.propose(); self.update(cid, state='confirmed')
        with patch.object(self.store, 'export', side_effect=OSError('organization file blocked')):
            with self.assertRaises(OSError): self.store.catalog.update('memory', cid, {'revision': 0, 'archived': True})
        self.restart(); self.assertEqual(self.store.memories.deliver_pending()['state'], 'ready')
        self.assertNotIn(cid, self.store.read_document('memory.md')['text'])
        self.assertIn(cid, self.store.read_document('memory-archive.md')['text'])
        category = self.store.catalog.categories('memory')[0]
        with patch.object(self.store.catalog, 'export_categories', side_effect=OSError('categories file blocked')):
            with self.assertRaises(OSError): self.store.catalog.save_category('memory', 'Synthetic renamed', category['id'], category['revision'])
        self.assertEqual(self.store.memories.delivery_status()['state'], 'pending')
        self.assertEqual(self.store.memories.deliver_pending()['state'], 'ready')

    def test_output_location_change_cannot_reuse_old_success(self):
        cid = self.propose(); self.update(cid, state='confirmed')
        other = self.root / 'another-library'
        self.store.close(); self.store = Store(self.root / 'node', other)
        self.assertEqual(self.store.memories.delivery_status()['state'], 'pending')
        self.assertIsNone(self.store.memories.delivery_status()['lastSuccessAt'])
        self.assertEqual(self.store.memories.deliver_pending()['state'], 'ready')
        self.assertIn(cid, (other / 'memory.md').read_text())
        self.assertTrue((other / 'memories' / (cid + '.json')).is_file())

    def test_upgrade_without_delivery_row_and_legacy_history_recover(self):
        cid = self.propose()
        with self.store.db:
            self.store.db.execute('DROP TABLE memory_delivery')
            self.store.db.execute('DELETE FROM memory_details')
            self.store.db.execute('DELETE FROM memory_versions')
        with patch.object(Store, '_export_memory', side_effect=OSError('legacy folder blocked')):
            self.restart()
        self.assertEqual(self.store.memories.delivery_status()['state'], 'failed')
        self.assertEqual(self.store.memories.deliver_pending()['state'], 'ready')
        self.assertEqual(self.store.memories.history(cid)['items'][0]['action'], 'migrated')

    def test_file_failure_does_not_persist_raw_exception_and_other_conflicts_are_excluded(self):
        cid = self.propose()
        with patch.object(self.store, '_export_memory', side_effect=OSError('sensitive fake /private/source sk-not-a-real-key')):
            with self.assertRaises(OSError): self.update(cid, state='confirmed')
        raw = str(dict(self.store.db.execute('SELECT * FROM memory_delivery').fetchone()))
        self.assertNotIn('sensitive', raw); self.assertNotIn('sk-not', raw)
        self.store.export('other.md', 'before', 'one')
        (self.store.output / 'other.md').write_text('Human edits')
        self.store.export('other.md', 'after', 'two')
        self.assertEqual(self.store.memories.deliver_pending()['conflicts'], [])

    def test_status_and_retry_require_device_token_and_no_model_work(self):
        self.propose()
        grant = self.store.knowledge.create_grant({'name': 'Synthetic reader', 'scopes': ['confirmedMemory'], 'days': 1})
        server = Server(('127.0.0.1', 0), self.store, 'synthetic-delivery-token')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            for method, path in [('GET', '/v1/memory-delivery'), ('POST', '/v1/memory-delivery/retry')]:
                for token, expected in [(grant['token'], 401), (server.token, 200)]:
                    client = http.client.HTTPConnection(*server.server_address, timeout=5)
                    client.request(method, path, body='{}' if method=='POST' else None,
                                   headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
                    response = client.getresponse(); data = json.loads(response.read()); client.close()
                    self.assertEqual(response.status, expected, data)
                    if expected == 200: self.assertEqual(data['state'], 'ready')
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_versions').fetchone()[0], 1)
            self.assertEqual(self.store.status()['memoryDelivery']['state'], 'ready')
        finally: server.shutdown(); server.server_close(); worker.join()

    def test_paused_revision_survives_restart_without_writing_until_explicit_resume(self):
        cid = self.propose(); self.update(cid, state='confirmed')
        human = 'Human file retained while the restored library is reviewed.\n'
        (self.store.output/'memory.md').write_text(human)
        before = {p.relative_to(self.store.output):p.read_bytes() for p in self.store.output.rglob('*') if p.is_file()}
        attempt = self.store.memories.delivery_status()['lastAttemptAt']
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO settings VALUES ('memoryDeliveryPaused','true')")
        with patch.object(self.store, '_export_memory', side_effect=AssertionError('paused files must not be written')):
            self.update(cid, text='A revision saved while file updates are paused.')
            self.store.memories.deliver_pending()
        status = self.store.memories.delivery_status()
        self.assertTrue(status['paused']); self.assertEqual(status['state'], 'pending')
        self.assertIsNone(status['problem']); self.assertEqual(status['lastAttemptAt'], attempt)
        self.restart(); self.assertTrue(self.store.memories.deliver_pending()['paused'])
        after = {p.relative_to(self.store.output):p.read_bytes() for p in self.store.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        versions = self.store.memories.history(cid)
        result = self.store.memories.deliver_pending(force=True)
        self.assertFalse(result['paused']); self.assertEqual(result['state'], 'review')
        self.assertEqual((self.store.output/'memory.md').read_text(), human)
        self.assertIn('A revision saved while file updates are paused.', (self.store.output/result['conflicts'][0]['generatedPath']).read_text())
        self.assertEqual(self.store.memories.history(cid), versions)

    def test_legacy_pause_notice_is_not_an_error_but_real_resume_failure_is(self):
        self.propose()
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO settings VALUES ('memoryDeliveryPaused','true')")
            self.store.db.execute('UPDATE memory_delivery SET pending=1,problem=?', ('从备份恢复后，请核对并重新写入记忆文件。',))
        status = self.store.memories.delivery_status()
        self.assertTrue(status['paused']); self.assertEqual(status['state'], 'pending'); self.assertIsNone(status['problem'])
        with patch.object(self.store, '_export_memory', side_effect=OSError('synthetic unavailable folder')):
            failed = self.store.memories.deliver_pending(force=True)
        self.assertFalse(failed['paused']); self.assertEqual(failed['state'], 'failed')
        self.assertIsNotNone(failed['problem'])
        with self.store.db:
            self.store.db.execute("UPDATE settings SET value='true' WHERE key='memoryDeliveryPaused'")
        paused_failure = self.store.memories.delivery_status()
        self.assertTrue(paused_failure['paused']); self.assertEqual(paused_failure['state'], 'failed')
        self.assertEqual(paused_failure['problem'], failed['problem'])
        self.assertEqual(self.store.memories.deliver_pending(force=True)['state'], 'ready')

    def test_read_only_status_does_not_resume_and_explicit_retry_does(self):
        self.propose()
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO settings VALUES ('memoryDeliveryPaused','true')")
            self.store.memories.mark_delivery_pending()
        server = Server(('127.0.0.1',0), self.store, 'synthetic-pause-token')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            for method,path,paused in [('GET','/v1/memory-delivery',True),('POST','/v1/memory-delivery/retry',False)]:
                client = http.client.HTTPConnection(*server.server_address,timeout=5)
                client.request(method,path,body='{}' if method=='POST' else None,
                               headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'})
                response=client.getresponse();data=json.loads(response.read());client.close()
                self.assertEqual(response.status,200);self.assertEqual(data['paused'],paused)
                self.assertEqual(data['state'],'pending' if paused else 'ready')
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_versions').fetchone()[0],1)
            for table in ('speech_jobs','memory_extractions','memory_batches','answer_jobs','memory_reviews'):
                self.assertEqual(self.store.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        finally:server.shutdown();server.server_close();worker.join()

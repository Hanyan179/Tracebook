import http.client
import json
import threading
import unittest
import uuid
from unittest.mock import patch
import test_memories as fixtures
from store import Store, StoreError
from server import Server


class MemoryFileTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    tearDown = fixtures.MemoryTests.tearDown
    note = fixtures.MemoryTests.note
    reference = fixtures.MemoryTests.reference
    propose = fixtures.MemoryTests.propose
    update = fixtures.MemoryTests.update

    def conflict(self, history=False):
        cid = self.propose(); self.update(cid, state='confirmed')
        path = 'memories/' + cid + '.json' if history else 'memory.md'
        human = 'Synthetic human edits that must survive.\n'
        (self.store.output / path).write_text(human)
        self.update(cid, text='Synthetic revised memory.')
        return cid, path, human

    def decision(self, path, action='generated'):
        pair = self.store.memory_files.preview(path)
        return {'path': path, 'snapshot': pair['snapshot'], 'action': action, 'requestID': str(uuid.uuid4())}

    def restart(self):
        self.store.close(); self.store = Store(self.root / 'node')

    def test_keep_preserves_manual_file_and_only_suppresses_this_generation(self):
        cid, path, human = self.conflict(); data = self.decision(path, 'keep')
        before = self.store.memories.history(cid)
        self.store.memory_files.resolve(data)
        for _ in range(2): self.store.memories.deliver_pending(force=True)
        self.assertEqual((self.store.output/path).read_text(), human)
        self.assertEqual(self.store.memories.delivery_status()['state'], 'reviewed')
        self.assertEqual(self.store.memories.history(cid), before)
        self.restart(); self.assertEqual(self.store.memory_files.resolve(data)['action'], 'keep')
        self.update(cid, text='A newer synthetic fact.')
        self.assertEqual((self.store.output/path).read_text(), human)
        self.assertEqual(self.store.memories.delivery_status()['state'], 'review')
        self.assertIn('A newer synthetic fact.', self.store.memory_files.preview(path)['generated']['text'])

    def test_adopt_backs_up_original_keeps_generated_and_does_not_change_facts(self):
        cid, path, human = self.conflict(); pair = self.store.memory_files.preview(path); data = self.decision(path)
        before = self.store.memories.history(cid)
        result = self.store.memory_files.resolve(data)
        self.assertEqual((self.store.output/result['backupPath']).read_text(), human)
        self.assertEqual((self.store.output/path).read_text(), pair['generated']['text'])
        self.assertTrue((self.store.output/pair['generated']['path']).is_file())
        self.assertEqual(self.store.memories.history(cid), before)
        self.assertEqual(self.store.memories.delivery_status()['state'], 'ready')
        self.restart(); self.assertEqual(self.store.memory_files.resolve(data), result)
        self.assertEqual(len(list((self.store.output/'.history'/path).glob('review-*'))), 1)
        with self.assertRaises(StoreError): self.store.memory_files.resolve({**data, 'action': 'keep'})

    def test_history_json_can_be_compared_and_restored_without_rewriting_db_history(self):
        cid, path, human = self.conflict(history=True)
        result = self.store.memory_files.resolve(self.decision(path))
        self.assertEqual((self.store.output/result['backupPath']).read_text(), human)
        history = json.loads((self.store.output/path).read_text())
        self.assertEqual(len(history['versions']), 3)
        self.assertEqual(len(self.store.memories.history(cid)['items']), 3)

    def test_changed_original_generated_or_memory_after_preview_blocks_write(self):
        cid, path, human = self.conflict(); data = self.decision(path)
        (self.store.output/path).write_text('A concurrent human edit.')
        with self.assertRaises(StoreError) as error: self.store.memory_files.resolve(data)
        self.assertEqual(error.exception.status, 409)
        data = self.decision(path)
        self.update(cid, text='Another newer memory.')
        with self.assertRaises(StoreError): self.store.memory_files.resolve(data)
        self.assertEqual((self.store.output/path).read_text(), 'A concurrent human edit.')
        data = self.decision(path); pair = self.store.memory_files.preview(path)
        (self.store.output/pair['generated']['path']).write_text('Someone edited the generated copy.')
        with self.assertRaises(StoreError): self.store.memory_files.resolve(data)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_file_operations').fetchone()[0], 0)

    def test_failure_before_destination_write_recovers_without_duplicate_backup(self):
        _, path, human = self.conflict(); data = self.decision(path); atomic = self.store._atomic
        def fail(target, raw):
            if target == self.store.output/path: raise OSError('synthetic failure')
            return atomic(target, raw)
        with patch.object(self.store, '_atomic', side_effect=fail):
            with self.assertRaises(OSError): self.store.memory_files.resolve(data)
            self.assertEqual(self.store.memories.deliver_pending()['state'], 'failed')
        self.assertEqual((self.store.output/path).read_text(), human)
        self.restart(); self.store.memories.deliver_pending()
        self.assertEqual(self.store.memories.delivery_status()['state'], 'ready')
        receipt = self.store.memory_files.resolve(data)
        self.assertEqual((self.store.output/receipt['backupPath']).read_text(), human)

    def test_crash_after_file_write_before_receipt_recovers_without_reapplying(self):
        _, path, human = self.conflict(); data = self.decision(path)
        with patch.object(self.store.memory_files, 'finish', side_effect=OSError('crash before receipt')):
            with self.assertRaises(OSError): self.store.memory_files.resolve(data)
        self.restart(); self.store.memories.deliver_pending()
        self.assertEqual(self.store.memories.delivery_status()['state'], 'ready')
        receipt = self.store.memory_files.resolve(data)
        self.assertEqual((self.store.output/receipt['backupPath']).read_text(), human)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memory_file_operations WHERE state='done'").fetchone()[0], 1)

    def test_external_edit_during_backup_blocks_replacement_and_is_preserved(self):
        _, path, human = self.conflict(); data = self.decision(path); atomic = self.store._atomic
        def edit_after_backup(target, raw):
            result = atomic(target, raw)
            if 'review-' in target.name: (self.store.output/path).write_text('New edit during backup.')
            return result
        with patch.object(self.store, '_atomic', side_effect=edit_after_backup):
            with self.assertRaises(StoreError): self.store.memory_files.resolve(data)
        self.assertEqual((self.store.output/path).read_text(), 'New edit during backup.')
        self.restart(); self.store.memories.deliver_pending()
        self.assertEqual((self.store.output/path).read_text(), 'New edit during backup.')
        self.assertEqual(self.store.db.execute('SELECT state FROM memory_file_operations').fetchone()[0], 'blocked')

    def test_preview_rejects_paths_links_large_or_missing_files_and_pending_generation(self):
        _, path, human = self.conflict()
        for bad in ['../connection-token', 'prompts/memory-extraction.md', '.generated/memory.md/fake.md', '/memory.md', None]:
            with self.assertRaises(StoreError): self.store.memory_files.preview(bad)
        target = self.store.output/path; target.write_text('x'*(128*1024+1))
        with self.assertRaises(StoreError) as error: self.store.memory_files.preview(path)
        self.assertEqual(error.exception.status, 413)
        target.unlink();target.symlink_to(self.store.output/'memory-inbox.md')
        with self.assertRaises(StoreError): self.store.memory_files.preview(path)
        target.unlink()
        with self.assertRaises(StoreError): self.store.memory_files.preview(path)
        target.write_text(human)
        with self.store.db:self.store.memories.mark_delivery_pending()
        with self.assertRaises(StoreError): self.store.memory_files.preview(path)

    def test_device_routes_do_not_expand_read_only_grants(self):
        _, path, _ = self.conflict(); decision = self.decision(path, 'keep')
        grant = self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        server = Server(('127.0.0.1',0),self.store,'synthetic-file-token')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for method, route, body in [('GET','/v1/memory-files/preview?path=memory.md',None),('POST','/v1/memory-files/resolve',json.dumps(decision))]:
                for token, expected in [(grant['token'],401),(server.token,200)]:
                    client=http.client.HTTPConnection(*server.server_address,timeout=5)
                    client.request(method,route,body=body,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    response=client.getresponse();value=json.loads(response.read());client.close()
                    self.assertEqual(response.status,expected,value)
        finally:server.shutdown();server.server_close();worker.join()

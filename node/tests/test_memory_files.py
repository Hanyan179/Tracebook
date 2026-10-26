import http.client
import json
import hashlib
import os
import threading
import unittest
import uuid
from unittest.mock import patch
import test_memories as fixtures
from store import Store, StoreError
from server import Server
from backup import create_backup, inspect_backup, restore_backup


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

    def merge(self, path, text):
        return {**self.decision(path,'merge'), 'nodeID':self.store.setting('nodeID'), 'text':text}

    def test_merge_preserves_facts_both_versions_and_later_human_protection(self):
        cid,path,human=self.conflict();pair=self.store.memory_files.preview(path)
        before=self.store.memories.history(cid);text=pair['generated']['text']+'\n'+human
        decision=self.merge(path,text);receipt=self.store.memory_files.resolve(decision)
        self.assertEqual((self.store.output/path).read_text(),text)
        self.assertEqual((self.store.output/receipt['backupPath']).read_text(),human)
        self.assertEqual((self.store.output/receipt['generatedPath']).read_text(),pair['generated']['text'])
        self.assertEqual(receipt['contentVersion'],hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(self.store.memories.history(cid),before)
        for _ in range(2):self.store.memories.deliver_pending(force=True)
        self.assertEqual((self.store.output/path).read_text(),text)
        self.assertEqual(self.store.memories.delivery_status()['state'],'reviewed')
        self.restart();self.assertEqual(self.store.memory_files.resolve(decision),receipt)
        self.assertTrue(self.store.memory_files.preview(path)['lastDecision']['matchesCurrent'])
        self.update(cid,text='A later factual revision, not a file edit.')
        next_pair=self.store.memory_files.preview(path)
        self.assertFalse(next_pair['resolved']);self.assertEqual(next_pair['original']['text'],text)
        self.assertIn('A later factual revision',next_pair['generated']['text'])

    def test_non_memory_reviewed_exports_do_not_appear_as_memory_file_reviews(self):
        cid=self.propose();self.update(cid,state='confirmed')
        with self.store.db:
            self.store.db.execute('INSERT INTO exports VALUES (?,?,?,?,?,?,?)',
                (str(self.store.output),'transcripts/'+str(uuid.uuid4()).upper()+'.md','hash','semantic','reviewed',None,'2026-10-26T00:00:00Z'))
        self.assertEqual(self.store.memories.delivery_status()['reviewedFiles'],[])
        self.assertEqual(self.store.memories.delivery_status()['state'],'ready')

    def test_merge_request_identity_and_library_binding_cannot_be_reused_for_different_text(self):
        _,path,human=self.conflict();decision=self.merge(path,human+'Merged note.')
        with self.assertRaises(StoreError):self.store.memory_files.resolve({**decision,'nodeID':str(uuid.uuid4())})
        with self.assertRaises(StoreError):self.store.memory_files.resolve({k:v for k,v in decision.items() if k!='nodeID'})
        receipt=self.store.memory_files.resolve(decision)
        with self.assertRaises(StoreError):self.store.memory_files.resolve({**decision,'text':'Different content.'})
        self.assertEqual(self.store.memory_files.resolve(decision),receipt)
        (self.store.output/path).write_text('Later external file edit.')
        preview=self.store.memory_files.preview(path)
        self.assertTrue(preview['resolved']);self.assertFalse(preview['lastDecision']['matchesCurrent'])
        self.assertEqual(self.store.memory_files.resolve(decision),receipt)
        self.assertEqual((self.store.output/path).read_text(),'Later external file edit.')

    def test_merge_rejects_changed_preview_without_losing_original_or_text(self):
        _,path,human=self.conflict();decision=self.merge(path,'Unsubmitted merged text.')
        (self.store.output/path).write_text('New human edit after preview.')
        with self.assertRaises(StoreError) as stale:self.store.memory_files.resolve(decision)
        self.assertEqual(stale.exception.status,409)
        self.assertEqual((self.store.output/path).read_text(),'New human edit after preview.')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_file_operations').fetchone()[0],0)

    def test_merge_payload_is_bounded_by_utf8_bytes_and_json_history_stays_an_object(self):
        _,path,_=self.conflict();decision=self.merge(path,'valid')
        for bad in [None,[],42,'\0','\ud800','中'*(self.store.memory_files.MAX_BYTES//3+1)]:
            with self.subTest(value=type(bad)):
                with self.assertRaises(StoreError) as error:self.store.memory_files.resolve({**decision,'text':bad})
                self.assertEqual(error.exception.status,400)
        with self.assertRaises(StoreError):self.store.memory_files.resolve({**self.decision(path,'keep'),'text':'Not allowed'})
        cid,path,human=self.conflict(history=True);pair=self.store.memory_files.preview(path)
        for bad in ['not json','[]','{"number": NaN}']:
            with self.assertRaises(StoreError):self.store.memory_files.resolve(self.merge(path,bad))
        history=self.store.memories.history(cid);value=json.loads(pair['generated']['text']);value['personalAnnotation']='Synthetic file-only annotation.'
        receipt=self.store.memory_files.resolve(self.merge(path,json.dumps(value,ensure_ascii=False)))
        self.assertEqual(json.loads((self.store.output/path).read_text()),value)
        self.assertEqual(self.store.memories.history(cid),history)
        self.assertEqual((self.store.output/receipt['backupPath']).read_text(),human)

    def test_pending_merge_retry_after_delivery_failure_uses_saved_text_and_one_backup(self):
        _,path,human=self.conflict();decision=self.merge(path,human+'Pending merge.')
        atomic=self.store._atomic
        def fail(target,raw):
            if target==self.store.output/path:raise OSError('Synthetic failed destination')
            return atomic(target,raw)
        with patch.object(self.store,'_atomic',side_effect=fail):
            with self.assertRaises(OSError):self.store.memory_files.resolve(decision)
        preview=self.store.memory_files.preview(path);pending=preview['pendingOperation']
        self.assertEqual(pending['requestID'],decision['requestID'].upper())
        with self.assertRaises(StoreError):self.store.memory_files.resolve(self.merge(path,'Another operation.'))
        retry={'requestID':pending['requestID'],'path':path,'nodeID':preview['nodeID']}
        with self.assertRaises(StoreError):self.store.memory_files.retry({**retry,'path':'memory-inbox.md'})
        self.restart();receipt=self.store.memory_files.retry(retry)
        self.assertEqual((self.store.output/path).read_text(),decision['text'])
        self.assertEqual((self.store.output/receipt['backupPath']).read_text(),human)
        self.assertEqual(len(list((self.store.output/'.history'/path).glob('review-*'))),1)

    def test_merge_after_write_before_receipt_recovers_and_never_replaces_external_edit(self):
        _,path,human=self.conflict();decision=self.merge(path,'Merged before interrupted receipt.')
        with patch.object(self.store.memory_files,'finish',side_effect=OSError('Synthetic lost receipt')):
            with self.assertRaises(OSError):self.store.memory_files.resolve(decision)
        self.restart();self.store.memories.deliver_pending()
        self.assertEqual(self.store.memory_files.resolve(decision)['action'],'merge')
        self.assertEqual((self.store.output/path).read_text(),decision['text'])
        # A separate conflict interrupted at the same point but edited before recovery.
        _,path,human=self.conflict();next_decision=self.merge(path,'Second interrupted merge.')
        with patch.object(self.store.memory_files,'finish',side_effect=OSError('Synthetic lost receipt')):
            with self.assertRaises(OSError):self.store.memory_files.resolve(next_decision)
        (self.store.output/path).write_text('Keep this newer human edit.')
        self.restart();self.store.memories.deliver_pending()
        self.assertEqual((self.store.output/path).read_text(),'Keep this newer human edit.')
        with self.assertRaises(StoreError):self.store.memory_files.resolve(next_decision)

    def test_lost_archive_during_merge_remains_retryable_without_creating_a_replacement(self):
        _,path,human=self.conflict();decision=self.merge(path,'Recovered merged file.')
        root=self.store.output;held=root.parent/'held-archive';atomic=self.store._atomic
        def detach(target,raw):
            result=atomic(target,raw)
            if target.name.startswith('review-'):root.rename(held)
            return result
        with patch.object(self.store,'_atomic',side_effect=detach):
            with self.assertRaises(StoreError) as error:self.store.memory_files.resolve(decision)
        self.assertEqual(error.exception.status,503);self.assertFalse(root.exists())
        self.assertEqual(self.store.db.execute('SELECT state FROM memory_file_operations').fetchone()[0],'pending')
        held.rename(root);self.store.memories.deliver_pending()
        self.assertEqual((root/path).read_text(),decision['text'])
        self.assertEqual((root/self.store.memory_files.resolve(decision)['backupPath']).read_text(),human)

    def test_changed_parent_during_read_and_non_regular_files_are_not_accepted(self):
        cid,path,human=self.conflict(history=True);parent=self.store.output/'memories';held=self.store.output/'held-memories'
        real_open=os.open;swapped=False
        def swap(name,*args,**kwargs):
            nonlocal swapped
            fd=real_open(name,*args,**kwargs)
            if name==cid+'.json' and not swapped:
                swapped=True;parent.rename(held);parent.mkdir()
                (parent/(cid+'.json')).write_text('Replaced content.')
            return fd
        with patch('memory_files.os.open',side_effect=swap):
            with self.assertRaises(StoreError):self.store.memory_files.preview(path)
        self.assertEqual((held/(cid+'.json')).read_text(),human)
        fifo=self.store.output/'memory.md';fifo.unlink();os.mkfifo(fifo)
        with self.assertRaises(StoreError):self.store.memory_files.read('memory.md')

    def test_backup_restores_merged_file_and_receipt_but_does_not_replay_pending_work(self):
        cid,path,human=self.conflict();decision=self.merge(path,human+'Merged portable text.')
        receipt=self.store.memory_files.resolve(decision);before=self.store.memories.history(cid)
        pending_cid=self.propose();pending_path='memory-inbox.md';pending_human='Unfinished original file.'
        (self.store.output/pending_path).write_text(pending_human)
        self.update(pending_cid,text='Changed pending proposal.')
        pending=self.merge(pending_path,'Do not replay this restored merge.')
        atomic=self.store._atomic
        def fail(target,raw):
            if target==self.store.output/pending_path:raise OSError('Synthetic save failure')
            return atomic(target,raw)
        with patch.object(self.store,'_atomic',side_effect=fail):
            with self.assertRaises(OSError):self.store.memory_files.resolve(pending)
        self.store.close();archive=self.root/'merged.zip';create_backup(self.root/'node',archive)
        checked=inspect_backup(archive,self.root);target=self.root/'restored'
        restore_backup(archive,target,checked['archiveSHA256'],self.root);restored=Store(target)
        try:
            self.assertEqual((restored.output/path).read_text(),decision['text'])
            self.assertEqual((restored.output/receipt['backupPath']).read_text(),human)
            self.assertEqual(restored.memories.history(cid),before)
            row=restored.db.execute('SELECT * FROM memory_file_operations WHERE id=?',(receipt['requestID'],)).fetchone()
            self.assertEqual(restored.memory_files.receipt(row),receipt)
            changes=restored.db.total_changes
            viewed=restored.memory_files.preview(path)
            self.assertTrue(viewed['resolved']);self.assertTrue(viewed['deliveryPending'])
            self.assertEqual(restored.db.total_changes,changes)
            self.assertEqual(restored.db.execute('SELECT state FROM memory_file_operations WHERE id=?',(pending['requestID'].upper(),)).fetchone()[0],'blocked')
            self.assertEqual((restored.output/pending_path).read_text(),pending_human)
            self.assertEqual(restored.setting('memoryDeliveryPaused'),'true')
        finally:restored.close()
        self.store=Store(self.root/'node')

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
            retry={'nodeID':self.store.setting('nodeID'),'path':path,'requestID':decision['requestID']}
            for method, route, body in [('GET','/v1/memory-files/preview?path=memory.md',None),('POST','/v1/memory-files/resolve',json.dumps(decision)),('POST','/v1/memory-files/retry',json.dumps(retry))]:
                for token, expected in [(grant['token'],401),(server.token,200)]:
                    client=http.client.HTTPConnection(*server.server_address,timeout=5)
                    client.request(method,route,body=body,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    response=client.getresponse();value=json.loads(response.read());client.close()
                    self.assertEqual(response.status,expected,value)
        finally:server.shutdown();server.server_close();worker.join()

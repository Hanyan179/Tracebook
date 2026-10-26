import hashlib
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch

import test_memories as fixtures
import test_document_originals as originals
from archive_copy import publish_file, read_checked
from backup import create_backup, restore_backup
from library_location import inspect_library
from server import Server
from store import Store, StoreError


class ArchiveCopyTests(unittest.TestCase):
    setUp=fixtures.MemoryTests.setUp
    tearDown=fixtures.MemoryTests.tearDown
    note=fixtures.MemoryTests.note
    reference=fixtures.MemoryTests.reference
    propose=fixtures.MemoryTests.propose
    update=fixtures.MemoryTests.update
    item=originals.DocumentOriginalTests.item
    save=originals.DocumentOriginalTests.save
    original_path=originals.DocumentOriginalTests.original_path

    def files(self, output):
        return {str(f.relative_to(output)):hashlib.sha256(f.read_bytes()).hexdigest() for f in output.rglob('*') if f.is_file()}

    def prepare(self):
        with self.store.db:self.store.db.execute("UPDATE settings SET value='false' WHERE key='dailyEnabled'")
        note=self.note('Synthetic first copy version.')
        self.store.knowledge.save_note({'title':note['title'],'text':'Synthetic second copy version.','etag':note['etag']},note['id'])
        note=self.store.knowledge.note(note['id']);cid=self.propose(note);self.update(cid,state='confirmed')
        (self.store.output/'memory.md').write_text('Synthetic manual memory file.')
        self.update(cid,text='Synthetic revised memory.')
        item,_=self.item();_,_,doc=self.save(item)
        original=self.original_path(doc);original.write_bytes(original.read_bytes()+b'Synthetic manual original change')
        return note,cid,doc

    def request(self, output):
        return {'outputDirectory':str(output),'archiveCopyID':str(uuid.uuid4()).upper(),
                'archiveSourceNodeID':self.store.setting('nodeID'),'archiveSourceDirectory':str(self.store.output)}

    def test_all_histories_generated_versions_and_changed_originals_survive_copy_and_backup(self):
        note,cid,doc=self.prepare();old=self.store.output;before=self.files(old)
        versions=list(self.store.db.execute('SELECT * FROM memory_versions'));node_id=self.store.setting('nodeID')
        output=(self.root/'copied').resolve();request=self.request(output)
        with patch.object(self.store,'export_memory',side_effect=AssertionError('Copy must not regenerate memory')), patch.object(self.store,'catch_up',side_effect=AssertionError('Copy must not start automatic work')):
            receipt=self.store.configure(request)['archiveTransfer']
        self.assertEqual(self.files(old),before);self.assertEqual(self.files(output),before)
        self.assertEqual(receipt['totalFiles'],len(before));self.assertEqual(receipt['copiedFiles'],len(before))
        self.assertEqual(list(self.store.db.execute('SELECT * FROM memory_versions')),versions)
        self.assertEqual(self.store.setting('nodeID'),node_id)
        self.assertEqual(self.store.documents.listing({})['items'][0]['documentInfo']['originalCopy']['state'],'unavailable')
        pair=self.store.memory_files.preview('memory.md');self.assertEqual(pair['original']['text'],'Synthetic manual memory file.')
        self.store.memory_files.resolve({'path':'memory.md','snapshot':pair['snapshot'],'action':'keep','requestID':str(uuid.uuid4())})
        self.assertEqual(self.store.memories.delivery_status()['state'],'reviewed')
        self.assertEqual((old/'memory.md').read_text(),'Synthetic manual memory file.')
        selected=inspect_library(self.store.data);self.assertEqual(selected['archiveCopyReceipt']['id'],request['archiveCopyID'])
        self.store.close();archive=self.root/'copied.zip';result=create_backup(self.root/'node',archive)
        restored=self.root/'restored';restore_backup(archive,restored,result['archiveSHA256'],self.root)
        self.assertEqual(self.files(restored/'Library'),self.files(output))
        with zipfile.ZipFile(archive) as z:self.assertNotIn(b'archiveCopy',z.read('tables/settings.jsonl'))

    def test_identical_partial_copy_is_reused_and_unmanaged_files_stay_untouched(self):
        self.prepare();old=self.store.output;before=self.files(old);output=self.root/'partial';output.mkdir()
        first=next(iter(before));(output/first).parent.mkdir(parents=True,exist_ok=True);(output/first).write_bytes((old/first).read_bytes())
        (output/'personal.txt').write_text('Synthetic unmanaged target file.')
        r=self.store.configure(self.request(output))['archiveTransfer']
        self.assertEqual(r['reusedFiles'],1);self.assertEqual(r['copiedFiles'],len(before)-1)
        self.assertEqual((output/'personal.txt').read_text(),'Synthetic unmanaged target file.')
        self.assertTrue(all(self.files(output)[name]==value for name,value in before.items()))

    def test_conflicting_target_is_never_overwritten_or_activated(self):
        note,_,_=self.prepare();old=self.store.output;before=self.files(old);target=self.root/'conflict';target.mkdir()
        relative='notes/'+note['id']+'.md';(target/relative).parent.mkdir();(target/relative).write_text('Synthetic target edit.')
        with self.assertRaises(StoreError) as error:self.store.configure(self.request(target))
        self.assertEqual(error.exception.path,relative);self.assertEqual(error.exception.status,409)
        self.assertEqual(self.files(old),before);self.assertEqual(self.store.output,old)
        self.assertEqual(list(self.files(target)),[relative]);self.assertEqual((target/relative).read_text(),'Synthetic target edit.')

    def test_other_managed_target_files_are_not_silently_merged(self):
        self.prepare();target=self.root/'other';(target/'notes').mkdir(parents=True);(target/'notes/other.md').write_text('Synthetic other library.')
        with self.assertRaises(StoreError) as error:self.store.configure(self.request(target))
        self.assertEqual(error.exception.path,'notes/other.md')

    def test_interruption_keeps_source_active_and_retry_reuses_finished_files_after_restart(self):
        self.prepare();old=self.store.output;before=self.files(old);target=self.root/'retry';request=self.request(target);calls=[]
        def fail(source,destination,path,entry):
            if calls:raise OSError('Synthetic interrupted copy')
            result=publish_file(source,destination,path,entry);calls.append(path);return result
        with patch('archive_copy.publish_file',side_effect=fail),self.assertRaises(StoreError):self.store.configure(request)
        self.assertEqual(self.store.output,old);self.assertEqual(self.store.setting('outputDirectory'),str(old))
        self.assertEqual(len(self.files(target)),1);self.assertFalse(list(target.rglob('*.tmp')))
        self.store.close();self.store=Store(self.root/'node')
        r=self.store.configure(request)['archiveTransfer'];self.assertEqual(r['reusedFiles'],1)
        self.assertEqual(self.files(target),before);self.assertEqual(self.files(old),before)

    def test_database_failure_rolls_back_location_and_receipt_but_keeps_full_copies_for_retry(self):
        self.prepare();old=self.store.output;before=self.files(old);target=self.root/'db-retry';request=self.request(target)
        with self.store.db:self.store.db.execute("""CREATE TRIGGER fail_copy BEFORE INSERT ON settings WHEN NEW.key='archiveCopyReceipt'
            BEGIN SELECT RAISE(ABORT,'Synthetic receipt failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):self.store.configure(request)
        self.assertEqual(self.store.output,old);self.assertEqual(self.store.setting('outputDirectory'),str(old))
        self.assertFalse(self.store.db.execute('SELECT 1 FROM exports WHERE root=?',(str(target.resolve()),)).fetchone())
        self.assertEqual(self.files(target),before)
        with self.store.db:self.store.db.execute('DROP TRIGGER fail_copy')
        r=self.store.configure(request)['archiveTransfer'];self.assertEqual(r['copiedFiles'],0);self.assertEqual(r['reusedFiles'],len(before))

    def test_source_change_during_copy_leaves_both_versions_and_refuses_switch(self):
        note,_,_=self.prepare();old=self.store.output;relative='notes/'+note['id']+'.md';target=self.root/'changed'
        def changing(source,destination,path,entry):
            result=publish_file(source,destination,path,entry)
            if path==relative:(old/path).write_text('Synthetic edit during copy.')
            return result
        with patch('archive_copy.publish_file',side_effect=changing),self.assertRaises(StoreError):self.store.configure(self.request(target))
        self.assertEqual(self.store.output,old);self.assertEqual((old/relative).read_text(),'Synthetic edit during copy.')
        self.assertEqual((target/relative).read_text(),note['text'])
        with self.assertRaises(StoreError):self.store.configure(self.request(target))

    def test_concurrent_destination_creation_is_preserved_without_replacement(self):
        self.prepare();old=self.store.output;target=self.root/'raced';real_link=os.link;written=[]
        def racing(src,dst,*args,**kwargs):
            if not written:
                fd=os.open(dst,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=kwargs['dst_dir_fd'])
                os.write(fd,b'Synthetic concurrent target.');os.close(fd);written.append(dst)
            return real_link(src,dst,*args,**kwargs)
        with patch('archive_copy.os.link',side_effect=racing),self.assertRaises(StoreError):self.store.configure(self.request(target))
        self.assertEqual(self.store.output,old);self.assertFalse(list(target.rglob('*.tmp')))
        files=[f for f in target.rglob('*') if f.is_file()];self.assertEqual(len(files),1)
        self.assertEqual(files[0].read_bytes(),b'Synthetic concurrent target.')

    def test_growing_source_fails_without_publishing_partial_file(self):
        note,_,_=self.prepare();old=self.store.output;relative='notes/'+note['id']+'.md';target=self.root/'growth'
        def growing(parent,name,info,expected,writer=None,expected_size=None):
            if writer and name==note['id']+'.md':
                class GrowingWriter:
                    def write(self,chunk):
                        with (old/relative).open('ab') as f:f.write(b'growth')
                        writer.write(chunk)
                return read_checked(parent,name,info,expected,GrowingWriter())
            return read_checked(parent,name,info,expected,writer,expected_size)
        with patch('archive_copy.read_checked',side_effect=growing),self.assertRaises(StoreError):self.store.configure(self.request(target))
        self.assertFalse((target/relative).exists());self.assertFalse(list(target.rglob('*.tmp')));self.assertEqual(self.store.output,old)

    def test_failed_file_flush_removes_own_temporary_and_keeps_previous_location(self):
        import stat
        self.prepare();old=self.store.output;before=self.files(old);target=self.root/'failed-flush';real_sync=os.fsync
        def failing(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):raise OSError('Synthetic storage full')
            return real_sync(fd)
        with patch('archive_copy.os.fsync',side_effect=failing),self.assertRaises(StoreError):self.store.configure(self.request(target))
        self.assertEqual(self.store.output,old);self.assertEqual(self.files(old),before)
        self.assertFalse(self.files(target));self.assertFalse(list(target.rglob('*.tmp')))

    def test_short_temporary_write_is_detected_before_publication(self):
        self.prepare();old=self.store.output;target=self.root/'short-write'
        def short(parent,name,info,expected,writer=None,expected_size=None):
            if writer:
                class ShortWriter:
                    def write(self,chunk):writer.write(chunk[:-1])
                return read_checked(parent,name,info,expected,ShortWriter())
            return read_checked(parent,name,info,expected,writer,expected_size)
        with patch('archive_copy.read_checked',side_effect=short),self.assertRaises(StoreError):self.store.configure(self.request(target))
        self.assertEqual(self.store.output,old);self.assertFalse(self.files(target));self.assertFalse(list(target.rglob('*.tmp')))

    def test_invalid_or_wrong_library_request_does_not_create_destination(self):
        self.prepare();target=self.root/'invalid';base=self.request(target)
        for change in [{'outputDirectory':None},{'outputDirectory':'relative'}, {'outputDirectory':''},
                       {'archiveSourceNodeID':str(uuid.uuid4())},{'archiveSourceDirectory':'/incorrect'},
                       {'archiveCopyID':'invalid'}, {'timeZone':'No/SuchZone'}]:
            with self.subTest(change=change),self.assertRaises(StoreError):self.store.configure({**base,**change})
        self.assertFalse(target.exists())

    def test_offline_receipt_inspection_refuses_a_running_node(self):
        import fcntl
        self.prepare()
        command=[sys.executable,str(Path(__file__).resolve().parents[1]/'library_location_cli.py'),
                 'inspect','--data',str(self.store.data),'--offline']
        with (self.store.data/'instance.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            busy=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertEqual(busy.returncode,1);self.assertFalse(json.loads(busy.stdout)['ok'])
            self.assertIn('核对复制结果',json.loads(busy.stdout)['error'])
        available=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(available.returncode,0);self.assertTrue(json.loads(available.stdout)['ok'])

    def test_symlinks_fifo_and_nested_or_internal_destinations_are_rejected(self):
        note,_,_=self.prepare();old=self.store.output;outside=self.root/'outside';outside.mkdir()
        linked=self.root/'linked';linked.symlink_to(outside,target_is_directory=True)
        for target in [linked,old/'child',old.parent,self.store.staging/'text',self.store.recordings/'text']:
            with self.assertRaises(StoreError):self.store.configure(self.request(target))
        target=self.root/'linked-child';target.mkdir();(target/'notes').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(StoreError):self.store.configure(self.request(target))
        fifo=old/'notes/pipe.md';os.mkfifo(fifo)
        with self.assertRaises(StoreError):self.store.configure(self.request(self.root/'fifo-target'))
        self.assertFalse(list(outside.iterdir()));self.assertEqual(self.store.output,old)

    def test_missing_registered_file_is_identified_before_target_creation(self):
        note,_,_=self.prepare();relative='notes/'+note['id']+'.md';(self.store.output/relative).rename(self.root/'held-note')
        target=self.root/'incomplete'
        with self.assertRaises(StoreError) as error:self.store.configure(self.request(target))
        self.assertEqual(error.exception.path,relative);self.assertFalse(target.exists())

    def test_receipts_are_idempotent_and_stale_requests_cannot_revert_later_choices(self):
        self.prepare();first=self.root/'first';request=self.request(first);r=self.store.configure(request)['archiveTransfer']
        with patch('archive_copy.publish_file',side_effect=AssertionError('Completed request must not copy again')):
            self.assertEqual(self.store.configure(request)['archiveTransfer'],r)
        wrong={**request,'outputDirectory':str(self.root/'wrong')}
        with self.assertRaises(StoreError):self.store.configure(wrong)
        second=self.root/'second';self.store.configure(self.request(second))
        with self.assertRaises(StoreError):self.store.configure(request)
        self.assertEqual(self.store.output,second.resolve())
        stale={**self.request(self.root/'stale'),'archiveSourceDirectory':str(first)}
        with self.assertRaises(StoreError):self.store.configure(stale)
        self.assertFalse((self.root/'stale').exists())

    def test_unmanaged_credentials_are_not_copied_and_receipt_preview_is_filtered(self):
        self.prepare();source=self.store.output;(source/'connection-token').write_text('Synthetic private marker')
        target=self.root/'safe';request=self.request(target);self.store.configure(request)
        self.assertFalse((target/'connection-token').exists());self.assertTrue((source/'connection-token').exists())
        saved=json.loads(self.store.setting('archiveCopyReceipt'));saved['secret']='Synthetic marker not for preview'
        with self.store.db:self.store.db.execute("UPDATE settings SET value=? WHERE key='archiveCopyReceipt'",(json.dumps(saved),))
        preview=inspect_library(self.store.data);self.assertNotIn('secret',json.dumps(preview));self.assertNotIn('Synthetic marker',json.dumps(preview))
        saved['id']=123
        with self.store.db:self.store.db.execute("UPDATE settings SET value=? WHERE key='archiveCopyReceipt'",(json.dumps(saved),))
        self.assertIsNone(inspect_library(self.store.data)['archiveCopyReceipt'])

    def test_http_requires_owner_loopback_and_returns_relative_conflict_path(self):
        self.prepare();grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        target=self.root/'http-target';target.mkdir();(target/'memory.md').write_text('Synthetic target content.')
        server=Server(('127.0.0.1',0),self.store,'synthetic-copy-token');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def put(token):
            c=http.client.HTTPConnection(*server.server_address,timeout=5)
            c.request('PUT','/v1/settings',body=json.dumps(self.request(target)),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            r=c.getresponse();body=json.loads(r.read());c.close();return r.status,body
        try:
            self.assertEqual(put('wrong')[0],401)
            self.assertEqual(put(grant['token'])[0],401)
            status,body=put('synthetic-copy-token');self.assertEqual(status,409);self.assertEqual(body['path'],'memory.md')
            with patch('server.ipaddress.ip_address') as address:
                address.return_value.is_loopback=False
                self.assertEqual(put('synthetic-copy-token')[0],403)
        finally:server.shutdown();server.server_close();thread.join()

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import test_memories as fixtures
import test_document_originals as originals
from backup import BackupError, create_backup, inspect_backup, restore_backup
from library_location import inspect_library, prepare_library
from store import Store


class LibraryRelocationTests(unittest.TestCase):
    setUp=fixtures.MemoryTests.setUp
    tearDown=fixtures.MemoryTests.tearDown
    note=fixtures.MemoryTests.note
    reference=fixtures.MemoryTests.reference
    propose=fixtures.MemoryTests.propose
    update=fixtures.MemoryTests.update
    item=originals.DocumentOriginalTests.item
    save=originals.DocumentOriginalTests.save
    original_path=originals.DocumentOriginalTests.original_path
    folder=fixtures.MemoryTests.folder
    write=fixtures.MemoryTests.write
    meta=fixtures.MemoryTests.meta
    user=fixtures.MemoryTests.user
    collect=fixtures.MemoryTests.collect

    def files(self, output):
        return {str(f.relative_to(output)):hashlib.sha256(f.read_bytes()).hexdigest() for f in output.rglob('*') if f.is_file()}

    def moved(self):
        note=self.note('Synthetic relocation source.');cid=self.propose(note)
        self.update(cid,state='confirmed')
        (self.store.output/'memory.md').write_text('Synthetic manual memory, preserve exactly.')
        self.update(cid,text='Synthetic changed memory.')
        old=self.store.output;before=self.files(old);node_id=self.store.setting('nodeID')
        self.store.close();target=(self.root/'moved').resolve();(self.root/'node').rename(target)
        return target,target/'Library',old,before,node_id,note,cid

    def test_preview_is_read_only_and_prepare_preserves_files_identity_history_and_conflicts(self):
        root,output,old,before,node_id,note,cid=self.moved()
        self.assertFalse(inspect_library(root)['outputAvailable'])
        preview=inspect_library(root,output)
        self.assertEqual(preview['previousOutputDirectory'],str(old));self.assertTrue(preview['outputAvailable'])
        self.assertEqual(preview['modifiedFiles'],1);self.assertEqual(preview['relocationFiles'],len(before))
        db=sqlite3.connect(root/'node.sqlite3');self.assertEqual(db.execute("SELECT value FROM settings WHERE key='outputDirectory'").fetchone()[0],str(old));db.close()
        self.assertEqual(self.files(output),before);self.assertFalse(old.exists())
        prepare_library(root,preview['snapshot'],output)
        self.store=Store(root);self.assertEqual(self.store.setting('nodeID'),node_id);self.assertEqual(self.store.output,output)
        self.assertEqual(self.store.knowledge.note(note['id'])['text'],note['text'])
        self.assertEqual(len(self.store.memories.history(cid)['items']),3)
        self.assertEqual(self.files(output),before);self.assertFalse(old.exists())
        state=self.store.memories.delivery_status();self.assertTrue(state['paused']);self.assertIsNone(state['problem'])
        self.assertEqual(state['conflicts'][0]['path'],'memory.md')
        self.assertFalse(self.store.db.execute('SELECT 1 FROM exports WHERE root=?',(str(old),)).fetchone())
        self.assertEqual(self.store.memories.deliver_pending(force=True)['state'],'review')
        self.assertEqual((output/'memory.md').read_text(),'Synthetic manual memory, preserve exactly.')
        pair=self.store.memory_files.preview('memory.md')
        result=self.store.memory_files.resolve({'path':'memory.md','snapshot':pair['snapshot'],'action':'keep','requestID':str(uuid.uuid4())})
        self.assertEqual(result['action'],'keep');self.assertEqual(self.store.memories.delivery_status()['state'],'reviewed')

    def test_file_or_folder_replacement_after_preview_never_relinks(self):
        root,output,old,_,_,note,_=self.moved();preview=inspect_library(root,output)
        file=output/'notes'/(note['id']+'.md');file.write_text(file.read_text()+' Changed after preview.')
        with self.assertRaises(BackupError):prepare_library(root,preview['snapshot'],output)
        preview=inspect_library(root,output);held=root/'held-text';output.rename(held);shutil.copytree(held,output)
        with self.assertRaises(BackupError):prepare_library(root,preview['snapshot'],output)
        db=sqlite3.connect(root/'node.sqlite3');self.assertEqual(db.execute("SELECT value FROM settings WHERE key='outputDirectory'").fetchone()[0],str(old));db.close()
        self.assertFalse(old.exists())

    def test_metadata_changes_and_conflicting_existing_registration_require_new_preview(self):
        root,output,old,_,_,_,_=self.moved();preview=inspect_library(root,output)
        db=sqlite3.connect(root/'node.sqlite3')
        with db:db.execute("UPDATE exports SET semantic_hash='changed' WHERE path='memory.md'")
        with self.assertRaises(BackupError):prepare_library(root,preview['snapshot'],output)
        with db:db.execute("INSERT INTO exports SELECT ?,path,last_hash,semantic_hash,state,generated_path,updated_at FROM exports WHERE path='memory.md'",(str(output),))
        with self.assertRaises(BackupError):inspect_library(root,output)
        self.assertEqual(db.execute("SELECT value FROM settings WHERE key='outputDirectory'").fetchone()[0],str(old));db.close()

    def test_missing_text_generated_version_and_empty_folder_are_rejected(self):
        root,output,_,_,_,note,_=self.moved()
        empty=self.root/'empty';empty.mkdir()
        with self.assertRaises(BackupError):inspect_library(root,empty)
        file=output/'notes'/(note['id']+'.md');held=self.root/'held-note';file.rename(held)
        with self.assertRaises(BackupError):inspect_library(root,output)
        held.rename(file)
        generated=next((output/'.generated/memory.md').glob('*.md'));generated.rename(self.root/'held-generated')
        with self.assertRaises(BackupError):inspect_library(root,output)

    def test_symlink_file_and_directory_are_rejected_without_reading_target(self):
        root,output,_,_,_,note,_=self.moved();file=output/'notes'/(note['id']+'.md')
        held=self.root/'held-note';file.rename(held);file.symlink_to(held)
        with self.assertRaises(BackupError):inspect_library(root,output)
        file.unlink();held.rename(file)
        folder=output/'notes';held_folder=self.root/'held-notes';folder.rename(held_folder);folder.symlink_to(held_folder,target_is_directory=True)
        with self.assertRaises(BackupError):inspect_library(root,output)
        linked=self.root/'linked';linked.symlink_to(output,target_is_directory=True)
        with self.assertRaises(BackupError):inspect_library(root,linked)

    def test_copy_can_be_selected_explicitly_without_changing_original_files(self):
        self.note();previous=self.store.output;original=self.files(previous)
        target=(self.root/'copied-text').resolve();shutil.copytree(previous,target)
        preview=inspect_library(self.store.data,target);self.store.close()
        prepare_library(self.root/'node',preview['snapshot'],target);self.store=Store(self.root/'node')
        self.assertEqual(self.store.output,target);self.assertEqual(self.files(previous),original)
        self.assertEqual(self.files(target),original)

    def test_path_updates_rollback_together_and_incomplete_work_stays_paused(self):
        root,output,old,_,_,_,_=self.moved();db=sqlite3.connect(root/'node.sqlite3')
        review=str(uuid.uuid4());rid=str(uuid.uuid4())
        with db:
            db.execute("INSERT INTO memory_reviews VALUES (?,?,?,'{}','queued',NULL,NULL,0,'now','now')",(review,rid,rid))
            db.execute("INSERT INTO speech_jobs(id,checksum,service,status,baseline,output_root,updated_at) VALUES (?,?,?,'queued',?,?,?)",(rid,'synthetic','synthetic','baseline',str(old),'now'))
            db.execute("INSERT INTO memory_file_operations VALUES (?,'fingerprint',?,'{}','pending')",(rid,str(old)))
        preview=inspect_library(root,output)
        from library_relocation import relink_output
        def failed(*args):relink_output(*args);raise OSError('Synthetic transaction interruption')
        with patch('library_relocation.relink_output',side_effect=failed),self.assertRaises(OSError):prepare_library(root,preview['snapshot'],output)
        self.assertEqual(db.execute("SELECT value FROM settings WHERE key='outputDirectory'").fetchone()[0],str(old))
        prepare_library(root,preview['snapshot'],output)
        self.assertEqual(db.execute('SELECT output_root,status FROM speech_jobs WHERE id=?',(rid,)).fetchone(),(str(output),'failed'))
        self.assertEqual(db.execute('SELECT root,state FROM memory_file_operations WHERE id=?',(rid,)).fetchone(),(str(output),'blocked'))
        self.assertEqual(db.execute('SELECT status FROM memory_reviews WHERE id=?',(review,)).fetchone()[0],'interrupted');db.close()

    def test_relinked_backup_preserves_file_bytes_and_uses_current_output_root(self):
        root,output,_,before,_,_,_=self.moved();preview=inspect_library(root,output);prepare_library(root,preview['snapshot'],output)
        self.store=Store(root);self.store.close()
        archive=self.root/'relinked.zip';receipt=create_backup(root,archive)
        self.assertEqual(inspect_backup(archive,self.root)['archiveSHA256'],receipt['archiveSHA256'])
        restored=self.root/'restored';restore_backup(archive,restored,receipt['archiveSHA256'],self.root)
        self.assertEqual(self.files(restored/'Library'),before)

    def test_cli_and_old_selection_pause_messages(self):
        root,output,_,_,_,_,_=self.moved();script=Path(__file__).parents[1]/'library_location_cli.py'
        run=subprocess.run([sys.executable,str(script),'inspect','--data',str(root),'--output',str(output)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout);preview=json.loads(run.stdout)
        self.assertNotIn('Synthetic relocation source',run.stdout);self.assertEqual(run.stderr,'')
        prepare_library(root,preview['snapshot'],output);self.store=Store(root)
        for issue in ['切换资料库后，请核对并重新写入记忆文件。','Real synthetic write failure']:
            with self.store.db:self.store.db.execute('UPDATE memory_delivery SET pending=1,problem=?',(issue,))
            state=self.store.memories.delivery_status()
            self.assertEqual(state['problem'],None if issue.startswith('切换') else issue)

    def test_original_copy_is_required_and_manual_binary_changes_are_preserved(self):
        item,_=self.item();_,_,source=self.save(item)
        original=self.original_path(source);original.write_bytes(original.read_bytes()+b'Synthetic user annotation')
        root,output,_,before,_,_,_=self.moved();copy=output/'originals/documents'/original.name
        saved=self.root/'held-original';copy.rename(saved)
        with self.assertRaises(BackupError):inspect_library(root,output)
        saved.rename(copy);preview=inspect_library(root,output);self.assertEqual(preview['modifiedFiles'],2)
        prepare_library(root,preview['snapshot'],output)
        self.store=Store(root);self.assertEqual(self.files(output),before)
        self.assertEqual(self.store.documents.listing({})['items'][0]['documentInfo']['originalCopy']['state'],'unavailable')

    def test_check_limits_and_change_during_file_read_do_not_publish_locations(self):
        root,output,old,_,_,_,_=self.moved()
        with patch('library_relocation.MAX_TOTAL',1),self.assertRaises(BackupError):inspect_library(root,output)
        import library_relocation
        original_stat=library_relocation.os.stat;changed=False
        def replacing(name,*args,**kwargs):
            nonlocal changed
            value=original_stat(name,*args,**kwargs)
            if name=='memory.md' and stat.S_ISREG(value.st_mode) and not changed:
                changed=True;(output/'memory.md').write_text('Changed during verification.')
            return value
        with patch('library_relocation.os.stat',side_effect=replacing),self.assertRaises(BackupError):inspect_library(root,output)
        self.assertTrue(changed)
        db=sqlite3.connect(root/'node.sqlite3');self.assertEqual(db.execute("SELECT value FROM settings WHERE key='outputDirectory'").fetchone()[0],str(old));db.close()

    def test_selecting_current_folder_needs_no_relink_and_rejects_symlink_alias(self):
        self.note();ordinary=inspect_library(self.store.data)
        selected=inspect_library(self.store.data,self.store.output)
        self.assertEqual(selected,ordinary)
        linked=self.root/'linked-current';linked.symlink_to(self.store.output,target_is_directory=True)
        with self.assertRaises(BackupError):inspect_library(self.store.data,linked)

    def test_selection_preserves_real_delivery_failure_instead_of_replacing_it_with_pause(self):
        self.note()
        with self.store.db:self.store.db.execute("UPDATE memory_delivery SET pending=1,problem='Synthetic disk error'")
        preview=inspect_library(self.store.data);self.store.close()
        prepare_library(self.root/'node',preview['snapshot']);self.store=Store(self.root/'node')
        state=self.store.memories.delivery_status();self.assertTrue(state['paused']);self.assertEqual(state['problem'],'Synthetic disk error')

    def test_local_inputs_are_counted_and_keep_manual_files_after_relink(self):
        directory,file=self.folder();self.write(file,[self.meta(),self.user('Synthetic local input.')]);preview,_=self.collect('codex',directory)
        input_id=self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        path=self.store.output/'sources/codex'/preview['sourceID']/'inputs'/(input_id+'.md')
        path.write_text(path.read_text()+'\nSynthetic manual input annotation.');raw=path.read_bytes()
        root,output,_,_,_,_,_=self.moved();selection=inspect_library(root,output);self.assertEqual(selection['localInputs'],1)
        self.assertEqual(selection['modifiedFiles'],2)
        prepare_library(root,selection['snapshot'],output);self.store=Store(root)
        self.assertEqual((output/'sources/codex'/preview['sourceID']/'inputs'/(input_id+'.md')).read_bytes(),raw)
        self.assertEqual(self.store.db.execute('SELECT directory,automatic FROM local_sources').fetchone()[0],str(directory.resolve()))
        self.assertEqual(self.store.db.execute('SELECT automatic FROM local_sources').fetchone()[0],0)

    def test_continuously_growing_file_is_rejected_with_bounded_reads(self):
        root,output,_,_,_,note,_=self.moved();target=output/'notes'/(note['id']+'.md');inode=target.stat().st_ino
        real_open=os.fdopen;reads=[]
        class Growing:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*_):self.stream.close()
            def fileno(self):return self.stream.fileno()
            def read(self,size):
                reads.append(size)
                if len(reads)>2:raise AssertionError('Read did not stop at the inspected file size')
                with target.open('ab') as writer:writer.write(b'x'*1024)
                return self.stream.read(size)
        def open_growing(fd,*args,**kwargs):
            matching=os.fstat(fd).st_ino==inode;stream=real_open(fd,*args,**kwargs)
            return Growing(stream) if matching else stream
        with patch('library_relocation.os.fdopen',side_effect=open_growing),self.assertRaises(BackupError):inspect_library(root,output)
        self.assertEqual(len(reads),2);self.assertEqual(reads[-1],1)

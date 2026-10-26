"""Archive disconnects must not create empty replacements or lose prior indexes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_node as fixtures
from store import Store, StoreError
from output_root import OutputRootError, saved_identity, inspect_root
from library_location import inspect_library, prepare_library
from backup import BackupError, create_backup, inspect_backup, restore_backup


class OutputRootTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown

    def note(self):
        return self.store.knowledge.save_note({'title':'Synthetic recovery','text':'Keep the synthetic original.'})

    def detach(self):
        self.old=self.store.output; self.held=self.root/'held-output';self.old.rename(self.held)

    def test_missing_runtime_output_stops_writes_without_creating_replacement(self):
        note=self.note();self.detach()
        before={str(p.relative_to(self.held)):p.read_bytes() for p in self.held.rglob('*') if p.is_file()}
        self.assertIsNotNone(self.store.status()['outputIssue'])
        with self.assertRaises(StoreError):self.store.export_memory()
        with self.assertRaises(StoreError):self.store.knowledge.save_note({'title':'New','text':'Must not save.'})
        with self.assertRaises(StoreError):self.store.read_document('notes/'+note['id']+'.md')
        self.assertFalse(self.old.exists())
        self.assertEqual({str(p.relative_to(self.held)):p.read_bytes() for p in self.held.rglob('*') if p.is_file()},before)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0],1)
        self.held.rename(self.old)
        self.assertIsNone(self.store.status()['outputIssue']);self.store.memories.deliver_pending(force=True)
        self.assertEqual(self.store.knowledge.note(note['id'])['text'],'Keep the synthetic original.')

    def test_missing_output_does_not_recreate_at_restart(self):
        self.note();data=self.store.data;self.store.close();self.detach()
        with self.assertRaises(StoreError):Store(data)
        self.assertFalse(self.old.exists());self.held.rename(self.old);self.store=Store(data)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0],1)

    def test_same_path_replacement_is_rejected_during_runtime_and_after_restart(self):
        self.note();self.detach();self.old.mkdir()
        with self.assertRaises(StoreError):self.store.export_memory()
        self.assertEqual(list(self.old.iterdir()),[])
        data=self.store.data;self.store.close()
        with self.assertRaises(StoreError):Store(data)
        self.assertFalse(inspect_library(data)['outputAvailable'])
        self.old.rmdir();self.held.rename(self.old);self.store=Store(data)

    def test_complete_replacement_requires_explicit_relink_and_preserves_manual_content(self):
        note=self.note();self.detach();shutil.copytree(self.held,self.old)
        file=self.old/'notes'/(note['id']+'.md');file.write_text('Manual correction in copied folder.')
        data=self.store.data;self.store.close()
        self.assertFalse(inspect_library(data)['outputAvailable'])
        preview=inspect_library(data,self.old);self.assertTrue(preview['outputAvailable'])
        self.assertGreater(preview['modifiedFiles'],0)
        prepared=prepare_library(data,preview['snapshot'],self.old);self.assertTrue(prepared['receivingPaused'])
        self.store=Store(data)
        self.assertEqual(self.store.knowledge.note(note['id'])['text'],'Manual correction in copied folder.')
        self.assertTrue((self.held/'notes'/(note['id']+'.md')).is_file())

    def test_empty_replacement_cannot_be_approved_as_complete(self):
        self.note();self.detach();self.old.mkdir();data=self.store.data;self.store.close()
        with self.assertRaises(BackupError):inspect_library(data,self.old)
        self.old.rmdir();self.held.rename(self.old);self.store=Store(data)

    def test_replaced_after_preview_is_rejected(self):
        self.note();data=self.store.data;self.store.close();preview=inspect_library(data)
        self.detach();shutil.copytree(self.held,self.old)
        with self.assertRaises(BackupError):prepare_library(data,preview['snapshot'])
        shutil.rmtree(self.old);self.held.rename(self.old);self.store=Store(data)

    def test_symlink_replacement_is_not_followed(self):
        self.note();self.detach();other=self.root/'other';other.mkdir();self.old.symlink_to(other,target_is_directory=True)
        with self.assertRaises(StoreError):self.store.export_memory()
        self.assertEqual(list(other.iterdir()),[])
        self.old.unlink();self.held.rename(self.old)

    def test_read_only_directory_reports_issue_and_recovers(self):
        self.note();root=self.store.output;mode=root.stat().st_mode
        root.chmod(0o500)
        try:
            if os.access(root,os.W_OK):self.skipTest('Current user bypasses file permissions')
            self.assertIn('无法写入',self.store.status()['outputIssue'])
            with self.assertRaises(StoreError):self.store._atomic(root/'blocked.md',b'blocked')
            self.assertFalse((root/'blocked.md').exists())
        finally:root.chmod(mode)
        self.assertIsNone(self.store.status()['outputIssue'])

    def test_reconcile_preserves_index_when_root_is_lost(self):
        self.note();before=[tuple(r) for r in self.store.db.execute('SELECT * FROM library_items ORDER BY kind,id')]
        self.detach()
        with self.assertRaises(StoreError):self.store.catalog.reconcile()
        self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT * FROM library_items ORDER BY kind,id')],before)
        self.held.rename(self.old)

    def test_memory_decision_and_evidence_survive_failed_file_delivery(self):
        note=self.note();_,body,version=self.store.memories.source('note',note['id'])
        cid=self.store.add_candidate('Preserve the original.', evidence=[
            {'kind':'note','id':note['id'],'contentVersion':version,'quote':body}])['id']
        captured=self.store.db.execute('SELECT evidence FROM memory_details WHERE id=?',(cid,)).fetchone()[0]
        self.detach()
        with self.assertRaises(StoreError):self.store.decide_candidate(cid,'confirmed')
        self.assertEqual(self.store.db.execute('SELECT state FROM candidates WHERE id=?',(cid,)).fetchone()[0],'confirmed')
        self.assertEqual(self.store.db.execute('SELECT evidence FROM memory_details WHERE id=?',(cid,)).fetchone()[0],captured)
        self.assertEqual(self.store.memories.delivery_status()['state'],'failed')
        self.assertFalse(self.old.exists());self.held.rename(self.old)
        self.assertEqual(self.store.memories.deliver_pending(force=True)['state'],'ready')
        self.assertIn('Preserve the original.',(self.old/'memory.md').read_text())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_versions WHERE id=?',(cid,)).fetchone()[0],2)

    def test_atomic_write_disconnect_after_open_does_not_recreate_root(self):
        self.note();root=self.store.output;original=os.replace
        def moved(src,dst,**kwargs):
            self.detach();return original(src,dst,**kwargs)
        with patch('output_root.os.replace',side_effect=moved):
            with self.assertRaises(StoreError):self.store._atomic(root/'race.md',b'Synthetic finished write')
        self.assertFalse(root.exists())
        # Any published bytes remain in the originally opened folder, never a replacement.
        self.assertEqual((self.held/'race.md').read_bytes(),b'Synthetic finished write')
        self.assertFalse(any(p.name.endswith('.tmp') for p in self.held.rglob('*')))
        self.held.rename(root)

    def test_atomic_parent_symlink_cannot_redirect_a_prepared_path(self):
        self.note();root=self.store.output;prepared=self.store._path('new-parent/file.md')
        other=self.root/'outside';other.mkdir();(root/'new-parent').symlink_to(other,target_is_directory=True)
        with self.assertRaises(OSError):self.store._atomic(prepared,b'must not escape')
        self.assertEqual(list(other.iterdir()),[])

    def test_parent_replaced_during_publish_is_not_acknowledged_as_current(self):
        root=self.store.output;parent=root/'race-parent';parent.mkdir();held=root/'held-parent'
        original=os.replace
        def moved(src,dst,**kwargs):
            parent.rename(held);parent.mkdir();return original(src,dst,**kwargs)
        with patch('output_root.os.replace',side_effect=moved):
            with self.assertRaises(StoreError):self.store._atomic(parent/'file.md',b'Preserved pending content')
        self.assertEqual(list(parent.iterdir()),[])
        self.assertEqual((held/'file.md').read_bytes(),b'Preserved pending content')

    def test_explicit_archive_copy_rebinds_identity_and_stays_openable(self):
        note=self.note();target=self.root/'chosen-output'
        self.store.configure({'outputDirectory':str(target)})
        self.assertEqual(saved_identity(self.store.db),inspect_root(target.resolve()))
        data=self.store.data;self.store.close();self.store=Store(data)
        self.assertEqual(self.store.knowledge.note(note['id'])['text'],'Keep the synthetic original.')

    def test_backup_omits_machine_identity_and_restore_binds_new_root(self):
        self.note();data=self.store.data;self.store.close();archive=self.root/'backup.zip'
        try:
            report=create_backup(data,archive);restore_backup(archive,self.root/'restored',report['archiveSHA256'],self.root)
            restored=Store(self.root/'restored')
            try:self.assertEqual(saved_identity(restored.db),inspect_root(restored.output))
            finally:restored.close()
        finally:self.store=Store(data)

    def test_durable_audio_receipt_survives_text_folder_outage(self):
        rid,manifest,audio=fixtures.NodeTests.fixture(self)
        self.detach()
        receipt=self.store.accept(manifest,audio)
        self.assertTrue(receipt['durable'])
        self.assertEqual(self.store.audio_path(rid).read_bytes(),audio.read_bytes())
        self.assertEqual(self.store.accept(manifest,audio),receipt)
        self.assertFalse(self.old.exists());self.held.rename(self.old)
        data=self.store.data;self.store.close();self.store=Store(data)
        self.assertTrue((self.old/'transcripts'/(rid+'.md')).exists())

    def test_read_only_original_folder_can_still_be_backed_up(self):
        self.note();data=self.store.data;root=self.store.output;mode=root.stat().st_mode
        self.store.close();root.chmod(0o500)
        try:self.assertGreater(create_backup(data,self.root/'read-only-backup.zip')['files'],0)
        finally:root.chmod(mode);self.store=Store(data)

    def test_backup_rejects_missing_or_replaced_output(self):
        self.note();data=self.store.data;self.store.close();self.detach();self.old.mkdir()
        try:
            with self.assertRaises(BackupError):create_backup(data,self.root/'bad.zip')
            self.assertFalse((self.root/'bad.zip').exists())
        finally:self.old.rmdir();self.held.rename(self.old);self.store=Store(data)

    def test_legacy_library_binds_existing_root_without_changing_originals(self):
        self.note();before={str(p.relative_to(self.store.output)):p.read_bytes() for p in self.store.output.rglob('*') if p.is_file()}
        data=self.store.data
        with self.store.db:self.store.db.execute("DELETE FROM settings WHERE key='outputRootIdentity'")
        self.store.close();self.store=Store(data)
        self.assertEqual(saved_identity(self.store.db),inspect_root(self.store.output))
        for path,content in before.items():self.assertEqual((self.store.output/path).read_bytes(),content)

    def test_duplicate_import_original_copy_obeys_root_identity_and_can_retry(self):
        import base64
        from test_pdf_documents import pdf_bytes
        raw=pdf_bytes(['Synthetic source.'])
        item={'name':'source.pdf','data':base64.b64encode(raw).decode()}
        preview=self.store.documents.preview({'files':[item]});self.store.documents.commit(preview['id'])
        item['retainOriginal']=True
        preview=self.store.documents.preview({'files':[item]})
        self.detach();self.old.mkdir()
        with self.assertRaises(StoreError):self.store.documents.commit(preview['id'])
        self.assertEqual(list(self.old.iterdir()),[])
        self.old.rmdir();self.held.rename(self.old)
        self.assertEqual(self.store.documents.commit(preview['id'])['originalsSaved'],1)
        document=self.store.documents.listing({})['items'][0]
        self.assertEqual(base64.b64decode(self.store.documents.original(document['id'])['data']),raw)

    def test_original_copy_rejects_root_swapped_before_descriptor_open(self):
        import uuid
        from document_originals import retain_original,OriginalError
        root=self.store.output;raw=b'Synthetic original bytes';identifier=str(uuid.uuid4()).upper()
        original=os.open;swapped=False
        def replaced(path,*args,**kwargs):
            nonlocal swapped
            if str(path)==str(root) and not swapped:
                swapped=True;self.detach();self.old.mkdir()
            return original(path,*args,**kwargs)
        with patch('document_originals.os.open',side_effect=replaced):
            with self.assertRaises(OriginalError):retain_original(root,identifier,'pdf',raw,hashlib.sha256(raw).hexdigest(),root_guard=self.store.output_root)
        self.assertEqual(list(root.iterdir()),[])
        root.rmdir();self.held.rename(root)

    def test_original_copy_parent_changed_during_publish_keeps_bytes_without_success(self):
        import uuid
        from document_originals import retain_original,OriginalError
        root=self.store.output;raw=b'Synthetic original bytes';identifier=str(uuid.uuid4()).upper()
        parent=root/'originals/documents';held=root/'held-original-documents';original=os.link
        def moved(src,dst,**kwargs):
            parent.rename(held);parent.mkdir();return original(src,dst,**kwargs)
        with patch('document_originals.os.link',side_effect=moved):
            with self.assertRaises(OriginalError):retain_original(root,identifier,'pdf',raw,hashlib.sha256(raw).hexdigest(),root_guard=self.store.output_root)
        self.assertEqual(list(parent.iterdir()),[])
        self.assertEqual((held/(identifier+'.pdf')).read_bytes(),raw)

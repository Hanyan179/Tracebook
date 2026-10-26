import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch

import test_node as fixtures
from test_docx_documents import package, paragraph
from test_pdf_documents import pdf_bytes
from backup import BackupError, create_backup, inspect_backup, restore_backup
from document_originals import OriginalError, retain_original
from store import Store, StoreError
from server import Server


class DocumentOriginalTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown

    def item(self, text='Orchard synthetic original.', extension='docx', retain=True):
        raw = package(paragraph(text)) if extension == 'docx' else pdf_bytes([text])
        return {'name': 'orchard.' + extension, 'sourcePath': '/selected/orchard.' + extension,
                'data': base64.b64encode(raw).decode(), 'retainOriginal': retain}, raw

    def save(self, item):
        preview = self.store.documents.preview({'files': [item]})
        report = self.store.documents.commit(preview['id'])
        rows = self.store.documents.listing({})['items']
        digest = hashlib.sha256(base64.b64decode(item['data'])).hexdigest()
        source = next(row for row in rows if row['documentInfo']['originalFileSHA256'] == digest)
        return preview, report, source

    def original_path(self, source):
        return self.store.output/'originals/documents'/(source['id'] + '.' + source['documentInfo']['format'])

    def backup(self):
        directory = self.store.data
        self.store.close()
        target = self.root/('backup-' + uuid.uuid4().hex + '.zip')
        try:return target, create_backup(directory, target)
        finally:self.store = Store(directory)

    def test_opt_in_preview_cancel_and_default_exclude_original_bytes(self):
        item, raw = self.item();item.pop('retainOriginal')
        _, report, source = self.save(item)
        self.assertEqual(report['originalsSaved'], 0)
        self.assertEqual(source['documentInfo']['originalCopy']['state'], 'not_retained')
        self.assertFalse((self.store.output/'originals').exists())
        item['retainOriginal'] = True
        preview = self.store.documents.preview({'files': [item]})
        self.assertEqual(preview['originalCopies'], [{'name': item['name'], 'state': 'selected', 'bytes': len(raw)}])
        self.assertFalse((self.store.output/'originals').exists())
        self.store.documents.cancel(preview['id'])
        self.assertFalse((self.store.data/'document-previews'/(preview['id'] + '.json')).exists())
        self.assertFalse((self.store.output/'originals').exists())

    def test_pdf_and_docx_retain_exact_bytes_and_repeat_commit_is_idempotent(self):
        for extension in ('pdf', 'docx'):
            with self.subTest(extension=extension):
                item, raw = self.item(extension=extension)
                preview, report, source = self.save(item)
                self.assertEqual(report['originalsSaved'], 1)
                original = self.store.documents.original(source['id'])
                self.assertEqual(base64.b64decode(original['data']), raw)
                self.assertEqual(original['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual(self.original_path(source).read_bytes(), raw)
                self.assertEqual(self.store.documents.commit(preview['id']), report)
                self.assertEqual(source['documentInfo']['originalCopy']['state'], 'available')
                with zipfile.ZipFile(self.store.exchange.export()) as portable:
                    self.assertIn('documents/' + source['id'] + '.md', portable.namelist())
                    self.assertFalse(any(name.endswith(('.pdf', '.docx')) for name in portable.namelist()))

    def test_duplicate_can_add_copy_without_replacing_text_or_identity(self):
        item, raw = self.item(retain=False);_, _, source = self.save(item)
        text = self.store.output/'documents'/(source['id'] + '.md')
        edited = text.read_bytes() + b'\nHuman edit stays.\n';text.write_bytes(edited)
        item['retainOriginal'] = True
        _, report, repeated = self.save(item)
        self.assertEqual(repeated['id'], source['id']);self.assertEqual(report['added'], 0)
        self.assertEqual(report['skipped'], 1);self.assertEqual(report['originalsSaved'], 1)
        self.assertEqual(text.read_bytes(), edited);self.assertEqual(self.original_path(source).read_bytes(), raw)

    def test_duplicate_within_batch_preserves_explicit_original_selection(self):
        item, raw = self.item(retain=False)
        second = {**item, 'retainOriginal': True, 'sourcePath': '/selected/duplicate.docx'}
        preview = self.store.documents.preview({'files': [item, second]})
        self.assertEqual(preview['accepted'], 1);self.assertEqual(preview['duplicates'], 1)
        self.assertEqual(len(preview['originalCopies']), 1)
        result = self.store.documents.commit(preview['id']);self.assertEqual(result['originalsSaved'], 1)
        source = self.store.documents.listing({})['items'][0]
        self.assertEqual(self.original_path(source).read_bytes(), raw)

    def test_changed_input_keeps_both_original_versions(self):
        item, old_raw = self.item();_, _, old = self.save(item)
        item, new_raw = self.item('Orchard updated source.');_, _, new = self.save(item)
        self.assertNotEqual(old['id'], new['id'])
        self.assertEqual(new['documentRevision']['previousID'], old['id'])
        self.assertEqual(self.original_path(old).read_bytes(), old_raw)
        self.assertEqual(self.original_path(new).read_bytes(), new_raw)

    def test_known_credentials_block_original_copy_but_keep_redacted_text(self):
        item, raw = self.item('Orchard token sk-' + 's'*50)
        preview, report, source = self.save(item)
        self.assertEqual(preview['redacted'], 1)
        self.assertEqual(preview['originalCopies'][0]['state'], 'blocked')
        self.assertEqual(report['originalsSaved'], 0)
        self.assertFalse(self.original_path(source).exists())
        with self.assertRaises(StoreError):self.store.documents.original(source['id'])

    def test_changed_copy_is_never_overwritten_and_missing_copy_can_be_restored(self):
        item, raw = self.item();_, _, source = self.save(item);path = self.original_path(source)
        path.write_bytes(b'Human binary change')
        with self.assertRaises(StoreError):self.store.documents.original(source['id'])
        with self.assertRaises(StoreError):self.save(item)
        self.assertEqual(path.read_bytes(), b'Human binary change')
        archive, report = self.backup()
        self.assertEqual(report['modifiedOriginalDocuments'], 1)
        target = self.root/'restored-manual'
        restore_backup(archive, target, report['archiveSHA256'], self.root)
        copied = target/'Library/originals/documents'/path.name
        self.assertEqual(copied.read_bytes(), b'Human binary change')
        with zipfile.ZipFile(archive) as z:entries = {n:z.read(n) for n in z.namelist()}
        manifest = json.loads(entries['manifest.json']);manifest['modifiedOriginalDocuments'] = 0
        entries['manifest.json'] = json.dumps(manifest).encode();tampered = self.root/'hidden-original-change.zip'
        with zipfile.ZipFile(tampered, 'w') as z:
            for name, value in entries.items():z.writestr(name, value)
        with self.assertRaises(BackupError):inspect_backup(tampered, self.root)
        restored = Store(target)
        try:
            with self.assertRaises(StoreError):restored.documents.original(source['id'])
            self.assertEqual(restored.documents.listing({})['items'][0]['documentInfo']['originalCopy']['state'], 'unavailable')
        finally:restored.close()
        path.unlink()
        self.assertEqual(self.store.documents.listing({})['items'][0]['documentInfo']['originalCopy']['state'], 'unavailable')
        _, report, _ = self.save(item)
        self.assertEqual(report['added'], 0);self.assertEqual(path.read_bytes(), raw)

    def test_symlink_fifo_and_concurrent_file_are_not_overwritten(self):
        item, raw = self.item();rid = str(uuid.uuid4()).upper();digest = hashlib.sha256(raw).hexdigest()
        root = self.store.output/'originals';outside = self.root/'outside';outside.mkdir()
        root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OriginalError):retain_original(self.store.output, rid, 'docx', raw, digest)
        self.assertEqual(list(outside.iterdir()), []);root.unlink()
        directory = root/'documents';directory.mkdir(parents=True)
        path = directory/(rid+'.docx');os.mkfifo(path)
        with self.assertRaises(OriginalError):retain_original(self.store.output, rid, 'docx', raw, digest)
        path.unlink();real_link = os.link
        def raced(*args, **kwargs):
            path.write_bytes(b'Concurrent manual content')
            return real_link(*args, **kwargs)
        with patch('document_originals.os.link', side_effect=raced):
            with self.assertRaises(OriginalError):retain_original(self.store.output, rid, 'docx', raw, digest)
        self.assertEqual(path.read_bytes(), b'Concurrent manual content')
        self.assertFalse(list(directory.glob('.*.tmp')))

    def test_output_move_copies_originals_and_rejects_conflicting_destination(self):
        item, raw = self.item();_, _, source = self.save(item);old = self.store.output
        target = self.root/'new-library';path = target/'originals/documents'/self.original_path(source).name
        path.parent.mkdir(parents=True);path.write_bytes(b'Keep target content')
        with self.assertRaises(StoreError):self.store.configure({'outputDirectory': str(target)})
        self.assertEqual(self.store.output, old);self.assertEqual(path.read_bytes(), b'Keep target content')
        target = self.root/'clear-library';self.store.configure({'outputDirectory': str(target)})
        self.assertEqual(self.original_path(source).read_bytes(), raw)
        self.assertEqual((old/'originals/documents'/path.name).read_bytes(), raw)

    def test_backup_restores_original_and_rejects_missing_or_legacy_mislabel(self):
        item, raw = self.item();_, _, source = self.save(item)
        archive, report = self.backup();self.assertEqual(report['originalDocuments'], 1)
        target = self.root/'restored';restore_backup(archive, target, report['archiveSHA256'], self.root)
        restored = Store(target)
        try:self.assertEqual(base64.b64decode(restored.documents.original(source['id'])['data']), raw)
        finally:restored.close()
        with zipfile.ZipFile(archive) as z:entries = {n: z.read(n) for n in z.namelist()}
        manifest = json.loads(entries['manifest.json']);self.assertEqual(manifest['version'], 3)
        manifest['tables'].pop('memory_reviews');entries.pop('tables/memory_reviews.jsonl')
        manifest['files'] = [item for item in manifest['files'] if item['path'] != 'tables/memory_reviews.jsonl']
        manifest['version'] = 1;entries['manifest.json'] = json.dumps(manifest).encode()
        fake = self.root/'invalid-legacy.zip'
        with zipfile.ZipFile(fake, 'w') as z:
            for name, value in entries.items():z.writestr(name, value)
        with self.assertRaises(BackupError):inspect_backup(fake, self.root)
        self.original_path(source).unlink()
        with self.assertRaises(BackupError):self.backup()

    def test_version_one_backup_without_originals_remains_restorable(self):
        item, _ = self.item(retain=False);self.save(item);archive, _ = self.backup()
        with zipfile.ZipFile(archive) as z:entries = {n: z.read(n) for n in z.namelist()}
        manifest = json.loads(entries['manifest.json']);manifest['version'] = 1;manifest.pop('originalDocuments')
        manifest['tables'].pop('memory_reviews');entries.pop('tables/memory_reviews.jsonl')
        manifest['files'] = [item for item in manifest['files'] if item['path'] != 'tables/memory_reviews.jsonl']
        entries['manifest.json'] = json.dumps(manifest).encode();legacy = self.root/'legacy.zip'
        with zipfile.ZipFile(legacy, 'w') as z:
            for name, value in entries.items():z.writestr(name, value)
        report = inspect_backup(legacy, self.root);self.assertEqual(report['originalDocuments'], 0)
        target = self.root/'legacy-restored';restore_backup(legacy, target, report['archiveSHA256'], self.root)
        self.assertEqual(len(list((target/'Library/documents').glob('*.md'))), 1)

    def test_original_download_requires_owner_and_loopback(self):
        item, raw = self.item();_, _, source = self.save(item)
        server = Server(('127.0.0.1', 0), self.store, 'synthetic-original-owner')
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        def request(token):
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            connection.request('GET', '/v1/documents/' + source['id'] + '/original', headers={'Authorization': 'Bearer ' + token})
            response = connection.getresponse();body = json.loads(response.read());connection.close()
            return response.status, body
        try:
            self.assertEqual(request('not-owner')[0], 401)
            grant = self.store.knowledge.create_grant({'name':'Synthetic document reader','scopes':['documents'],'days':1})
            self.assertEqual(request(grant['token'])[0], 401)
            with patch('server.ipaddress.ip_address') as address:
                address.return_value.is_loopback = False
                self.assertEqual(request(server.token)[0], 403)
            status, value = request(server.token)
            self.assertEqual(status, 200);self.assertEqual(base64.b64decode(value['data']), raw)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

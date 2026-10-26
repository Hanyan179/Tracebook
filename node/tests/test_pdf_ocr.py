"""OCR coverage contracts; actual on-device recognition is additionally tested on Mac."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_node as fixtures
from test_pdf_documents import pdf_bytes
from pdf_documents import extract_pdf, PDFError
from store import StoreError


def result():
    return {'pageCount': 4, 'parserWarnings': False, 'ocrRequested': True,
            'ocrEngine': 'apple-vision-revision-3', 'pages': [
        {'number': 1, 'status': 'text', 'text': 'Embedded original.'},
        {'number': 2, 'status': 'text', 'text': 'Synthetic scanned evidence.', 'method': 'ocr', 'ocrStatus': 'recognized'},
        {'number': 3, 'status': 'empty', 'text': '', 'ocrStatus': 'no_text'},
        {'number': 4, 'status': 'empty', 'text': '', 'ocrStatus': 'limit'}]}


class PDFOCRContractTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown

    def item(self, recognize=True):
        return {'name': 'synthetic.pdf', 'data': base64.b64encode(pdf_bytes(['Embedded original.', None])).decode(),
                'sourcePath': '/synthetic/documents/synthetic.pdf', 'recognizeScans': recognize}

    def preview(self, value=None):
        with patch('documents.extract_pdf', return_value=value or result()):
            return self.store.documents.preview({'files': [self.item()]})

    def test_coverage_ranges_provenance_and_no_automatic_memory(self):
        preview = self.preview()
        self.assertEqual(preview['pdfs'][0]['ocrPages'], [2])
        self.assertEqual(preview['pdfs'][0]['ocrSkippedPages'], [4])
        self.assertEqual(preview['pdfs'][0]['emptyPages'], [3, 4])
        self.assertEqual(self.store.documents.listing({})['count'], 0)
        self.store.documents.commit(preview['id'])
        source = self.store.documents.listing({})['items'][0]
        read = self.store.knowledge.read_source('document', source['id'], ['documents'], {'limit': 16000})
        info = read['documentInfo']; page = info['pages'][1]
        self.assertEqual(read['text'][page['start']:page['end']], 'Synthetic scanned evidence.')
        self.assertEqual(page['method'], 'ocr'); self.assertTrue(info['ocrRequested'])
        self.assertIn('尚未识别', read['text']); self.assertIn('作者：未确认', read['text'])
        self.assertEqual(self.store.candidates(), [])
        self.assertEqual(self.store.analysis.snapshot(read['date'])['sources'], [])
        with self.assertRaises(StoreError):self.store.knowledge.read_source('document', source['id'], ['notes'], {})

    def test_ocr_new_revision_preserves_old_and_manual_text(self):
        old = self.store.documents.preview({'files': [self.item(False)]})
        self.store.documents.commit(old['id']); old_source = self.store.documents.listing({})['items'][0]
        path = self.store.output/'documents'/(old_source['id']+'.md'); path.write_text('Manual older content.')
        preview = self.preview(); self.assertEqual(self.store.documents.commit(preview['id'])['versions'], 1)
        self.assertEqual(path.read_text(), 'Manual older content.')
        self.assertEqual(self.preview()['duplicates'], 1)
        source = next(s for s in self.store.documents.listing({})['items'] if s['id'] != old_source['id'])
        (self.store.output/'documents'/(source['id']+'.md')).write_text('Manual OCR correction.')
        again = self.preview(); self.store.documents.commit(again['id'])
        read = self.store.knowledge.read_source('document', source['id'], ['documents'], {})
        self.assertEqual(read['text'], 'Manual OCR correction.'); self.assertFalse(read['documentInfo']['rangesValid'])
        self.assertEqual(read['documentInfo']['pages'][1]['method'], 'ocr')
        self.assertNotIn('start', read['documentInfo']['pages'][1])

    def test_coverage_change_is_a_new_version_even_with_same_text(self):
        preview = self.preview(); self.store.documents.commit(preview['id'])
        changed = result(); changed['pages'][1].pop('method'); changed['pages'][1].pop('ocrStatus')
        self.assertEqual(self.preview(changed)['added'], 1)

    def test_reject_non_boolean_choice(self):
        with self.assertRaises(StoreError): self.store.documents.preview({'files': [self.item('true')]})

    def test_all_embedded_text_keeps_existing_identity_when_ocr_was_not_needed(self):
        ordinary = {'pageCount': 1, 'parserWarnings': False, 'pages': [
            {'number': 1, 'status': 'text', 'text': 'Embedded original.'}]}
        with patch('documents.extract_pdf', return_value=ordinary):
            old = self.store.documents.preview({'files': [self.item(False)]})
        self.store.documents.commit(old['id'])
        with patch('documents.extract_pdf', return_value={**ordinary, 'ocrRequested': True}):
            again = self.store.documents.preview({'files': [self.item()]})
        self.assertEqual(again['duplicates'], 1); self.assertNotIn('ocrRequested', again['pdfs'][0])

    def test_non_apple_worker_explicitly_rejects_ocr(self):
        with patch('pdf_documents.ocr_available', return_value=False):
            with self.assertRaisesRegex(PDFError, 'ocr_unavailable'):extract_pdf(pdf_bytes([None]), self.root/'scratch', recognize_scans=True)
            preview = self.store.documents.preview({'files': [self.item()]})
            self.assertEqual(preview['warnings'][0]['reason'], 'ocr_unavailable')
            self.assertEqual(preview['accepted'], 0)

    def test_empty_scan_is_not_claimed_as_an_import(self):
        empty = result(); empty['pages'] = [{'number': 1, 'status': 'empty', 'text': '', 'ocrStatus': 'no_text'}]; empty['pageCount'] = 1
        preview = self.preview(empty)
        self.assertEqual(preview['accepted'], 0); self.assertEqual(preview['warnings'][0]['reason'], 'ocr_no_text')

    def test_original_and_coverage_survive_backup_restore(self):
        from backup import create_backup, restore_backup
        from store import Store
        item = self.item(); item['retainOriginal'] = True
        with patch('documents.extract_pdf', return_value=result()):
            preview = self.store.documents.preview({'files': [item]})
        self.store.documents.commit(preview['id'])
        source = self.store.documents.listing({})['items'][0]
        raw = base64.b64decode(item['data'])
        self.assertEqual(base64.b64decode(self.store.documents.original(source['id'])['data']), raw)
        original_data = self.store.data
        self.store.close(); archive = self.root/'ocr-backup.zip'
        report = create_backup(original_data, archive)
        target = self.root/'restored'
        restore_backup(archive, target, report['archiveSHA256'], self.root)
        restored = Store(target)
        try:
            actual = restored.documents.listing({})['items'][0]
            self.assertEqual(actual['documentInfo']['pages'], source['documentInfo']['pages'])
            self.assertTrue(actual['documentInfo']['ocrRequested'])
            self.assertEqual(base64.b64decode(restored.documents.original(source['id'])['data']), raw)
        finally:
            restored.close(); self.store = Store(original_data)

    def test_worker_cannot_silently_ignore_ocr_choice(self):
        missing = result(); missing.pop('ocrRequested')
        with patch('pdf_documents.ocr_available', return_value=True), patch('pdf_documents.worker_command', return_value=['worker']), patch('pdf_documents.supervise', return_value=missing):
            with self.assertRaisesRegex(PDFError, 'ocr_unavailable'):extract_pdf(b'%PDF-synthetic', self.root/'scratch', recognize_scans=True)
        self.assertEqual(list((self.root/'scratch').iterdir()), [])

    def test_invalid_recognition_metadata_rejected(self):
        malformed = result(); malformed['pages'][1]['status'] = 'empty'
        with patch('pdf_documents.ocr_available', return_value=True), patch('pdf_documents.worker_command', return_value=['worker']), patch('pdf_documents.supervise', return_value=malformed):
            with self.assertRaisesRegex(PDFError, 'invalid_pdf'):extract_pdf(b'%PDF-synthetic', self.root/'scratch', recognize_scans=True)


@unittest.skipUnless(sys.platform == 'darwin', 'Apple Vision on-device recognition requires macOS')
class PDFOCRNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        source = Path(__file__).parent/'fixtures/scanned_pdf.swift'
        subprocess.run(['xcrun', 'swiftc', str(source), '-o', str(cls.root/'make-scans')], check=True, capture_output=True, timeout=60)
        subprocess.run([str(cls.root/'make-scans'), str(cls.root/'scanned.pdf')], check=True, capture_output=True, timeout=20)
        cls.raw = (cls.root/'scanned.pdf').read_bytes()

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_real_bilingual_raster_pages_and_blank_page(self):
        ordinary = extract_pdf(self.raw, self.root/'scratch')
        self.assertEqual([p['status'] for p in ordinary['pages']], ['empty']*3)
        value = extract_pdf(self.raw, self.root/'scratch', seconds=60, recognize_scans=True)
        self.assertIn('Synthetic orchard review', value['pages'][0]['text'])
        self.assertIn('Friday', value['pages'][0]['text'])
        self.assertIn('合成资料核对', value['pages'][1]['text'])
        self.assertEqual([p.get('ocrStatus') for p in value['pages']], ['recognized', 'recognized', 'no_text'])
        self.assertEqual(list((self.root/'scratch').iterdir()), [])

    def test_real_limit_does_not_hide_remaining_pages(self):
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(self.raw)); writer = PdfWriter()
        for _ in range(11):writer.add_page(reader.pages[2])
        buffer = io.BytesIO();writer.write(buffer)
        value = extract_pdf(buffer.getvalue(), self.root/'scratch', seconds=60, recognize_scans=True)
        self.assertEqual(value['pageCount'], 11)
        self.assertEqual(value['pages'][10]['ocrStatus'], 'limit')
        self.assertEqual(value['pages'][10]['status'], 'empty')

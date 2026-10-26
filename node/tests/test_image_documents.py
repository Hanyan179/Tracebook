import base64
import copy
import hashlib
import http.client
import threading
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import test_node as fixtures
from image_documents import extract_image, MAX_IMAGE
from pdf_documents import PDFError
from backup import create_backup, inspect_backup, restore_backup, BackupError
from store import Store, StoreError
from server import Server


def extracted(lines=None):
    lines=['Synthetic orchard review', 'I review projects every Friday.'] if lines is None else lines
    return {'format':'image','parser':'apple-vision-revision-3','containerType':'public.png',
            'width':900,'height':1200,'orientation':1,'recognizedWidth':900,'recognizedHeight':1200,
            'lines':lines,'status':'recognized' if lines else 'no_text'}


class ImageDocumentTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown

    def item(self,**changes):
        return {'name':'orchard.png','sourcePath':'synthetic/orchard.png','data':base64.b64encode(b'synthetic image').decode(),**changes}

    def preview(self,item=None,result=None):
        with patch('documents.extract_image',return_value=result or extracted()):
            return self.store.documents.preview({'files':[item or self.item()]})

    def test_import_preview_and_lines_are_traceable_and_manual_text_is_preserved(self):
        preview=self.preview();self.assertEqual((preview['accepted'],preview['images'][0]['lines']),(1,2))
        self.assertEqual(self.store.documents.listing({})['count'],0)
        self.store.documents.commit(preview['id']);source=self.store.documents.listing({})['items'][0];rid=source['id']
        document=self.store.read_document('documents/'+rid+'.md');info=source['documentInfo'];self.assertTrue(info['imageOCR']);self.assertTrue(info['rangesValid'])
        for line,text in zip(info['imageLines'],extracted()['lines']):self.assertEqual(document['text'][line['start']:line['end']],text)
        original=document['text'];self.assertIn('作者：未确认',original)
        (self.store.output/'documents'/(rid+'.md')).write_text(original+'Manual annotation retained.\n')
        self.store.documents.commit(self.preview()['id'])
        current=self.store.documents.listing({})['items'][0]['documentInfo'];self.assertFalse(current['rangesValid'])
        self.assertTrue(all('start' not in line for line in current['imageLines']))
        self.assertTrue(self.store.read_document('documents/'+rid+'.md')['text'].endswith('Manual annotation retained.\n'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)

    def test_changed_file_creates_version_and_originals_are_opt_in(self):
        first=self.preview();self.store.documents.commit(first['id']);rid=self.store.documents.listing({})['items'][0]['id']
        with self.assertRaises(StoreError):self.store.documents.original(rid)
        self.store.documents.commit(self.preview(self.item(retainOriginal=True))['id'])
        self.assertEqual(base64.b64decode(self.store.documents.original(rid)['data']),b'synthetic image')
        second=self.preview(self.item(data=base64.b64encode(b'new image bytes').decode()),extracted(['Changed image text']))
        result=self.store.documents.commit(second['id']);self.assertEqual(result['versions'],1)
        self.assertEqual(self.store.documents.listing({})['count'],2)

    def test_recognized_credentials_block_retaining_raw_image_and_generated_role_stays_generated(self):
        value=extracted(['Authorization: Bearer synthetic-token-1234567890'])
        preview=self.preview(self.item(retainOriginal=True,contentRole='generated'),value)
        self.assertEqual(preview['originalCopies'][0]['state'],'blocked')
        self.store.documents.commit(preview['id']);source=self.store.documents.listing({})['items'][0]
        self.assertEqual(source['contentRole'],'generated')
        with self.assertRaises(StoreError):self.store.documents.original(source['id'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)

    def test_blank_unsupported_and_bad_images_are_reported_without_fake_import(self):
        for reason in ['ocr_unavailable','invalid_image','image_frames','image_dimensions','image_ocr_failed','image_too_large']:
            with self.subTest(reason=reason),patch('documents.extract_image',side_effect=PDFError(reason)):
                preview=self.store.documents.preview({'files':[self.item()]})
                self.assertEqual(preview['accepted'],0);self.assertEqual(preview['warnings'][0]['reason'],reason)
        value=extracted([])
        preview=self.preview(result=value);self.assertEqual(preview['accepted'],0);self.assertEqual(preview['warnings'][0]['reason'],'image_no_text')

    def test_cancel_preview_does_not_store_original_or_document(self):
        preview=self.preview(self.item(retainOriginal=True));self.store.documents.cancel(preview['id'])
        with self.assertRaises(StoreError):self.store.documents.commit(preview['id'])
        self.assertFalse((self.store.output/'originals').exists());self.assertEqual(self.store.documents.listing({})['count'],0)

    def test_backup_restores_image_original_text_and_provenance_and_rejects_legacy_label(self):
        self.store.documents.commit(self.preview(self.item(retainOriginal=True))['id']);source=self.store.documents.listing({})['items'][0]
        self.store.close();archive=self.root/'images.zip';summary=create_backup(self.root/'node',archive)
        with zipfile.ZipFile(archive) as z:entries={name:z.read(name) for name in z.namelist()}
        manifest=json.loads(entries['manifest.json']);self.assertEqual(manifest['version'],10)
        target=self.root/'restored';restore_backup(archive,target,summary['archiveSHA256'],self.root);restored=Store(target)
        try:
            actual=restored.documents.listing({})['items'][0]
            self.assertEqual(actual['documentInfo'],source['documentInfo'])
            self.assertEqual(base64.b64decode(restored.documents.original(source['id'])['data']),b'synthetic image')
        finally:restored.close()
        manifest['version']=9;entries['manifest.json']=json.dumps(manifest).encode();legacy=self.root/'mislabeled.zip'
        with zipfile.ZipFile(legacy,'w') as z:
            for name,raw in entries.items():z.writestr(name,raw)
        with self.assertRaises(BackupError):inspect_backup(legacy,self.root)
        self.store=Store(self.root/'node')

    def test_jpeg_extension_aliases_do_not_duplicate_the_same_original(self):
        result=extracted();result['containerType']='public.jpeg'
        first=self.preview(self.item(name='orchard.jpg',retainOriginal=True),result);self.store.documents.commit(first['id'])
        second=self.preview(self.item(name='renamed.jpeg',retainOriginal=True),result)
        self.assertEqual((second['added'],second['duplicates']),(0,1))
        self.store.documents.commit(second['id']);self.assertEqual(self.store.documents.listing({})['count'],1)

    def test_image_original_above_document_limit_is_retained_without_raising_pdf_limit(self):
        raw=b'synthetic image bytes'*(3*1024*1024//21+1)
        self.assertGreater(len(raw),2*1024*1024)
        value=self.preview(self.item(data=base64.b64encode(raw).decode(),retainOriginal=True))
        self.store.documents.commit(value['id']);rid=self.store.documents.listing({})['items'][0]['id']
        self.assertEqual(base64.b64decode(self.store.documents.original(rid)['data']),raw)
        from document_originals import original_metadata, OriginalError
        with self.assertRaises(OriginalError):original_metadata(rid,'pdf',{'originalFileSHA256':hashlib.sha256(raw).hexdigest(),'original':{'sha256':hashlib.sha256(raw).hexdigest(),'byteCount':len(raw)}})

    def test_correction_preserves_original_history_and_prior_memory_evidence(self):
        self.store.documents.commit(self.preview(self.item(retainOriginal=True))['id'])
        source=self.store.documents.listing({})['items'][0];rid=source['id'];path='documents/'+rid+'.md'
        full=self.store.read_document(path);line=source['documentInfo']['imageLines'][1]
        candidate=self.store.memories.add('Synthetic weekly review.',evidence=[{'kind':'document','id':rid,
            'contentVersion':source['contentVersion'],'quote':full['text'][line['start']:line['end']],'quoteStart':line['start']}])
        before=[tuple(r) for r in self.store.db.execute('SELECT * FROM memory_details')]
        editor=self.store.documents.text_document(rid);self.assertNotIn('作者：',editor['text'])
        revised='Corrected synthetic Friday review.\n'
        result=self.store.documents.text_document(rid,{'text':revised,'etag':editor['etag'],'nodeID':self.store.setting('nodeID')})
        self.assertEqual(result['text'],revised)
        self.assertIn(full['text'],[f.read_text() for f in (self.store.output/'.history/documents'/(rid+'.md')).iterdir()])
        self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT * FROM memory_details')],before)
        self.assertEqual(base64.b64decode(self.store.documents.original(rid)['data']),b'synthetic image')
        self.assertFalse(self.store.documents.listing({})['items'][0]['documentInfo']['rangesValid'])
        self.store.documents.commit(self.preview(self.item(retainOriginal=True))['id'])
        self.assertEqual(self.store.documents.text_document(rid)['text'],revised)

    def test_correction_rejects_wrong_library_stale_version_and_changed_header(self):
        self.store.documents.commit(self.preview()['id']);rid=self.store.documents.listing({})['items'][0]['id'];path='documents/'+rid+'.md'
        initial=self.store.read_document(path);value=self.store.documents.text_document(rid)
        for body in [{'text':'new','etag':value['etag'],'nodeID':'other'},{'text':'new','etag':'stale','nodeID':self.store.setting('nodeID')}]:
            with self.assertRaises(StoreError):self.store.documents.text_document(rid,body)
        self.assertEqual(self.store.read_document(path),initial)
        modified='Manual preface\n'+initial['text'];(self.store.output/path).write_text(modified)
        with self.assertRaises(StoreError):self.store.documents.text_document(rid)
        self.assertEqual((self.store.output/path).read_text(),modified)

    def test_correction_device_api_does_not_expand_readonly_access(self):
        self.store.documents.commit(self.preview()['id']);rid=self.store.documents.listing({})['items'][0]['id']
        grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-image-token');worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def call(method,body=None,token=server.token):
            c=http.client.HTTPConnection(*server.server_address,timeout=10);c.request(method,'/v1/documents/'+rid+'/text',json.dumps(body) if body else None,{'Authorization':'Bearer '+token});r=c.getresponse();value=json.loads(r.read());c.close();return r.status,value
        try:
            self.assertEqual(call('GET',token=grant['token'])[0],401)
            status,value=call('GET');self.assertEqual(status,200)
            self.assertEqual(call('PUT',{'text':'Corrected text.','etag':value['etag']})[0],409)
            self.assertEqual(call('PUT',{'text':'Corrected text.','etag':value['etag'],'nodeID':self.store.setting('nodeID')})[0],200)
        finally:server.shutdown();server.server_close();worker.join()

    def test_container_integrity_rejects_truncated_and_crc_changed_sources_before_decoding(self):
        from image_documents import validate_container
        for raw,extension in [(b'\xff\xd8truncated','jpg'),(b'\x89PNG\r\n\x1a\n','png'),(b'\x00\x00\x00\x20ftyptruncated','heic')]:
            with self.subTest(extension=extension),self.assertRaises(PDFError):validate_container(raw,extension)

    def test_parser_contract_is_bounded_and_rejects_missing_or_inconsistent_metadata(self):
        bad=[]
        for change in [{'format':'pdf'},{'width':True},{'orientation':9},{'recognizedWidth':2401},{'lines':['a\nb']},{'containerType':'public.jpeg'},{'lines':[]}]:
            bad.append({**extracted(),**change})
        for result in bad:
            with self.subTest(change=result),patch('image_documents.image_ocr_available',return_value=True),patch('image_documents.validate_container'),patch('image_documents.worker_command',return_value=['owned-worker','input','output']),patch('image_documents.supervise',return_value=result):
                with self.assertRaises(PDFError):extract_image(b'image','png',self.root/'scratch')
        with patch('image_documents.image_ocr_available',return_value=True):
            with self.assertRaisesRegex(PDFError,'image_too_large'):extract_image(b'x'*(MAX_IMAGE+1),'png',self.root/'scratch')
        with patch('image_documents.image_ocr_available',return_value=False):
            with self.assertRaisesRegex(PDFError,'ocr_unavailable'):extract_image(b'image','png',self.root/'scratch')
        self.assertEqual(list((self.root/'scratch').iterdir()),[])


@unittest.skipUnless(sys.platform=='darwin','Image OCR uses Apple Vision on macOS')
class ImageOCRNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        subprocess.run(['xcrun','swiftc',str(Path(__file__).parent/'fixtures/ocr_images.swift'),'-o',str(cls.root/'make-images')],check=True,capture_output=True,timeout=60)
        subprocess.run([str(cls.root/'make-images'),str(cls.root/'images')],check=True,capture_output=True,timeout=20)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def parse(self,name):return extract_image((self.root/'images'/name).read_bytes(),Path(name).suffix[1:],self.root/'scratch',seconds=60)
    def test_real_png_jpeg_heic_and_orientation_metadata(self):
        for name in ['orchard.png','orchard.jpg','rotated.jpg']:
            result=self.parse(name);self.assertIn('Synthetic orchard review','\n'.join(result['lines']))
            self.assertEqual((result['recognizedWidth'],result['recognizedHeight']),(900,1200))
        self.assertEqual(self.parse('rotated.jpg')['orientation'],6)
        self.assertIn('合成资料核对','\n'.join(self.parse('chinese.heic')['lines']))
        alias=extract_image((self.root/'images/chinese.heic').read_bytes(),'heif',self.root/'scratch')
        self.assertIn('合成资料核对','\n'.join(alias['lines']))
    def test_real_blank_multiframe_and_truncated_images(self):
        self.assertEqual(self.parse('blank.png')['status'],'no_text')
        with self.assertRaisesRegex(PDFError,'image_frames'):self.parse('two-images.heic')
        png=bytearray((self.root/'images/orchard.png').read_bytes());png[29]^=1
        with self.assertRaisesRegex(PDFError,'invalid_image'):extract_image(bytes(png),'png',self.root/'scratch')
        raw=(self.root/'images/orchard.jpg').read_bytes()
        with self.assertRaises(PDFError):extract_image(raw[:len(raw)//2],'jpg',self.root/'scratch')

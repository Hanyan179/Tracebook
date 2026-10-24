import http.client
import json
import threading
import unittest
import zipfile
from unittest.mock import patch
import test_node as fixtures
from store import Store, StoreError
from server import Server


class DocumentImportTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown

    def item(self, text='Synthetic document orchard plan.', name='journal.md'):
        return {'name':name,'sourcePath':'/synthetic/documents/'+name,'modifiedAt':'2026-10-20T16:30:00Z','text':text}

    def import_files(self, files):
        preview=self.store.documents.preview({'files':files})
        return preview,self.store.documents.commit(preview['id'])

    def test_preview_commit_duplicate_and_cancel_are_explicit(self):
        preview=self.store.documents.preview({'files':[self.item(),self.item(name='copy.txt')]})
        self.assertEqual(preview['added'],1);self.assertEqual(preview['duplicates'],1)
        self.assertEqual(self.store.documents.listing({})['count'],0)
        self.assertFalse(list((self.store.output/'documents').glob('*.md')))
        report=self.store.documents.commit(preview['id'])
        self.assertEqual(report['added'],1)
        self.assertEqual(report['skipped'],1)
        self.assertEqual(self.store.documents.commit(preview['id']),report)
        self.assertEqual(self.import_files([self.item()])[1]['skipped'],1)
        cancelled=self.store.documents.preview({'files':[self.item('Discard this preview')]})
        self.store.documents.cancel(cancelled['id'])
        self.assertFalse((self.store.data/'document-previews'/(cancelled['id']+'.json')).exists())
        self.assertEqual(self.store.documents.listing({})['count'],1)

    def test_reimport_preserves_manual_edit_and_changed_file_creates_version(self):
        self.import_files([self.item()])
        old=next((self.store.output/'documents').glob('*.md'))
        old.write_text('A human revision must remain.')
        self.assertEqual(self.import_files([self.item()])[1]['skipped'],1)
        self.assertEqual(old.read_text(),'A human revision must remain.')
        report=self.import_files([self.item('Updated orchard plan.')])[1]
        self.assertEqual(report['versions'],1);self.assertEqual(report['added'],1)
        new=next(p for p in (self.store.output/'documents').glob('*.md') if p!=old)
        self.assertIn(old.stem,new.read_text())
        self.assertEqual(old.read_text(),'A human revision must remain.')

    def test_documents_are_searchable_but_not_automatically_analyzed_or_granted(self):
        self.import_files([self.item()])
        results=self.store.knowledge.query({'query':'orchard','scopes':['documents']})
        self.assertEqual(len(results['sources']),1)
        item=results['sources'][0]
        self.assertEqual(item['kind'],'document')
        with self.assertRaises(StoreError):self.store.knowledge.read_source('document',item['id'],['notes'],{})
        text=self.store.knowledge.read_source('document',item['id'],['documents'],{})['text']
        self.assertIn('orchard',text);self.assertIn('作者：未确认',text)
        self.assertEqual(self.store.analysis.snapshot('2026-10-21')['sources'],[])
        self.assertEqual(self.store.candidates(),[])

    def test_bad_files_are_reported_and_known_credentials_do_not_leave_preview(self):
        files=[self.item('Private key sk-proj-abcdefghijklmnopqrstuv'),self.item('','empty.txt'),self.item('PDF data','paper.pdf'),self.item('x'*(2*1024*1024+1),'huge.txt')]
        preview,report=self.import_files(files)
        self.assertEqual(len(preview['warnings']),3)
        self.assertEqual(preview['redacted'],1);self.assertEqual(report['added'],1)
        self.assertNotIn('sk-proj-',json.dumps(preview))
        with zipfile.ZipFile(self.store.exchange.export()) as bundle:
            documents=[name for name in bundle.namelist() if name.startswith('documents/')]
            self.assertEqual(len(documents),1)
            for name in bundle.namelist():self.assertNotIn(b'sk-proj-',bundle.read(name))

    def test_output_move_restart_and_tampered_preview(self):
        self.import_files([self.item()])
        path=next((self.store.output/'documents').glob('*.md'));body=path.read_text()
        self.store.configure({'outputDirectory':str(self.root/'moved')})
        self.assertEqual((self.store.output/'documents'/path.name).read_text(),body)
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.documents.listing({})['count'],1)
        preview=self.store.documents.preview({'files':[self.item('Another document')]})
        (self.store.data/'document-previews'/(preview['id']+'.json')).write_text('[]')
        with self.assertRaises(StoreError):self.store.documents.commit(preview['id'])
        self.assertEqual(self.store.documents.listing({})['count'],1)

    def test_partial_write_can_retry_without_duplicate_files(self):
        preview=self.store.documents.preview({'files':[self.item(),self.item('Second document','two.txt')]})
        export=self.store.export;calls=[]
        def interrupted(*args,**kwargs):
            calls.append(args[0])
            if len(calls)==2:raise OSError('Synthetic storage failure')
            return export(*args,**kwargs)
        with patch.object(self.store,'export',side_effect=interrupted):
            with self.assertRaises(OSError):self.store.documents.commit(preview['id'])
        report=self.store.documents.commit(preview['id'])
        self.assertEqual(report['added'],1);self.assertEqual(report['skipped'],1)
        self.assertEqual(self.store.documents.listing({})['count'],2)

    def test_http_import_requires_local_auth_and_reads_actual_file(self):
        server=Server(('127.0.0.1',0),self.store,'document-fixture-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,values=None,authenticated=True):
            conn=http.client.HTTPConnection(*server.server_address,timeout=5)
            headers={'Content-Type':'application/json'}
            if authenticated:headers['Authorization']='Bearer '+server.token
            conn.request(method,path,json.dumps(values) if values is not None else None,headers)
            reply=conn.getresponse();code=reply.status;body=json.loads(reply.read());conn.close();return code,body
        try:
            self.assertEqual(request('GET','/v1/documents',authenticated=False)[0],401)
            with patch('server.ipaddress.ip_address') as address:
                address.return_value.is_loopback=False
                self.assertEqual(request('GET','/v1/documents')[0],403)
            code,preview=request('POST','/v1/documents/preview',{'files':[self.item()]})
            self.assertEqual(code,200)
            self.assertEqual(request('POST','/v1/documents/'+preview['id']+'/commit',{})[1]['added'],1)
            code,page=request('GET','/v1/documents');self.assertEqual(page['count'],1)
            code,source=request('GET','/v1/sources/document/'+page['items'][0]['id'])
            self.assertEqual(code,200);self.assertIn('orchard',source['text'])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

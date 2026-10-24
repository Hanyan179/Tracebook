import http.client
import base64
import hashlib
import json
import threading
import unittest
import uuid
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

    def test_search_skips_only_generated_header_and_preserves_file_bytes(self):
        self.import_files([self.item('The orchard has a Friday review.', 'orchard.md')])
        path=next((self.store.output/'documents').glob('*.md'));body=path.read_text()
        expected=('# orchard.md\n\n来源：导入资料\n格式：md\n文件修改时间：2026-10-20T16:30:00+00:00\n'
                  '作者：未确认。资料内容不自动成为本人事实或长期记忆。\n\n---\n\nThe orchard has a Friday review.\n')
        self.assertEqual(body,expected)
        source=self.store.knowledge.query({'query':'orchard','scopes':['documents']})['sources'][0]
        self.assertTrue(source['excerpt'].startswith('The orchard'))
        self.assertEqual(body[source['excerptStart']:source['excerptStart']+len(source['excerpt'])],source['excerpt'])
        self.assertEqual(source['contentVersion'],hashlib.sha256(body.encode()).hexdigest())
        self.assertEqual(self.store.answers.checked_source(source)['excerpt'],source['excerpt'])
        self.assertEqual(self.store.knowledge.query({'query':'作者','scopes':['documents']})['sources'],[])
        self.assertEqual(self.store.knowledge.query({'query':'文件修改时间','scopes':['documents']})['sources'],[])
        self.assertEqual(path.read_text(),expected)

    def test_filename_match_shows_body_and_keeps_original_markdown_metadata(self):
        text='---\nauthor: A fictional author\n---\n# Original heading\n作者：合成作者。\n来源：正文中的说明。'
        item=self.item(text,'quartz-index.md');item['contentRole']='generated';item.pop('modifiedAt')
        self.import_files([item])
        source=self.store.knowledge.query({'query':'quartz-index','scopes':['documents']})['sources'][0]
        self.assertEqual(source['excerpt'],text)
        self.assertEqual(source['evidenceType'],'model_generated_document')
        self.assertEqual(source['contentRole'],'generated')
        self.assertIn('正文中的说明',self.store.knowledge.query({'query':'正文中的说明','scopes':['documents']})['sources'][0]['excerpt'])
        # Matching the body must not make this source eligible as personal memory evidence.
        self.assertEqual(self.store.candidates(),[])

    def test_changed_or_prepended_header_is_not_hidden_as_generated_content(self):
        self.import_files([self.item('Orchard body.')]);path=next((self.store.output/'documents').glob('*.md'));original=path.read_text()
        for body,query in [('Human prefix correction\n'+original,'correction'),
                           (original.replace('作者：未确认','作者：人工修正'),'人工修正')]:
            path.write_text(body);self.store.catalog.index_document(path.stem)
            row=self.store.db.execute("SELECT data FROM library_items WHERE kind='document' AND id=?",(path.stem,)).fetchone()
            self.assertEqual(json.loads(row['data'])['contentStart'],0)
            source=self.store.knowledge.query({'query':query,'scopes':['documents']})['sources'][0]
            self.assertIn(query,source['excerpt'])
            self.assertEqual(body[source['excerptStart']:source['excerptStart']+len(source['excerpt'])],source['excerpt'])
            self.assertEqual(path.read_text(),body)

    def test_legacy_index_is_rebuilt_without_changing_sources_or_saved_turns(self):
        self.import_files([self.item('Orchard content stays unchanged.','orchard.md')]);path=next((self.store.output/'documents').glob('*.md'));body=path.read_text()
        chat=self.store.knowledge.create_conversation();request={'requestID':str(uuid.uuid4()),'query':'orchard','scopes':['documents']}
        turn=self.store.knowledge.ask(chat['id'],request)
        # A persisted turn from an older release included the archive prefix.
        old_response=turn['response'];old_response['sources'][0].update(excerpt=body,excerptStart=0,excerptTruncated=False)
        row=self.store.db.execute("SELECT data FROM library_items WHERE kind='document' AND id=?",(path.stem,)).fetchone()
        data=json.loads(row['data']);data.pop('contentStart',None)
        with self.store.db:
            self.store.db.execute('UPDATE conversation_turns SET response=? WHERE id=?',(json.dumps(old_response),turn['id']))
            self.store.db.execute("UPDATE library_items SET data=?,search_text=? WHERE kind='document' AND id=?",(json.dumps(data,ensure_ascii=False,sort_keys=True),body,path.stem))
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(path.read_text(),body)
        self.assertEqual(self.store.knowledge.ask(chat['id'],request)['response'],old_response)
        source=self.store.knowledge.query({'query':'orchard','scopes':['documents']})['sources'][0]
        self.assertTrue(source['excerpt'].startswith('Orchard content'))
        self.assertEqual(self.store.knowledge.query({'query':'作者','scopes':['documents']})['sources'],[])

    def test_binary_document_passages_keep_unicode_positions_and_source_coverage(self):
        from test_docx_documents import package,paragraph
        from test_pdf_documents import pdf_bytes
        text='Orchard ＰＬＡＮＮＩＮＧ café 👩🏽‍💻 synthetic review.'
        for extension,raw in [('pdf',pdf_bytes([text])),('docx',package(paragraph(text)))]:
            with self.subTest(extension=extension):
                item=self.item(name='orchard.'+extension);item.pop('text');item['data']=base64.b64encode(raw).decode()
                self.import_files([item])
                source=next(s for s in self.store.knowledge.query({'query':'planning','scopes':['documents']})['sources'] if s['title']==item['name'])
                body=self.store.read_document('documents/'+source['id']+'.md')['text']
                self.assertIn('ＰＬＡＮＮＩＮＧ',source['excerpt']);self.assertNotIn('来源：导入资料',source['excerpt'])
                self.assertEqual(body[source['excerptStart']:source['excerptStart']+len(source['excerpt'])],source['excerpt'])
                self.assertTrue(source['documentInfo']['rangesValid'])
                self.assertEqual(self.store.answers.checked_source(source)['excerptStart'],source['excerptStart'])

    def test_version_and_manual_body_edits_keep_content_positions_valid_for_retrieval(self):
        item=self.item('Earlier orchard material.','orchard.md');item['contentRole']='transcript'
        self.import_files([item]);item['text']='Updated orchard material.';self.import_files([item])
        rows=self.store.documents.listing({})['items'];new=next(r for r in rows if r['documentRevision']['previousID'])
        path=self.store.output/'documents'/(new['id']+'.md');body=path.read_text()+'\nManual sapphire addition é 👩🏽‍💻.\n';path.write_text(body)
        self.store.catalog.index_document(new['id'])
        source=self.store.knowledge.query({'query':'sapphire','scopes':['documents']})['sources'][0]
        self.assertEqual(source['id'],new['id']);self.assertNotIn('此前导入版本',source['excerpt'])
        self.assertEqual(body[source['excerptStart']:source['excerptStart']+len(source['excerpt'])],source['excerpt'])
        self.assertEqual(source['contentVersion'],hashlib.sha256(body.encode()).hexdigest())

    def test_header_without_remaining_body_does_not_return_an_empty_passage(self):
        self.import_files([self.item('Orchard text.','orchard.md')]);path=next((self.store.output/'documents').glob('*.md'))
        body=path.read_text();path.write_text(body[:body.index('Orchard text.')]);self.store.catalog.index_document(path.stem)
        result=self.store.knowledge.query({'query':'orchard','scopes':['documents']})
        self.assertEqual(result['sources'],[]);self.assertFalse(result['coverage']['hasMore'])

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

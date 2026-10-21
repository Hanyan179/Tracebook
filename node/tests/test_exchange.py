import datetime as dt
import io
import http.client
import json
import unittest
import threading
import uuid
import zipfile
import test_node as fixtures
from store import StoreError
from server import Server

class ExchangeTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown
    def payload(self,text='Synthetic user plan',extra=False):
        cid='synthetic-conversation'
        mapping={'root':{'parent':None,'message':None},
            'user':{'parent':'root','message':{'author':{'role':'user'},'create_time':1760000000,'content':{'parts':[text]}}},
            'assistant':{'parent':'user','message':{'author':{'role':'assistant'},'create_time':1760000100,'content':{'parts':['Synthetic assistant suggestion']}}},
            'other':{'parent':'user','message':{'author':{'role':'assistant'},'create_time':1760000090,'content':{'parts':['Other branch']}}}}
        if extra:mapping['image']={'parent':'assistant','message':{'author':{'role':'user'},'create_time':1760000200,'content':{'parts':[{'content_type':'image_asset_pointer'}]}}}
        return json.dumps([{'id':cid,'title':'Synthetic chat','mapping':mapping,'current_node':'image' if extra else 'assistant'}]).encode()
    def test_preview_has_no_notes_commit_is_idempotent_and_retains_roles(self):
        preview=self.store.exchange.preview(self.payload(extra=True))
        self.assertEqual(self.store.catalog.query('note',{})['total'],0)
        self.assertEqual(preview['otherBranchMessages'],1);self.assertEqual(preview['nonTextMessages'],1)
        first=self.store.exchange.commit(preview['id']);again=self.store.exchange.commit(preview['id'])
        self.assertEqual(first,again);self.assertEqual(first['added'],1)
        note=self.store.catalog.query('note',{})['items'][0]
        text=self.store.knowledge.note(note['id'])['text']
        self.assertIn('### 用户',text);self.assertIn('### 助手',text);self.assertNotIn('Other branch',text)
        self.assertNotEqual(note['day'],dt.datetime.now().date().isoformat())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],0)
        self.assertTrue(self.store.exchange.preview(self.payload(extra=True))['alreadyImported'])
    def test_changed_export_updates_unedited_note_but_preserves_human_edits(self):
        preview=self.store.exchange.preview(self.payload());self.store.exchange.commit(preview['id'])
        item=self.store.catalog.query('note',{})['items'][0]
        changed=self.store.exchange.preview(self.payload('Updated synthetic plan'))
        self.assertEqual(self.store.exchange.commit(changed['id'])['updated'],1)
        note=self.store.knowledge.note(item['id'])
        self.store.knowledge.save_note({'title':note['title'],'text':'Human edit','etag':note['etag']},note['id'])
        changed=self.store.exchange.preview(self.payload('Third source version'))
        self.assertEqual(self.store.exchange.commit(changed['id'])['conflicts'],1)
        self.assertEqual(self.store.knowledge.note(item['id'])['text'],'Human edit')
        self.assertTrue(list((self.store.output/'.generated/imports').glob('*.md')))
    def test_zip_is_read_without_extracting_members_and_cancel_removes_preview(self):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('../../conversations.json',self.payload())
            archive.writestr('../../outside-secret','should never be extracted')
        preview=self.store.exchange.preview(buffer.getvalue())
        self.store.exchange.cancel(preview['id'])
        self.assertFalse((self.store.data/'imports'/(preview['id']+'.json')).exists())
        self.assertFalse((self.root/'outside-secret').exists())
    def test_corrupt_branch_rejected_without_partial_import(self):
        payload=json.loads(self.payload());payload[0]['mapping']['user']['parent']='assistant'
        with self.assertRaises(StoreError):self.store.exchange.preview(json.dumps(payload).encode())
        self.assertEqual(self.store.catalog.query('note',{})['total'],0)
    def test_malformed_message_fields_return_controlled_errors(self):
        for field in ('current_node','parent','author','content'):
            payload=json.loads(self.payload())
            if field=='current_node':payload[0][field]=['invalid']
            elif field=='parent':payload[0]['mapping']['user'][field]=['invalid']
            else:payload[0]['mapping']['user']['message'][field]=['invalid']
            with self.subTest(field=field),self.assertRaises(StoreError):
                self.store.exchange.preview(json.dumps(payload).encode())
        self.assertEqual(self.store.catalog.query('note',{})['total'],0)
    def test_preview_dates_use_library_timezone(self):
        self.store.configure_daily({'enabled':True,'time':'00:15','timeZone':'Asia/Shanghai'})
        payload=json.loads(self.payload())
        stamp=dt.datetime(2026,10,20,23,30,tzinfo=dt.timezone.utc).timestamp()
        for node in payload[0]['mapping'].values():
            if node.get('message'):node['message']['create_time']=stamp
        report=self.store.exchange.preview(json.dumps(payload).encode())
        self.assertEqual(report['firstDay'],'2026-10-21')
        self.assertEqual(report['lastDay'],'2026-10-21')
    def test_portable_export_keeps_history_and_excludes_credentials(self):
        preview=self.store.exchange.preview(self.payload());self.store.exchange.commit(preview['id'])
        chat=self.store.knowledge.create_conversation()
        self.store.knowledge.ask(chat['id'],{'requestID':str(uuid.uuid4()),'query':'Synthetic'})
        grant=self.store.knowledge.create_grant({'name':'isolated','scopes':['notes'],'days':1})
        path=self.store.exchange.export()
        try:
            with zipfile.ZipFile(path) as archive:
                names=archive.namelist()
                self.assertIn('conversations.json',names);self.assertIn('manifest.json',names)
                self.assertTrue(any(n.startswith('original-imports/') for n in names))
                self.assertFalse(any('sqlite' in n or 'token' in n or '.caf' in n for n in names))
                joined=b''.join(archive.read(n) for n in names)
                self.assertNotIn(grant['token'].encode(),joined)
                manifest=json.loads(archive.read('manifest.json'));self.assertFalse(manifest['includesAudio'])
                self.assertEqual(len(json.loads(archive.read('conversations.json'))[0]['turns']),1)
        finally:path.unlink()
    def test_http_import_export_and_reader_boundary(self):
        grant=self.store.knowledge.create_grant({'name':'isolated reader','scopes':['notes'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'isolated-admin')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def call(method,path,body=None,token='isolated-admin'):
            connection=http.client.HTTPConnection(*server.server_address,timeout=5)
            connection.request(method,path,body,{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response=connection.getresponse();status=response.status;data=response.read();connection.close()
            return status,data
        try:
            for method,path,body in [('POST','/v1/imports/preview',self.payload()),
                                     ('GET','/v1/export',None),('POST','/v1/model-session',b'{}')]:
                self.assertEqual(call(method,path,body,grant['token'])[0],401)
            status,data=call('POST','/v1/imports/preview',self.payload());self.assertEqual(status,200)
            preview=json.loads(data)
            status,data=call('POST','/v1/imports/'+preview['id']+'/commit',b'{}')
            self.assertEqual(status,200);self.assertEqual(json.loads(data)['added'],1)
            status,data=call('GET','/v1/export');self.assertEqual(status,200)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(len(json.loads(archive.read('manifest.json'))['notes']),1)
            self.assertFalse(list(self.store.staging.glob('library-export-*')))
        finally:server.shutdown();server.server_close();worker.join()

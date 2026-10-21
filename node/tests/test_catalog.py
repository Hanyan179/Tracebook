import datetime as dt
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.parse
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_node as fixtures
from store import Store,StoreError
from server import Server
from catalog import normalized

class CatalogTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown
    fixture=fixtures.NodeTests.fixture
    receive=fixtures.NodeTests.receive
    def seed(self,count):
        items=[];search=[]
        for i in range(count):
            rid=str(uuid.uuid5(uuid.NAMESPACE_DNS,'voice-memory-catalog-'+str(i))).upper()
            date=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)+dt.timedelta(hours=i)
            title='演示资料 '+str(i);body='季度项目复盘 中文搜索 '+str(i)
            data={'id':rid,'title':title,'startedAt':date.isoformat(),'duration':2,'byteCount':100,'sourceDevice':'phone','audioSHA256':'a'*64,'textState':'ready'}
            text=normalized(title+'\n'+body+'\n')
            items.append(('recording',rid,title,body,date.timestamp(),date.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat(),'ready',json.dumps(data),'fixture',text))
            search.append(('recording:'+rid,text))
        with self.store.db:
            self.store.db.executemany('INSERT INTO library_items(kind,id,title,body,sort_date,day,state,data,mtime,search_text) VALUES (?,?,?,?,?,?,?,?,?,?)',items)
            if self.store.catalog.trigram:self.store.db.executemany('INSERT INTO library_search(entity,body) VALUES (?,?)',search)

    def test_existing_recordings_migrate_without_rewriting_audio_or_text(self):
        rid,_,audio=self.receive();old=audio.read_bytes()
        doc=self.store.read_document('transcripts/'+rid+'.md')
        self.store.save_transcript(rid,'旧资料中的中文内容',doc['etag'])
        with self.store.db:
            for table in ['library_items','library_categories','library_tags','library_search']:self.store.db.execute('DROP TABLE IF EXISTS '+table)
        self.store.close();self.store=Store(self.root/'node')
        page=self.store.catalog.query('recording',{'q':'中文内容'})
        self.assertEqual(page['total'],1)
        self.assertEqual(page['items'][0]['organization']['revision'],0)
        self.assertEqual(self.store.audio_path(rid).read_bytes(),old)
        self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'],'旧资料中的中文内容')

    def test_categories_tags_revision_and_archive_round_trip(self):
        rid,_,audio=self.receive();before=audio.read_bytes()
        c=self.store.catalog.save_category('recording','项目甲')
        result=self.store.catalog.update('recording',rid,{'revision':0,'categoryID':c['id'],'tags':['产品','Product','product'],'archived':False})
        self.assertEqual(result['organization']['tags'],['产品','Product'])
        self.assertEqual(self.store.catalog.query('recording',{'category':c['id'],'tag':'PRODUCT'})['total'],1)
        with self.assertRaises(StoreError) as stale:self.store.catalog.update('recording',rid,{'revision':0,'tags':['过期']})
        self.assertEqual(stale.exception.status,409)
        self.store.catalog.update('recording',rid,{'revision':1,'archived':True})
        self.assertEqual(self.store.catalog.query('recording',{})['total'],0)
        self.assertEqual(self.store.catalog.query('recording',{'archived':'true'})['total'],1)
        self.store.catalog.update('recording',rid,{'revision':2,'archived':False})
        self.assertEqual(self.store.audio_path(rid).read_bytes(),before)
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.catalog.detail('recording',rid)['organization']['categoryID'],c['id'])
        self.assertTrue((self.store.output/'organization/recording'/(rid+'.json')).exists())

    def test_category_names_are_scoped_and_references_survive_rename(self):
        rid,_,_=self.receive();c=self.store.catalog.save_category('recording','Focus')
        self.store.catalog.update('recording',rid,{'revision':0,'categoryID':c['id']})
        with self.assertRaises(StoreError):self.store.catalog.save_category('recording','Ｆｏｃｕｓ')
        other=self.store.catalog.save_category('memory','Focus')
        with self.assertRaises(StoreError):self.store.catalog.update('recording',rid,{'revision':1,'categoryID':other['id']})
        self.store.catalog.save_category('recording','专题',c['id'],1)
        self.assertEqual(self.store.catalog.query('recording',{'category':c['id']})['total'],1)
        self.assertEqual(next(x for x in self.store.catalog.categories('recording') if x['id']==c['id'])['name'],'专题')

    def test_memory_grouping_archive_and_manual_file_protection(self):
        rid,_,_=self.receive();cid=self.store.add_candidate('合成偏好：喜欢简短记录',[rid])['id']
        self.store.catalog.update('memory',cid,{'revision':0,'categoryID':'memory.preferences','tags':['演示']})
        self.assertNotIn('喜欢简短记录',self.store.read_document('memory.md')['text'])
        self.store.decide_candidate(cid,'confirmed')
        text=self.store.read_document('memory.md')['text']
        self.assertIn('## 偏好习惯',text);self.assertIn(rid,text)
        self.store.catalog.update('memory',cid,{'revision':1,'archived':True})
        self.assertNotIn('喜欢简短记录',self.store.read_document('memory.md')['text'])
        self.assertIn('喜欢简短记录',self.store.read_document('memory-archive.md')['text'])
        self.assertIn('确认状态：已确认',self.store.read_document('memory-archive.md')['text'])
        pending=self.store.add_candidate('合成的未确认内容',[rid])['id']
        self.store.catalog.update('memory',pending,{'revision':0,'archived':True})
        self.assertEqual(self.store.catalog.detail('memory',pending)['state'],'pending')
        self.assertIn('确认状态：待确认',self.store.read_document('memory-archive.md')['text'])
        self.assertNotIn('合成的未确认内容',self.store.read_document('memory.md')['text'])
        (self.store.output/'memory.md').write_text('人工保护内容')
        self.store.catalog.update('memory',cid,{'revision':2,'archived':False})
        self.assertEqual(self.store.read_document('memory.md')['text'],'人工保护内容')
        self.assertTrue(any(x['path']=='memory.md' for x in self.store.status()['conflicts']))

    def test_keyset_pagination_survives_new_insertion_and_filter_change_is_rejected(self):
        self.seed(95)
        first=self.store.catalog.query('recording',{'limit':'40'})
        rid,_,_=self.receive('2026-10-20T00:00:00Z')
        second=self.store.catalog.query('recording',{'limit':'40','cursor':first['nextCursor']})
        third=self.store.catalog.query('recording',{'limit':'40','cursor':second['nextCursor']})
        ids=[r['id'] for p in [first,second,third] for r in p['items']]
        self.assertEqual(len(ids),95);self.assertEqual(len(set(ids)),95);self.assertNotIn(rid,ids)
        with self.assertRaises(StoreError):self.store.catalog.query('recording',{'q':'别的筛选','cursor':first['nextCursor']})

    def test_large_catalog_returns_bounded_pages_and_global_statistics(self):
        self.seed(10000)
        page=self.store.catalog.query('recording',{'q':'中文搜索'})
        self.assertEqual(page['total'],10000);self.assertEqual(len(page['items']),40)
        self.assertLess(len(json.dumps(page).encode()),100000)
        self.assertEqual(self.store.catalog.query('recording',{'q':'中文'})['total'],10000)
        self.assertEqual(self.store.catalog.query('recording',{'q':'" OR *'})['total'],0)
        overview=self.store.catalog.overview()
        self.assertEqual(overview['recordingCount'],10000);self.assertEqual(overview['duration'],20000)
        self.assertGreater(len(self.store.catalog.facets('recording')['months']),10)

    def test_external_transcript_edits_are_reconciled_and_daily_searchable(self):
        rid,_,_=self.receive();path=self.store.output/'transcripts'/(rid+'.md')
        path.write_text('外部修改的中文资料')
        self.store.catalog.reconcile()
        self.assertEqual(self.store.catalog.query('recording',{'q':'外部修改'})['total'],1)
        self.store.build_daily('2026-10-19')
        page=self.store.catalog.query('daily',{'q':'中文资料','month':'2026-10'})
        self.assertEqual(page['total'],1);self.assertEqual(page['items'][0]['recordingCount'],1)

    def test_day_sources_include_archived_records_without_other_dates(self):
        rid,_,_=self.receive('2026-10-19T04:00:00Z')
        self.receive('2026-10-18T04:00:00Z')
        self.store.catalog.update('recording',rid,{'revision':0,'archived':True})
        page=self.store.catalog.query('recording',{'day':'2026-10-19','archived':'all'})
        self.assertEqual(page['total'],1);self.assertEqual(page['items'][0]['id'],rid)
        with self.assertRaises(StoreError):self.store.catalog.query('recording',{'day':'invalid'})

    def test_http_query_encoding_and_metadata_updates(self):
        rid,_,_=self.receive();server=Server(('127.0.0.1',0),self.store,'catalog-test-only')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection(*server.server_address,timeout=5);h={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'}
            conn.request('GET','/v1/library/recording?'+urllib.parse.urlencode({'q':'测试','limit':1}),headers=h)
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['total'],1)
            conn.request('PUT','/v1/library/recording/'+rid,body=json.dumps({'revision':0,'tags':['集成']}),headers=h)
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['organization']['tags'],['集成'])
            conn.request('GET','/v1/library/recording?'+urllib.parse.urlencode({'tag':'集成'}),headers=h)
            response=conn.getresponse();self.assertEqual(json.loads(response.read())['total'],1);conn.close()
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()

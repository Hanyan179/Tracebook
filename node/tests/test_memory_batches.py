import http.client
import json
import threading
import unittest
import uuid
from unittest.mock import patch
import test_memories as fixtures
from store import Store, StoreError
from server import Server


class MemoryBatchTests(unittest.TestCase):
    setUp=fixtures.MemoryTests.setUp
    tearDown=fixtures.MemoryTests.tearDown
    note=fixtures.MemoryTests.note
    receive=fixtures.MemoryTests.receive
    fixture=fixtures.MemoryTests.fixture
    text_etag=fixtures.MemoryTests.text_etag
    folder=fixtures.MemoryTests.folder
    write=fixtures.MemoryTests.write
    meta=fixtures.MemoryTests.meta
    user=fixtures.MemoryTests.user
    collect=fixtures.MemoryTests.collect

    def refs(self,count=2):
        return [{'kind':'note','id':self.note('Synthetic personal statement number '+str(i)+'.')['id']} for i in range(count)]

    def configure(self):
        self.store.analysis.configure({'endpoint':'https://batch.invalid/v1','model':'synthetic-batch','key':'synthetic-batch-secret-not-for-files'})

    def request(self,refs):
        plan=self.store.memory_batches.preview(refs)
        return {'sources':refs,'snapshot':plan['snapshot'],'sendToModel':True,'requestID':str(uuid.uuid4())}

    def response(self,config,rules,payload):
        return {'candidates':[{'text':'Synthetic proposal for '+payload['id'],'quote':payload['text'],'subject':'user','basis':'explicit'}]}

    def resume(self,key):
        return self.store.memory_batches.resume(key,{'sendToModel':True,'model':'synthetic-batch','endpoint':'https://batch.invalid/v1'},background=False)

    def test_preview_is_read_only_and_missing_config_is_clear(self):
        refs=self.refs()
        with patch.object(self.store.analysis,'request_json') as call:
            plan=self.store.memory_batches.preview(refs)
            self.assertFalse(plan['configured']);self.assertEqual(plan['newRequests'],2)
            with self.assertRaises(StoreError):self.store.memory_batches.start(self.request(refs),background=False)
            call.assert_not_called()
        self.assertEqual(self.store.candidates(),[])
        self.assertEqual(self.store.memory_batches.listing()['items'],[])

    def test_all_four_source_kinds_use_the_same_explicit_batch(self):
        rid,_,_=self.receive();self.store.save_transcript(rid,'Synthetic transcript: I prefer weekly reviews.',self.text_etag(rid))
        preview=self.store.documents.preview({'files':[{'name':'synthetic.txt','text':'Synthetic author text: I prefer tea.'}]})
        self.store.documents.commit(preview['id']);doc=self.store.db.execute('SELECT id FROM documents').fetchone()[0]
        root,path=self.folder();self.write(path,[self.meta(),self.user('Synthetic local input: I prefer morning reviews.')]);self.collect('codex',root)
        inp=self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        refs=[{'kind':kind,'id':identifier} for kind,identifier in [('recording',rid),('document',doc),('input',inp),('note',self.note()['id'])]]
        self.configure()
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
            result=self.store.memory_batches.start(self.request(refs),background=False)
        self.assertEqual(result['status'],'ready');self.assertEqual(call.call_count,4)
        self.assertEqual({c.args[2]['kind'] for c in call.call_args_list},{'recording','document','input','note'})
        self.assertEqual({self.store.memories.detail(row['id'])['evidence'][0]['kind'] for row in self.store.candidates()}, {'recording','document','input','note'})

    def test_only_selected_sources_sent_and_identical_requests_reuse_pending_results(self):
        refs=self.refs();self.note('UNSELECTED: never send this source.');self.configure();request=self.request(refs)
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
            result=self.store.memory_batches.start(request,background=False)
            self.assertEqual(result['status'],'ready');self.assertEqual(call.call_count,2)
            self.assertEqual([c.args[2]['id'] for c in call.call_args_list],[r['id'] for r in refs])
            self.assertEqual(self.store.memory_batches.start(request,background=False)['id'],result['id'])
            other=self.store.memory_batches.start(self.request(refs),background=False)
            self.assertEqual(other['candidateCount'],2);self.assertEqual(call.call_count,2)
        self.assertEqual(len(self.store.candidates()),2)
        self.assertTrue(all(r['state']=='pending' for r in self.store.candidates()))
        for path in self.store.data.rglob('*'):
            if path.is_file():self.assertNotIn(b'synthetic-batch-secret-not-for-files',path.read_bytes())

    def test_changed_source_rules_or_provider_after_preview_reject_before_sending(self):
        self.configure();refs=self.refs();request=self.request(refs)
        source=self.store.output/'notes'/(refs[0]['id']+'.md');source.write_text('Changed original')
        with self.assertRaises(StoreError):self.store.memory_batches.start(request,background=False)
        request=self.request(refs);(self.store.output/'prompts/memory-extraction.md').write_text('Changed rules')
        with self.assertRaises(StoreError):self.store.memory_batches.start(request,background=False)
        request=self.request(refs);self.store.analysis.configure({'endpoint':'https://other.invalid/v1','model':'other','key':'synthetic'})
        with self.assertRaises(StoreError):self.store.memory_batches.start(request,background=False)
        self.assertEqual(self.store.memory_batches.listing()['items'],[])

    def test_ranges_duplicates_unreadable_and_oversized_sources_are_explicit(self):
        refs=self.refs(1)
        for bad in [[],refs*2,refs*21,[{'kind':[],'id':refs[0]['id']}],[{'kind':'memory','id':refs[0]['id']}]]:
            with self.assertRaises(StoreError):self.store.memory_batches.preview(bad)
        (self.store.output/'notes'/(refs[0]['id']+'.md')).write_text('x'*32001)
        plan=self.store.memory_batches.preview(refs);self.assertIsNotNone(plan['items'][0]['problem'])
        self.assertEqual(plan['items'][0]['characters'],32001)
        (self.store.output/'notes'/(refs[0]['id']+'.md')).unlink()
        self.assertIsNotNone(self.store.memory_batches.preview(refs)['items'][0]['problem'])

    def test_pause_finishes_inflight_item_without_holding_store_lock_then_resume(self):
        refs=self.refs();self.configure();started=threading.Event();release=threading.Event()
        def delayed(*args):
            started.set();self.assertTrue(release.wait(5));return self.response(*args)
        try:
            with patch.object(self.store.analysis,'request_json',side_effect=delayed):
                result=self.store.memory_batches.start(self.request(refs))
                self.assertTrue(started.wait(3))
                self.assertEqual(self.store.memory_batches.pause(result['id'])['status'],'pausing')
                release.set();self.store.memory_batches.worker.join(3)
            state=self.store.memory_batches.status(result['id'])
            self.assertEqual(state['status'],'paused');self.assertEqual([i['status'] for i in state['items']],['ready','queued'])
            self.assertEqual(state['remainingRequests'],1)
            with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
                self.assertEqual(self.resume(result['id'])['status'],'ready');self.assertEqual(call.call_count,1)
            self.assertEqual(len(self.store.candidates()),2)
        finally:release.set()

    def test_active_batch_blocks_another_batch_and_single_source_send(self):
        refs=self.refs();other=self.refs(1)[0];self.configure();started=threading.Event();release=threading.Event()
        def delayed(*args):started.set();release.wait(5);return self.response(*args)
        try:
            with patch.object(self.store.analysis,'request_json',side_effect=delayed):
                result=self.store.memory_batches.start(self.request(refs));self.assertTrue(started.wait(3))
                with self.assertRaises(StoreError):self.store.memory_batches.start(self.request([other]))
                _,_,version=self.store.memories.source(other['kind'],other['id'])
                with self.assertRaises(StoreError):self.store.memory_extraction.start(other['kind'],other['id'],{'contentVersion':version,'sendToModel':True,'model':'synthetic-batch','endpoint':'https://batch.invalid/v1'})
                self.store.memory_batches.pause(result['id']);release.set();self.store.memory_batches.worker.join(3)
        finally:release.set()

    def test_service_failure_pauses_later_sends_and_retry_uses_same_range(self):
        self.configure();request=self.request(self.refs())
        with patch.object(self.store.analysis,'request_json',side_effect=StoreError(502,'模型额度或频率受限，请稍后重试。')) as call:
            result=self.store.memory_batches.start(request,background=False);self.assertEqual(call.call_count,1)
        self.assertEqual(result['status'],'paused');self.assertEqual([i['status'] for i in result['items']],['failed','queued'])
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
            self.assertEqual(self.resume(result['id'])['status'],'ready');self.assertEqual(call.call_count,2)

    def test_bad_evidence_does_not_discard_other_source_and_retry_only_failed(self):
        self.configure();refs=self.refs();counter=[0]
        def mixed(*args):
            counter[0]+=1
            return {'candidates':[{'text':'Invalid','quote':'Invented evidence','subject':'user','basis':'explicit'}]} if counter[0]==1 else self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=mixed):result=self.store.memory_batches.start(self.request(refs),background=False)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['candidateCount'],1)
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
            result=self.resume(result['id']);self.assertEqual(call.call_count,1)
        self.assertEqual(result['status'],'ready');self.assertEqual(result['candidateCount'],2)

    def test_source_changed_mid_batch_is_not_sent(self):
        self.configure();refs=self.refs()
        def mutate(*args):
            (self.store.output/'notes'/(refs[1]['id']+'.md')).write_text('Unapproved new content')
            return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=mutate) as call:
            result=self.store.memory_batches.start(self.request(refs),background=False)
            self.assertEqual(call.call_count,1)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['candidateCount'],1)
        self.assertIn('原文已变化',result['items'][1]['problem'])

    def test_restart_never_auto_sends_and_saved_results_resume_without_key(self):
        self.configure()
        with patch.object(self.store.analysis,'request_json',side_effect=self.response):result=self.store.memory_batches.start(self.request(self.refs()),background=False)
        with self.store.db:
            self.store.db.execute("UPDATE memory_batches SET status='running' WHERE id=?",(result['id'],))
            self.store.db.execute("UPDATE memory_batch_items SET status='running' WHERE batch_id=? AND position=1",(result['id'],))
        self.store.close();self.store=Store(self.root/'node')
        state=self.store.memory_batches.status(result['id']);self.assertEqual(state['status'],'paused');self.assertFalse(state['configured'])
        self.assertEqual(state['remainingRequests'],0)
        with patch.object(self.store.analysis,'request_json') as call:
            self.assertEqual(self.resume(result['id'])['status'],'ready');call.assert_not_called()
        self.assertEqual(len(self.store.candidates()),2)

    def test_sources_pagination_and_archive_filter_are_bounded(self):
        for i in range(43):self.note('Batch paging source '+str(i))
        first=self.store.memory_batches.sources({'kind':'note','q':'Batch paging'})
        self.assertEqual(len(first['items']),40);self.assertEqual(first['total'],43)
        second=self.store.memory_batches.sources({'kind':'note','q':'Batch paging','cursor':first['nextCursor']})
        self.assertEqual(len(second['items']),3)
        self.assertEqual(len({i['id'] for i in first['items']+second['items']}),43)
        with self.assertRaises(StoreError):self.store.memory_batches.sources({'kind':'document','cursor':first['nextCursor']})
        rid=first['items'][0]['id'];self.store.catalog.update('note',rid,{'revision':0,'archived':True})
        self.assertEqual(self.store.memory_batches.sources({'kind':'note','q':'Batch paging'})['total'],42)

    def test_no_implicit_send_and_device_token_required(self):
        refs=self.refs(1);request=self.request(refs)
        with self.assertRaises(StoreError):self.store.memory_batches.start({**request,'sendToModel':False})
        grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-batch-token');worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for method,path,body in [('GET','/v1/memory-batches/sources?kind=note',None),('POST','/v1/memory-batches/preview',json.dumps({'sources':refs}))]:
                for token,expected in [(grant['token'],401),(server.token,200)]:
                    client=http.client.HTTPConnection(*server.server_address,timeout=5);client.request(method,path,body=body,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    response=client.getresponse();value=json.loads(response.read());client.close();self.assertEqual(response.status,expected,value)
        finally:server.shutdown();server.server_close();worker.join()

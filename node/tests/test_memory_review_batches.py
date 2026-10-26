import json
import http.client
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch
import test_memory_reviews as fixtures
from store import Store, StoreError
from backup import create_backup, inspect_backup, restore_backup
from server import Server


class ReviewBatchTests(unittest.TestCase):
    setUp = fixtures.MemoryReviewTests.setUp
    tearDown = fixtures.MemoryReviewTests.tearDown
    note = fixtures.MemoryReviewTests.note
    reference = fixtures.MemoryReviewTests.reference
    propose = fixtures.MemoryReviewTests.propose
    update = fixtures.MemoryReviewTests.update
    prepare = fixtures.MemoryReviewTests.prepare

    def plan(self, count=3):
        pairs = [self.prepare()[0]['pair'] for _ in range(count)]
        plan = self.store.memory_review_batches.preview(pairs)
        return {'requestID':str(uuid.uuid4()), 'pairs':pairs, 'snapshot':plan['snapshot'], 'sendToModel':True}, plan

    def response(self, config, rules, payload):
        return {'relation':'uncertain','reason':'Synthetic evidence requires human review.','citations':[]}

    def test_scope_approval_limits_duplicates_and_preview_only(self):
        values,plan = self.plan(); before = self.store.db.total_changes
        self.assertEqual(plan, self.store.memory_review_batches.preview(values['pairs']))
        self.assertEqual(before,self.store.db.total_changes)
        for pairs in [[], values['pairs']*7, [values['pairs'][0]]*2]:
            with self.assertRaises(StoreError):self.store.memory_review_batches.preview(pairs)
        for invalid in [{**values,'sendToModel':False},{**values,'snapshot':'old'}]:
            with self.assertRaises(StoreError):self.store.memory_review_batches.start(invalid,False)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_review_batches').fetchone()[0],0)
        self.assertNotIn('synthetic-review-key-only',json.dumps(plan))

    def test_batch_preserves_all_memories_and_reuses_successes(self):
        values,plan=self.plan(); before=[dict(r) for r in self.store.db.execute('SELECT * FROM candidates')]
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as request:
            state=self.store.memory_review_batches.start(values,False)
            self.assertEqual(state['status'],'ready');self.assertEqual(request.call_count,3)
            self.assertEqual(self.store.memory_review_batches.start(values,False)['id'],state['id'])
            again=self.store.memory_review_batches.preview(values['pairs']);self.assertEqual(again['newRequests'],0)
            self.store.analysis.configuration=None
            again=self.store.memory_review_batches.preview(values['pairs'])
            copied=self.store.memory_review_batches.start({**values,'requestID':str(uuid.uuid4()),'snapshot':again['snapshot']},False)
            self.assertEqual(copied['status'],'ready');self.assertEqual(request.call_count,3)
        self.assertEqual(before,[dict(r) for r in self.store.db.execute('SELECT * FROM candidates')])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_relations').fetchone()[0],0)
        self.assertEqual(len(self.store.memory_review_batches.listing()['items']),2)
        self.assertEqual(self.store.memory_review_batches.listing()['items'][0]['items'],[])
        self.assertEqual(self.store.memory_review_batches.listing()['items'][0]['pairCount'],3)

    def test_pause_finishes_current_only_and_resume_requires_fresh_preview(self):
        values,plan=self.plan()
        def pause(*args):
            self.store.memory_review_batches.pause(values['requestID'])
            return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=pause) as request:
            state=self.store.memory_review_batches.start(values,False)
        self.assertEqual(state['status'],'paused');self.assertEqual(state['finishedCount'],1);self.assertEqual(request.call_count,1)
        remaining=self.store.memory_review_batches.resume_preview(state['id']);self.assertEqual(len(remaining['items']),2)
        with self.assertRaises(StoreError):self.store.memory_review_batches.resume(state['id'],{'sendToModel':True,'snapshot':plan['snapshot']},False)
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as request:
            done=self.store.memory_review_batches.resume(state['id'],{'sendToModel':True,'snapshot':remaining['snapshot']},False)
        self.assertEqual(done['status'],'ready');self.assertEqual(request.call_count,2)

    def test_source_changed_after_preview_never_sends_and_history_keeps_old_selection(self):
        values,plan=self.plan(1)
        self.update(values['pairs'][0]['incomingID'],text='Revised human memory')
        with patch.object(self.store.analysis,'request_json',side_effect=AssertionError('No sending')):
            with self.assertRaises(StoreError):self.store.memory_review_batches.start(values,False)

    def test_mid_batch_change_blocks_unapproved_pair_but_keeps_result(self):
        values,plan=self.plan(2)
        def change(*args):
            self.update(values['pairs'][1]['targetID'],text='Revised while first request ran.')
            return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=change) as request:
            state=self.store.memory_review_batches.start(values,False)
        self.assertEqual(state['status'],'partial');self.assertEqual(state['finishedCount'],1);self.assertEqual(request.call_count,1)
        current=self.store.memory_review_batches.resume_preview(state['id']);self.assertIsNotNone(current['items'][0]['problem'])
        with self.assertRaises(StoreError):self.store.memory_review_batches.resume(state['id'],{'sendToModel':True,'snapshot':current['snapshot']},False)
        self.assertEqual(state['items'][1]['selection'],plan['items'][1]['selection'])

    def test_rate_limit_stops_later_requests_and_retry_reuses_finished(self):
        values,_=self.plan(); calls=[]
        def response(*args):
            calls.append(args)
            if len(calls)==2:raise StoreError(429,'模型额度或频率受限，请稍后重试。')
            return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=response):state=self.store.memory_review_batches.start(values,False)
        self.assertEqual(len(calls),2);self.assertEqual([i['status'] for i in state['items']],['ready','failed','queued'])
        preview=self.store.memory_review_batches.resume_preview(state['id'])
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as request:
            done=self.store.memory_review_batches.resume(state['id'],{'sendToModel':True,'snapshot':preview['snapshot']},False)
        self.assertEqual(done['finishedCount'],3);self.assertEqual(request.call_count,2)

    def test_service_change_does_not_expand_original_batch_authorization(self):
        values,_=self.plan(2)
        def changed(*args):
            self.store.analysis.configuration={**self.store.analysis.configuration,'model':'different-synthetic-model'}
            return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=changed) as request:
            state=self.store.memory_review_batches.start(values,False)
        self.assertEqual(request.call_count,1);self.assertEqual(state['finishedCount'],1)
        preview=self.store.memory_review_batches.resume_preview(state['id'])
        with self.assertRaises(StoreError):self.store.memory_review_batches.resume(state['id'],{'sendToModel':True,'snapshot':preview['snapshot']},False)

    def test_concurrent_batch_and_single_requests_are_not_started_twice(self):
        values,_=self.plan(2);entered=threading.Event();release=threading.Event()
        def hold(*args):entered.set();release.wait(5);return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=hold):
            try:
                state=self.store.memory_review_batches.start(values);self.assertTrue(entered.wait(2))
                self.assertEqual(self.store.memory_review_batches.start(values)['id'],state['id'])
                with self.assertRaises(StoreError):self.store.memory_review_batches.start({**values,'requestID':str(uuid.uuid4())})
                other=self.store.memory_reviews.preview({'pair':values['pairs'][1]})
                with self.assertRaises(StoreError):self.store.memory_reviews.start({'pair':values['pairs'][1],'previewToken':other['previewToken'],'sendToModel':True})
                self.store.memory_review_batches.pause(state['id'])
            finally:release.set();self.store.memory_review_batches.worker.join(5)
        self.assertEqual(self.store.memory_review_batches.status(state['id'])['finishedCount'],1)

    def test_restart_pauses_interrupted_batch_and_recovers_saved_result_without_resend(self):
        values,_=self.plan(2)
        with patch.object(self.store.memory_review_batches,'launch'):
            state=self.store.memory_review_batches.start(values)
        first=self.store.memory_reviews.preview({'pair':values['pairs'][0]})
        with patch.object(self.store.analysis,'request_json',side_effect=self.response):
            self.store.memory_reviews.start({'pair':values['pairs'][0],'previewToken':first['previewToken'],'sendToModel':True},False)
        self.store.close();self.store=Store(self.root/'node')
        state=self.store.memory_review_batches.status(state['id']);self.assertEqual(state['status'],'paused')
        self.store.analysis.configure({'model':'synthetic-review','endpoint':'https://synthetic.invalid/v1','key':'synthetic-review-key-only'})
        preview=self.store.memory_review_batches.resume_preview(state['id']);self.assertEqual(preview['newRequests'],1)
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as request:
            done=self.store.memory_review_batches.resume(state['id'],{'sendToModel':True,'snapshot':preview['snapshot']},False)
        self.assertEqual(done['finishedCount'],2);self.assertEqual(request.call_count,1)

    def test_backup_restores_batch_history_and_legacy_five(self):
        values,_=self.plan(2)
        with patch.object(self.store.analysis,'request_json',side_effect=self.response):state=self.store.memory_review_batches.start(values,False)
        before=self.store.memory_review_batches.status(state['id']);self.store.close()
        archive=self.root/'batch.zip';create_backup(self.root/'node',archive)
        checked=inspect_backup(archive,self.root);destination=self.root/'restored'
        restore_backup(archive,destination,checked['archiveSHA256'],self.root)
        restored=Store(destination)
        try:
            self.assertEqual(restored.memory_review_batches.status(state['id']),before)
            self.assertEqual(restored.db.execute('SELECT count(*) FROM memory_reviews').fetchone()[0],2)
        finally:restored.close()
        with zipfile.ZipFile(archive) as z:members={n:z.read(n) for n in z.namelist()}
        manifest=json.loads(members['manifest.json']);manifest['version']=5
        manifest['tables'].pop('memory_review_batches');members.pop('tables/memory_review_batches.jsonl')
        manifest['files']=[i for i in manifest['files'] if i['path']!='tables/memory_review_batches.jsonl']
        members['manifest.json']=json.dumps(manifest).encode();legacy=self.root/'five.zip'
        with zipfile.ZipFile(legacy,'w') as z:
            for name,data in members.items():z.writestr(name,data)
        report=inspect_backup(legacy,self.root);destination=self.root/'legacy'
        restore_backup(legacy,destination,report['archiveSHA256'],self.root);restored=Store(destination)
        try:self.assertEqual(restored.memory_review_batches.listing()['items'],[])
        finally:restored.close()
        self.store=Store(self.root/'node')

    def test_owner_api_preview_start_list_and_readonly_token_is_rejected(self):
        values,_=self.plan(1);server=Server(('127.0.0.1',0),self.store,'synthetic-owner')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def call(method,path,body=None,token='synthetic-owner'):
            client=http.client.HTTPConnection(*server.server_address,timeout=5)
            client.request(method,'/v1/memory-review-batches'+path,body=json.dumps(body) if body is not None else None,
                           headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response=client.getresponse();status=response.status;data=json.loads(response.read());client.close()
            return status,data
        try:
            grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
            for token in ['',grant['token']]:self.assertEqual(call('GET','',token=token)[0],401)
            code,plan=call('POST','/preview',{'pairs':values['pairs']});self.assertEqual(code,200)
            with patch.object(self.store.analysis,'request_json',side_effect=self.response):
                code,state=call('POST','',{**values,'snapshot':plan['snapshot']});self.assertEqual(code,202)
                self.store.memory_review_batches.worker.join(5)
            code,state=call('GET','/'+state['id']);self.assertEqual(code,200);self.assertEqual(state['finishedCount'],1)
            self.assertEqual(call('GET','')[1]['items'][0]['id'],state['id'])
            self.assertEqual(call('POST','/'+state['id']+'/pause',{})[1]['status'],'ready')
        finally:server.shutdown();server.server_close();thread.join()

    def test_restoring_inflight_batch_never_starts_model_work(self):
        values,_=self.plan(1)
        with patch.object(self.store.memory_review_batches,'launch'):state=self.store.memory_review_batches.start(values)
        self.store.close();archive=self.root/'inflight.zip';create_backup(self.root/'node',archive)
        checked=inspect_backup(archive,self.root);destination=self.root/'inflight-copy'
        restore_backup(archive,destination,checked['archiveSHA256'],self.root)
        restored=Store(destination)
        try:
            self.assertEqual(restored.memory_review_batches.status(state['id'])['status'],'paused')
            self.assertIsNone(restored.memory_review_batches.worker)
            self.assertEqual(restored.db.execute('SELECT count(*) FROM memory_reviews').fetchone()[0],0)
        finally:restored.close()
        self.store=Store(self.root/'node')

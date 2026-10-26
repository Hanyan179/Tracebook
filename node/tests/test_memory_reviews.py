import http.client
import json
import threading
import unittest
import zipfile
from unittest.mock import patch
import test_memories as fixtures
from store import Store, StoreError
from server import Server
from backup import create_backup, inspect_backup, restore_backup, BackupError
from analysis_http_fixture import LocalModelFixture


class MemoryReviewTests(unittest.TestCase):
    setUp=fixtures.MemoryTests.setUp
    tearDown=fixtures.MemoryTests.tearDown
    note=fixtures.MemoryTests.note
    reference=fixtures.MemoryTests.reference
    propose=fixtures.MemoryTests.propose
    update=fixtures.MemoryTests.update

    def prepare(self, configure=True):
        old_note=self.note('I said: I review the orchard plan every Friday.')
        new_note=self.note('I said: I now review the orchard plan on Monday instead of Friday.')
        old=self.propose(old_note,'Weekly orchard review on Friday.');self.update(old,state='confirmed')
        new=self.propose(new_note,'Weekly orchard review on Monday.')
        if configure:self.store.analysis.configure({'model':'synthetic-review','endpoint':'https://synthetic.invalid/v1','key':'synthetic-review-key-only'})
        pair={'incomingID':new,'targetID':old,'incomingRevision':1,'targetRevision':2}
        preview=self.store.memory_reviews.preview({'pair':pair})
        return {'pair':pair,'previewToken':preview['previewToken'],'sendToModel':True},preview

    def result(self, preview, relation='conflict'):
        return {'relation':relation,'reason':'Synthetic review advice only; check the original speakers and dates.',
                'citations':[{'evidence':s['evidence'][0]['key'],'quote':s['evidence'][0]['quote']} for s in preview['sides']]}

    def run_review(self, values, result):
        with patch.object(self.store.analysis,'request_json',return_value=result) as request:
            state=self.store.memory_reviews.start(values,background=False)
        return state,request

    def test_preview_is_read_only_and_requires_explicit_current_scope_and_model(self):
        values,preview=self.prepare(configure=False);before=self.store.db.total_changes
        self.assertFalse(preview['canSend']);self.assertEqual(preview['status'],'idle')
        self.assertEqual(self.store.memory_reviews.preview({'pair':values['pair']}),preview)
        self.assertEqual(self.store.db.total_changes,before)
        self.assertNotIn('path',json.dumps(preview['sides']));self.assertNotIn('synthetic-review-key-only',json.dumps(preview))
        with patch.object(self.store.analysis,'request_json',side_effect=AssertionError('not authorized')):
            for data in [{**values,'sendToModel':False},values]:
                with self.assertRaises(StoreError):self.store.memory_reviews.start(data,background=False)
        self.store.analysis.configure({'model':'different-model','endpoint':'https://synthetic.invalid/v1','key':'synthetic-review-key-only'})
        with self.assertRaises(StoreError) as changed:self.store.memory_reviews.start(values,background=False)
        self.assertEqual(changed.exception.status,409)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_reviews').fetchone()[0],0)

    def test_valid_result_never_decides_memories_and_is_exported_with_original_positions(self):
        values,preview=self.prepare();before={r['id']:dict(r) for r in self.store.db.execute('SELECT * FROM candidates')}
        state,request=self.run_review(values,self.result(preview));self.assertEqual(state['status'],'ready')
        self.assertEqual(request.call_count,1);payload=request.call_args.args[2]
        self.assertEqual(set(payload),{'memories'});self.assertEqual(payload['memories'],preview['sides'])
        self.assertNotIn('synthetic-review-key-only',json.dumps(payload));self.assertNotIn('path',json.dumps(payload))
        self.assertEqual(before,{r['id']:dict(r) for r in self.store.db.execute('SELECT * FROM candidates')})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_relations').fetchone()[0],0)
        for citation in state['result']['citations']:
            e=citation['evidence'];_,body,version=self.store.memories.source(e['kind'],e['id'])
            self.assertEqual(body[e['quoteStart']:e['quoteStart']+len(e['quote'])],e['quote'])
            self.assertEqual(version,e['contentVersion'])
        for cid in before:
            detail=self.store.memories.detail(cid);self.assertEqual(detail['modelReviews'][0]['id'],state['id'])
            portable=json.loads((self.store.output/'memories'/(cid+'.json')).read_text())
            self.assertEqual(portable['modelReviews'][0]['result'],state['result'])
            self.assertNotIn('synthetic-review-key-only',json.dumps(portable))

    def test_ready_result_reopens_without_config_or_network_and_survives_human_decision(self):
        values,preview=self.prepare();state,_=self.run_review(values,self.result(preview))
        self.store.close();self.store=Store(self.root/'node')
        with patch.object(self.store.analysis,'request_json',side_effect=AssertionError('No resend')):
            repeated=self.store.memory_reviews.preview({'pair':values['pair']})
            self.assertEqual(repeated['id'],state['id']);self.assertEqual(repeated['result'],state['result'])
            self.assertEqual(self.store.memory_reviews.start(values,background=False)['status'],'ready')
        self.update(values['pair']['incomingID'],state='confirmed')
        historical=self.store.memory_reviews.status(state['id']);self.assertFalse(historical['current'])
        self.assertEqual(historical['result'],state['result'])
        self.assertEqual(self.store.memories.detail(values['pair']['incomingID'])['modelReviews'][0]['id'],state['id'])

    def test_invalid_or_one_sided_or_ambiguous_quotes_are_never_saved(self):
        values,preview=self.prepare();valid=self.result(preview)
        invalid=[{}, {**valid,'relation':'replace'}, {**valid,'relation':[]}, {**valid,'citations':valid['citations'][:1]},
                 {**valid,'citations':[{'evidence':'unselected/0','quote':'x'}]},
                 {**valid,'citations':[{'evidence':'incoming/0','quote':'invented'}]},
                 {**valid,'reason':'synthetic-review-key-only'}, {**valid,'reason':' '},
                 {**valid,'citations':'invalid'}, {**valid,'extra':'not allowed'}]
        for result in invalid:
            with self.subTest(result=result):
                state,_=self.run_review(values,result);self.assertEqual(state['status'],'failed');self.assertIsNone(state['result'])
                self.assertNotIn('synthetic-review-key-only',json.dumps(state))
        self.assertEqual(self.store.memory_reviews.saved(values['pair']['incomingID']),[])
        self.assertEqual(self.store.memories.detail(values['pair']['incomingID'])['state'],'pending')
        selection=self.store.memory_reviews.selected(values['pair'])
        selection['sides'][0]['evidence'][0]['quote']='same same'
        ambiguous={**valid,'citations':[{'evidence':'incoming/0','quote':'same'},valid['citations'][1]]}
        with self.assertRaises(StoreError):self.store.memory_reviews.validate(ambiguous,selection)

    def test_uncertain_has_no_unsupported_fact_or_automatic_decision(self):
        values,preview=self.prepare();state,_=self.run_review(values,{'relation':'uncertain','reason':'The subject is not clear.','citations':[]})
        self.assertEqual(state['status'],'ready');self.assertEqual(state['result']['relation'],'uncertain')
        self.assertEqual(self.store.memories.detail(values['pair']['targetID'])['state'],'confirmed')

    def test_new_app_rules_do_not_mislabel_unchanged_sources_or_rewrite_old_provenance(self):
        from memory_reviews import RULES
        values,preview=self.prepare();state,_=self.run_review(values,self.result(preview))
        row=self.store.db.execute('SELECT snapshot,updated_at FROM memory_reviews WHERE id=?',(state['id'],)).fetchone()
        self.assertEqual(state['completedAt'],row['updated_at']);self.assertEqual(len(json.loads(row['snapshot'])['rulesVersion']),64)
        with patch('memory_reviews.RULES',RULES+'\nUpdated test rules.'):
            historical=self.store.memory_reviews.status(state['id'])
            self.assertTrue(historical['current']);self.assertEqual(historical['result'],state['result'])
            new=self.store.memory_reviews.preview({'pair':values['pair']})
            self.assertNotEqual(new['id'],state['id']);self.assertEqual(new['status'],'idle')

    def test_changes_before_and_during_request_reject_result_and_keep_sources(self):
        values,preview=self.prepare()
        self.update(values['pair']['targetID'],text='A revised confirmed statement.')
        with self.assertRaises(StoreError):self.store.memory_reviews.start(values,background=False)
        values['pair']['targetRevision']=3;preview=self.store.memory_reviews.preview({'pair':values['pair']});values['previewToken']=preview['previewToken']
        def changed(*_):
            self.update(values['pair']['incomingID'],text='Changed while the model was working.')
            return self.result(preview)
        with patch.object(self.store.analysis,'request_json',side_effect=changed):state=self.store.memory_reviews.start(values,background=False)
        self.assertEqual(state['status'],'failed');self.assertIsNone(state['result']);self.assertFalse(state['current'])
        self.assertEqual(self.store.memories.detail(values['pair']['incomingID'])['state'],'pending')

    def test_edited_source_prevents_sending(self):
        values,preview=self.prepare();cid=values['pair']['incomingID'];original=preview['sides'][0]['evidence'][0]
        document=self.store.read_document('notes/'+original['id']+'.md')
        self.store.edit_document(document['path'],'Changed original source.',document['etag'])
        with self.assertRaises(StoreError):self.store.memory_reviews.preview({'pair':values['pair']})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_reviews').fetchone()[0],0)

    def test_empty_archived_or_oversized_evidence_and_credentials_are_rejected(self):
        values,preview=self.prepare();target=values['pair']['targetID']
        self.store.catalog.update('memory',target,{'revision':0,'archived':True})
        with self.assertRaises(StoreError):self.store.memory_reviews.preview({'pair':values['pair']})
        self.store.catalog.update('memory',target,{'revision':1,'archived':False})
        with self.store.db:self.store.db.execute("UPDATE memory_details SET evidence='[]' WHERE id=?",(target,))
        with self.assertRaises(StoreError):self.store.memory_reviews.preview({'pair':values['pair']})
        refs=[self.reference('note',self.note('Section '+str(i)+': '+'x'*3900)['id']) for i in range(9)]
        large_id=self.store.add_candidate('Large synthetic evidence set.',evidence=refs)['id'];self.update(large_id,state='confirmed')
        values['pair'].update(targetID=large_id,targetRevision=2)
        with self.assertRaises(StoreError) as large:self.store.memory_reviews.preview({'pair':values['pair']})
        self.assertEqual(large.exception.status,413)
        secret_note=self.note('SYNTHETIC api_key=sk-test-'+'x'*40)
        secret_id=self.propose(secret_note,'Synthetic credential-bearing source.');self.update(secret_id,state='confirmed')
        values['pair'].update(targetID=secret_id,targetRevision=2)
        with self.assertRaises(StoreError) as sensitive:self.store.memory_reviews.preview({'pair':values['pair']})
        self.assertEqual(sensitive.exception.status,409)
        self.assertNotIn('sk-test-',str(sensitive.exception))

    def test_running_duplicate_and_restart_require_explicit_retry(self):
        values,preview=self.prepare();entered=threading.Event();release=threading.Event()
        def delayed(*_):
            entered.set();release.wait(5);return self.result(preview)
        with patch.object(self.store.analysis,'request_json',side_effect=delayed) as request:
            state=self.store.memory_reviews.start(values);self.assertTrue(entered.wait(5))
            self.assertEqual(self.store.memory_reviews.start(values)['status'],'running')
            self.assertEqual(request.call_count,1);release.set();self.store.memory_reviews.worker.join(5)
        self.assertEqual(self.store.memory_reviews.status(state['id'])['status'],'ready')
        with self.store.db:self.store.db.execute("UPDATE memory_reviews SET status='running',result=NULL WHERE id=?",(state['id'],))
        self.store.close();self.store=Store(self.root/'node')
        restored=self.store.memory_reviews.status(state['id']);self.assertEqual(restored['status'],'interrupted')
        self.assertFalse(restored['canSend']);self.assertIsNone(self.store.analysis.configuration)

    def test_local_http_failure_retry_is_explicit_and_ready_result_is_not_resent(self):
        values,preview=self.prepare();fixture=LocalModelFixture(self.root/'retry-model')
        try:
            self.store.analysis.configure({'endpoint':fixture.endpoint,'model':'synthetic-review','key':'synthetic-model-key'})
            preview=self.store.memory_reviews.preview({'pair':values['pair']});values['previewToken']=preview['previewToken']
            fixture.set_response(mode='rate_limit')
            failed=self.store.memory_reviews.start(values,background=False)
            self.assertEqual(failed['status'],'failed');self.assertIsNone(failed['result'])
            self.assertEqual(len(fixture.requests),1)
            self.store.memory_reviews.preview({'pair':values['pair']});self.assertEqual(len(fixture.requests),1)
            fixture.set_response(mode='valid')
            ready=self.store.memory_reviews.start(values,background=False)
            self.assertEqual(ready['status'],'ready');self.assertEqual(ready['id'],failed['id'])
            self.assertEqual(len(fixture.requests),2)
            self.store.memory_reviews.start(values,background=False);self.assertEqual(len(fixture.requests),2)
            self.assertEqual(self.store.db.execute('SELECT attempts FROM memory_reviews').fetchone()[0],2)
        finally:fixture.close()

    def test_export_conflict_preserves_manual_file_and_never_reissues_model_request(self):
        values,preview=self.prepare();path=self.store.output/'memories'/(values['pair']['incomingID']+'.json')
        path.write_text('Synthetic manual history edit.')
        state,request=self.run_review(values,self.result(preview));self.assertEqual(state['status'],'ready')
        self.assertEqual(path.read_text(),'Synthetic manual history edit.')
        self.assertEqual(self.store.memories.delivery_status()['state'],'review')
        with patch.object(self.store.analysis,'request_json',side_effect=AssertionError('No resend')):
            self.assertEqual(self.store.memory_reviews.start(values,background=False)['id'],state['id'])
            self.store.memories.deliver_pending(force=True)
        self.assertEqual(path.read_text(),'Synthetic manual history edit.')

    def test_http_access_is_owner_only_and_local_transport_does_not_send_unselected_data(self):
        values,preview=self.prepare();self.note('UNSELECTED PRIVATE TEST WORDS')
        grant=self.store.knowledge.create_grant({'name':'Synthetic read-only','scopes':['confirmedMemory'],'days':1})
        fixture=LocalModelFixture(self.root/'model')
        try:
            self.store.analysis.configure({'endpoint':fixture.endpoint,'model':'synthetic-review','key':'synthetic-model-key'})
            values['previewToken']=self.store.memory_reviews.preview({'pair':values['pair']})['previewToken']
            state=self.store.memory_reviews.start(values,background=False)
            self.assertEqual(state['status'],'ready');self.assertEqual(len(fixture.requests),1)
            payload=json.dumps(fixture.requests[0]['body']);self.assertNotIn('UNSELECTED PRIVATE TEST WORDS',payload)
            self.assertTrue(fixture.requests[0]['hasAuthorization'])
        finally:fixture.close()
        server=Server(('127.0.0.1',0),self.store,'synthetic-owner')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for token,expected in [(grant['token'],401),('synthetic-owner',200)]:
                client=http.client.HTTPConnection(*server.server_address,timeout=5)
                client.request('GET','/v1/memory-reviews/'+state['id'],headers={'Authorization':'Bearer '+token})
                response=client.getresponse();self.assertEqual(response.status,expected);response.read();client.close()
        finally:server.shutdown();server.server_close();worker.join()

    def test_backup_preserves_advice_and_old_formats_restore_without_new_table(self):
        values,preview=self.prepare();state,_=self.run_review(values,self.result(preview))
        self.store.close();archive=self.root/'reviews.zip';create_backup(self.root/'node',archive)
        report=inspect_backup(archive,self.root);self.assertEqual(report['modelReviews'],1)
        target=self.root/'restored';restore_backup(archive,target,report['archiveSHA256'],self.root)
        restored=Store(target)
        try:
            self.assertEqual(restored.memory_reviews.status(state['id'])['result'],state['result'])
            self.assertIsNone(restored.analysis.configuration)
            self.assertEqual(restored.memory_reviews.preview({'pair':values['pair']})['id'],state['id'])
        finally:restored.close()
        # Known v1/v2 schemas lack only this new table. Other missing tables still fail.
        with zipfile.ZipFile(archive) as z:members={n:z.read(n) for n in z.namelist()}
        mislabeled=dict(members);manifest=json.loads(mislabeled['manifest.json']);manifest['version']=2
        manifest['tables'].pop('memory_reviews');mislabeled['manifest.json']=json.dumps(manifest).encode()
        invalid=self.root/'unregistered-table.zip'
        with zipfile.ZipFile(invalid,'w') as z:
            for name,raw in mislabeled.items():z.writestr(name,raw)
        with self.assertRaises(BackupError):inspect_backup(invalid,self.root)
        for version in [1,2]:
            legacy=dict(members);manifest=json.loads(legacy['manifest.json']);manifest['version']=version
            manifest['tables'].pop('memory_reviews');legacy.pop('tables/memory_reviews.jsonl')
            manifest['files']=[x for x in manifest['files'] if x['path']!='tables/memory_reviews.jsonl']
            legacy['manifest.json']=json.dumps(manifest).encode();file=self.root/('legacy-'+str(version)+'.zip')
            with zipfile.ZipFile(file,'w') as z:
                for name,raw in legacy.items():z.writestr(name,raw)
            report=inspect_backup(file,self.root);self.assertEqual(report['modelReviews'],0)
            destination=self.root/('legacy-'+str(version))
            restore_backup(file,destination,report['archiveSHA256'],self.root)
            reopened=Store(destination)
            try:self.assertEqual(reopened.db.execute('SELECT count(*) FROM memory_reviews').fetchone()[0],0)
            finally:reopened.close()
        self.store=Store(self.root/'node')


if __name__=='__main__':unittest.main()

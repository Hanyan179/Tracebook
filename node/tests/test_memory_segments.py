import hashlib
import http.client
import json
import threading
import unittest
import uuid
from unittest.mock import patch
import test_memories as fixtures
from store import Store, StoreError
from server import Server
from memory_segments import MAX_CHARACTERS, CONTEXT_CHARACTERS, FOCUS_CHARACTERS, grapheme_cluster_boundaries


class MemorySegmentTests(unittest.TestCase):
    setUp=fixtures.MemoryTests.setUp
    tearDown=fixtures.MemoryTests.tearDown
    note=fixtures.MemoryTests.note
    reference=fixtures.MemoryTests.reference

    def long_note(self):
        return self.note(''.join('Synthetic context line %05d: personal source text.\n'%i for i in range(1700)))

    def refs(self,note):
        plan=self.store.memory_segments.prepare('note',note['id'])
        return [{'kind':'note','id':note['id'],'contentVersion':plan['contentVersion'],
                 'segment':{k:u['segment'][k] for k in ('start','end')}} for u in plan['units']]

    def configure(self):
        self.store.analysis.configure({'endpoint':'https://segments.invalid/v1','model':'synthetic-segments','key':'synthetic-segment-key'})

    def request(self,refs):
        plan=self.store.memory_batches.preview(refs)
        return {'sources':refs,'snapshot':plan['snapshot'],'sendToModel':True,'requestID':str(uuid.uuid4())}

    def response(self,config,rules,payload):
        segment=payload['segment']
        quote=payload['text'][:80]
        return {'candidates':[{'text':'Synthetic focus at '+str(segment['start']),'quote':quote,'subject':'user','basis':'explicit'}]}

    def test_full_focus_coverage_bounded_context_and_no_text_mutation(self):
        note=self.long_note();body=note['text'];before=hashlib.sha256(body.encode()).hexdigest()
        plan=self.store.memory_segments.prepare('note',note['id']);self.assertGreater(len(plan['units']),1)
        joined='';previous=0
        for unit in plan['units']:
            s=unit['segment'];self.assertEqual(s['start'],previous);previous=s['end']
            joined+=body[s['start']:s['end']]
            self.assertLessEqual(unit['characters'],MAX_CHARACTERS)
            self.assertLessEqual(s['start']-s['contextStart'],CONTEXT_CHARACTERS)
            self.assertLessEqual(s['contextEnd']-s['end'],CONTEXT_CHARACTERS)
        self.assertEqual(joined,body)
        self.assertEqual(hashlib.sha256((self.store.output/'notes'/(note['id']+'.md')).read_bytes()).hexdigest(),before)
        self.assertEqual(self.store.candidates(),[])

    def test_unicode_clusters_survive_focus_and_context_boundaries(self):
        self.assertIsNotNone(grapheme_cluster_boundaries)
        for cluster in ['👩🏽‍💻','cafe\u0301','🇨🇳','각','क्\u200dष']:
            text='A'*(FOCUS_CHARACTERS-1)+cluster+'B'*4000+cluster+'C'*FOCUS_CHARACTERS
            ranges=self.store.memory_segments.ranges(text,hashlib.sha256(text.encode()).hexdigest())
            valid=set(grapheme_cluster_boundaries(text))
            self.assertEqual(''.join(text[r[0]:r[1]] for r in ranges),text)
            self.assertTrue(all(position in valid for r in ranges for position in r[:4]))
            self.assertLess(ranges[0][1],FOCUS_CHARACTERS+len(cluster))
        huge='x'+'\u0301'*(FOCUS_CHARACTERS+2)+'y'*5000
        with self.assertRaises(StoreError) as error:self.store.memory_segments.ranges(huge,hashlib.sha256(huge.encode()).hexdigest())
        self.assertEqual(error.exception.status,413)

    def test_short_source_reuses_original_identity_and_ascii_crlf_is_not_split(self):
        note=self.note();plan=self.store.memory_segments.prepare('note',note['id'])
        self.assertIsNone(plan['units'][0]['segment'])
        tid,_=self.store.memory_extraction.identity('note',note['id'],plan['contentVersion'])
        self.assertEqual(tid,str(uuid.uuid5(uuid.NAMESPACE_URL,'tracebook:memory:note/'+note['id']+'/'+plan['contentVersion'])).upper())
        text='x'*(FOCUS_CHARACTERS-1)+'\r\n'+'y'*40000
        ranges=self.store.memory_segments.ranges(text,hashlib.sha256(text.encode()).hexdigest())
        self.assertTrue(all(not (0<p<len(text) and text[p-1:p+1]=='\r\n') for r in ranges for p in r[:4]))

    def test_only_selected_ranges_sent_and_citations_use_full_source_positions(self):
        self.configure();note=self.long_note();refs=self.refs(note);chosen=refs[1:3]
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
            batch=self.store.memory_batches.start(self.request(chosen),background=False)
            self.assertEqual(call.call_count,len(chosen))
            self.assertEqual(batch['status'],'ready')
            for sent,item in zip(call.call_args_list,batch['items']):
                payload=sent.args[2];span=item['segment']
                self.assertEqual(payload['text'],note['text'][span['start']:span['end']])
                self.assertEqual(payload['contextBefore'],note['text'][span['contextStart']:span['start']])
                self.assertEqual(payload['contextAfter'],note['text'][span['end']:span['contextEnd']])
                self.assertEqual(sum(len(payload[k]) for k in ('text','contextBefore','contextAfter')),item['characters'])
        for item in batch['items']:
            candidate=self.store.memories.detail(item['candidateIDs'][0]);e=candidate['evidence'][0]
            self.assertEqual(e['quoteStart'],item['segment']['start'])
            self.assertEqual(note['text'][e['quoteStart']:e['quoteStart']+len(e['quote'])],e['quote'])
            self.assertEqual(candidate['proposal']['segment'],item['segment'])
            self.assertEqual(candidate['state'],'pending')
        coverage=self.store.memory_segments.prepare('note',note['id'])
        self.assertEqual(sum(u['reusable'] for u in coverage['units']),len(chosen))
        self.assertFalse(coverage['units'][0]['reusable'])

    def test_repeated_quote_in_another_segment_keeps_the_selected_occurrence(self):
        quote='本人说：我每周五回顾计划。'
        note=self.note(quote+'\n'+('其他人说的是合成背景。\n'*5000)+quote+'\n')
        ref=self.refs(note)[-1];self.configure()
        output={'candidates':[{'text':'Synthetic weekly review','quote':quote,'subject':'user','basis':'explicit'}]}
        with patch.object(self.store.analysis,'request_json',return_value=output):
            result=self.store.memory_batches.start(self.request([ref]),background=False)
        self.assertEqual(result['status'],'ready')
        evidence=self.store.memories.detail(result['items'][0]['candidateIDs'][0])['evidence'][0]
        self.assertEqual(evidence['quoteStart'],note['text'].rfind(quote))

    def test_context_only_quotes_are_rejected_instead_of_duplicated(self):
        note=self.long_note();ref=self.refs(note)[1];self.configure()
        def outside(config,rules,payload):
            self.assertIn('surrounding context',rules)
            return {'candidates':[{'text':'Wrong scope','quote':payload['contextBefore'][:70],'subject':'user','basis':'explicit'}]}
        with patch.object(self.store.analysis,'request_json',side_effect=outside):
            batch=self.store.memory_batches.start(self.request([ref]),background=False)
        self.assertEqual(batch['status'],'partial');self.assertIn('超出所选片段',batch['items'][0]['problem'])
        self.assertEqual(self.store.candidates(),[])

    def test_invalid_overlapping_or_stale_ranges_do_not_send(self):
        note=self.long_note();refs=self.refs(note)
        for segment in [{'start':True,'end':20},{'start':-1,'end':100},{'start':0,'end':1.5},{'start':0,'end':32001}]:
            with self.assertRaises(StoreError):self.store.memory_batches.preview([{**refs[0],'segment':segment}])
        for choices in [[refs[0],refs[0]],[refs[0],{'kind':'note','id':note['id']}]]:
            with self.assertRaises(StoreError):self.store.memory_batches.preview(choices)
        arbitrary={**refs[0],'segment':{'start':1,'end':100}}
        self.assertIsNotNone(self.store.memory_batches.preview([arbitrary])['items'][0]['problem'])
        self.configure();request=self.request([refs[0]])
        self.store.knowledge.save_note({'title':note['title'],'text':note['text']+'Changed','etag':note['etag']},note['id'])
        with patch.object(self.store.analysis,'request_json') as call:
            with self.assertRaises(StoreError):self.store.memory_batches.start(request,background=False)
            call.assert_not_called()
        self.assertIn('已变化',self.store.memory_batches.preview([refs[0]])['items'][0]['problem'])

    def test_saved_ranges_survive_restart_and_reuse_without_a_model_key(self):
        note=self.long_note();refs=self.refs(note)[1:2];self.configure()
        with patch.object(self.store.analysis,'request_json',side_effect=self.response):first=self.store.memory_batches.start(self.request(refs),background=False)
        ids=first['items'][0]['candidateIDs'];self.store.close();self.store=Store(self.root/'node')
        with patch.object(self.store.analysis,'request_json') as call:
            second=self.store.memory_batches.start(self.request(refs),background=False);call.assert_not_called()
        self.assertEqual(second['status'],'ready');self.assertEqual(second['items'][0]['candidateIDs'],ids)
        self.assertEqual(len(self.store.candidates()),1)

    def test_preparation_releases_store_lock_and_rejects_changes_during_segmentation(self):
        note=self.long_note();observed=[];original=self.store.memory_segments.ranges
        def slow(text,version):
            def other():
                acquired=self.store.lock.acquire(timeout=1);observed.append(acquired)
                if acquired:self.store.lock.release()
            thread=threading.Thread(target=other);thread.start();thread.join()
            return original(text,version)
        with patch.object(self.store.memory_segments,'ranges',side_effect=slow):self.store.memory_segments.prepare('note',note['id'])
        self.assertEqual(observed,[True])
        def changed(text,version):
            (self.store.output/'notes'/(note['id']+'.md')).write_text(text+'Changed')
            return original(text,version)
        with patch.object(self.store.memory_segments,'ranges',side_effect=changed):
            with self.assertRaises(StoreError) as error:self.store.memory_segments.prepare('note',note['id'])
        self.assertEqual(error.exception.status,409)

    def test_source_edit_after_model_request_keeps_original_and_stores_no_proposal(self):
        note=self.long_note();ref=self.refs(note)[0];self.configure()
        def edit(*args):
            (self.store.output/'notes'/(note['id']+'.md')).write_text(note['text']+'Human edit')
            return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=edit):batch=self.store.memory_batches.start(self.request([ref]),background=False)
        self.assertEqual(batch['status'],'partial');self.assertEqual(self.store.candidates(),[])
        self.assertTrue((self.store.output/'notes'/(note['id']+'.md')).read_text().endswith('Human edit'))

    def test_twenty_unit_limit_preserves_coverage_for_later_batches(self):
        text=''.join('Synthetic unique document line %06d: keep this original text.\n'%i for i in range(11000))
        preview=self.store.documents.preview({'files':[{'name':'long-source.txt','text':text}]});self.store.documents.commit(preview['id'])
        rid=self.store.db.execute('SELECT id FROM documents').fetchone()[0]
        plan=self.store.memory_segments.prepare('document',rid)
        refs=[{'kind':'document','id':rid,'contentVersion':plan['contentVersion'],'segment':{k:u['segment'][k] for k in ('start','end')}} for u in plan['units']]
        self.assertGreater(len(refs),20)
        with self.assertRaises(StoreError):self.store.memory_batches.preview(refs[:21])
        self.configure()
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
            self.store.memory_batches.start(self.request(refs[:20]),background=False);self.assertEqual(call.call_count,20)
            current=self.store.memory_segments.prepare('document',rid)
            self.assertEqual(sum(u['reusable'] for u in current['units']),20)
            self.store.memory_batches.start(self.request(refs[20:]),background=False)
            self.assertEqual(call.call_count,len(refs))
        self.assertEqual(len(self.store.candidates()),len(refs))
        self.assertTrue(all(u['reusable'] for u in self.store.memory_segments.prepare('document',rid)['units']))

    def test_partial_segment_batch_restart_resumes_only_unfinished_ranges(self):
        note=self.long_note();refs=self.refs(note)[:3];self.configure();counter=[0]
        def limited(*args):
            counter[0]+=1
            if counter[0]==2:raise StoreError(502,'模型额度或频率受限，请稍后重试。')
            return self.response(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=limited):result=self.store.memory_batches.start(self.request(refs),background=False)
        self.assertEqual(result['status'],'paused');self.assertEqual(result['remainingRequests'],2)
        self.store.close();self.store=Store(self.root/'node');self.assertEqual(len(self.store.candidates()),1)
        self.configure()
        with patch.object(self.store.analysis,'request_json',side_effect=self.response) as call:
            resumed=self.store.memory_batches.resume(result['id'],{'sendToModel':True,'model':'synthetic-segments','endpoint':'https://segments.invalid/v1'},background=False)
            self.assertEqual([x.args[2]['segment']['start'] for x in call.call_args_list],[r['segment']['start'] for r in refs[1:]])
        self.assertEqual(resumed['status'],'ready');self.assertEqual(len(self.store.candidates()),3)

    def test_saved_segment_file_recovery_does_not_resend_a_changed_source(self):
        note=self.long_note();ref=self.refs(note)[1];self.configure()
        args={**ref,'sendToModel':True,'model':'synthetic-segments','endpoint':'https://segments.invalid/v1'}
        with patch.object(self.store.analysis,'request_json',side_effect=self.response),patch.object(self.store,'export_memory',side_effect=OSError('synthetic disk failure')):
            result=self.store.memory_extraction.start('note',note['id'],args,background=False)
        self.assertTrue(result['canResume']);self.assertEqual(result['status'],'failed')
        path=self.store.output/'notes'/(note['id']+'.md');path.write_text(note['text']+'Manual source edit')
        self.store.analysis.configuration=None
        with patch.object(self.store.analysis,'request_json') as call:
            recovered=self.store.memory_extraction.start('note',note['id'],args,background=False);call.assert_not_called()
        self.assertEqual(recovered['status'],'ready');self.assertTrue(recovered['sourceChanged'])
        self.assertEqual(len(self.store.candidates()),1);self.assertTrue(path.read_text().endswith('Manual source edit'))

    def test_source_and_cache_bounds_and_missing_component_are_explicit(self):
        text='x'*2_000_001
        with self.assertRaises(StoreError) as too_big:self.store.memory_segments.ranges(text,hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(too_big.exception.status,413)
        with patch('memory_segments.grapheme_cluster_boundaries',None):
            with self.assertRaises(StoreError) as absent:self.store.memory_segments.ranges('x'*50000,'new-version')
        self.assertEqual(absent.exception.status,503)
        for i in range(12):
            text=('line %d\n'%i)*6000
            self.store.memory_segments.ranges(text,hashlib.sha256(text.encode()).hexdigest())
        self.assertLessEqual(len(self.store.memory_segments.cache),8)

    def test_real_loopback_https_protocol_returns_all_synthetic_focus_markers(self):
        from analysis_http_fixture import LocalModelFixture
        text=('Synthetic background.\n'*1500)+'本人陈述：我保留来源。\n本人陈述：我每周五回顾计划。\n'+('More background.\n'*2000)
        note=self.note(text);refs=self.refs(note)
        chosen=next(r for r in refs if r['segment']['start']<=text.index('本人陈述')<r['segment']['end'])
        fixture=LocalModelFixture(self.root/'model-fixture')
        try:
            fixture.set_response('segments')
            self.store.analysis.configure({'endpoint':fixture.endpoint,'model':'synthetic-segments','key':'synthetic-model-key'})
            result=self.store.memory_batches.start(self.request([chosen]),background=False)
            self.assertEqual(result['status'],'ready');self.assertEqual(result['candidateCount'],2)
            self.assertEqual(len(fixture.requests),1);self.assertTrue(fixture.requests[0]['hasAuthorization'])
            for cid in result['items'][0]['candidateIDs']:
                evidence=self.store.memories.detail(cid)['evidence'][0]
                self.assertEqual(text[evidence['quoteStart']:evidence['quoteStart']+len(evidence['quote'])],evidence['quote'])
        finally:fixture.close()

    def test_scope_and_excerpt_endpoints_require_device_token(self):
        note=self.long_note();ref=self.refs(note)[1]
        grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-segment-token');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for method in ['GET','POST']:
                for token,expected in [(grant['token'],401),(server.token,200)]:
                    client=http.client.HTTPConnection(*server.server_address,timeout=5)
                    client.request(method,'/v1/memory-segments/note/'+note['id'],body=json.dumps(ref) if method=='POST' else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    response=client.getresponse();self.assertEqual(response.status,expected);body=json.loads(response.read());client.close()
                    if expected==200 and method=='POST':self.assertEqual(body['text'],note['text'][ref['segment']['start']:ref['segment']['end']])
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()

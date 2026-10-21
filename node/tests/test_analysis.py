import datetime as dt
import json
import unittest
from unittest.mock import patch
import test_node as fixtures
from store import Store,StoreError
from analysis import SECTIONS

class AnalysisTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown
    def prepare(self,text='Synthetic user explicitly decided to review Friday.'):
        note=self.store.knowledge.save_note({'title':'Synthetic test','text':text})
        day=self.store.catalog.query('note',{})['items'][0]['day']
        self.store.analysis.configure({'endpoint':'https://api.deepseek.com','model':'test-model','key':'synthetic-key-only'})
        return note,day
    def response(self,snapshot):
        result={key:[] for key,_ in SECTIONS}
        result['overview']=[{'text':'Synthetic review decision.','sources':[snapshot['sources'][0]['id']]}]
        return result
    def test_requires_explicit_configuration_and_keeps_secret_out_of_disk(self):
        with self.assertRaises(StoreError):self.store.analysis.start('2026-10-20',background=False)
        note,day=self.prepare()
        self.assertTrue(self.store.analysis.state()['configured'])
        self.assertFalse(self.store.analysis.state()['automatic'])
        self.assertNotIn('synthetic-key-only',json.dumps(self.store.analysis.state()))
        with self.assertRaises(StoreError):self.store.analysis.configure({'endpoint':'http://remote.test','model':'test','key':'fake'})
        for file in self.store.data.rglob('*'):
            if file.is_file():self.assertNotIn(b'synthetic-key-only',file.read_bytes())
        self.store.close();self.store=Store(self.root/'node')
        self.assertFalse(self.store.analysis.state()['configured'])
    def test_valid_response_exports_sources_without_confirming_memory(self):
        note,day=self.prepare()
        with patch.object(self.store.analysis,'complete',side_effect=lambda config,snapshot:self.response(snapshot)) as call:
            result=self.store.analysis.start(day,background=False)
            self.assertEqual(result['status'],'ready')
            self.store.analysis.start(day,background=False);self.assertEqual(call.call_count,1)
        text=self.store.read_document('analysis/'+day+'.md')['text']
        self.assertIn('../notes/'+note['id']+'.md',text)
        self.assertIn('不代表完整的一天',text)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],0)
        found=self.store.knowledge.query({'query':'Synthetic','scopes':['analyses']})
        self.assertEqual(found['sources'][0]['kind'],'analysis')
    def test_invalid_citation_fails_without_replacing_existing_analysis(self):
        note,day=self.prepare()
        with patch.object(self.store.analysis,'complete',side_effect=lambda config,snapshot:self.response(snapshot)):
            self.store.analysis.start(day,background=False)
        original=self.store.read_document('analysis/'+day+'.md')['text']
        bad={key:[] for key,_ in SECTIONS};bad['overview']=[{'text':'Unsupported claim','sources':['note/not-provided']}]
        with patch.object(self.store.analysis,'complete',return_value=bad):
            result=self.store.analysis.start(day,force=True,background=False)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.store.read_document('analysis/'+day+'.md')['text'],original)
    def test_human_analysis_edit_is_preserved_as_conflict(self):
        note,day=self.prepare()
        with patch.object(self.store.analysis,'complete',side_effect=lambda config,snapshot:self.response(snapshot)):
            self.store.analysis.start(day,background=False)
            document=self.store.read_document('analysis/'+day+'.md')
            self.store.edit_document(document['path'],'Human analysis edit',document['etag'])
            self.store.knowledge.save_note({'title':note['title'],'text':'A new source version','etag':note['etag']},note['id'])
            result=self.store.analysis.start(day,background=False)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual(self.store.read_document('analysis/'+day+'.md')['text'],'Human analysis edit')
    def test_bounded_context_discloses_missing_text_and_truncation(self):
        note,day=self.prepare('Synthetic '+('x'*80000))
        snapshot=self.store.analysis.snapshot(day)
        self.assertEqual(snapshot['coverage']['truncatedSources'],1)
        self.assertLessEqual(snapshot['coverage']['includedCharacters'],60000)
        self.assertLessEqual(len(snapshot['sources'][0]['text']),12000)
    def test_model_request_uses_configured_endpoint_json_and_no_redirects(self):
        note,day=self.prepare();snapshot=self.store.analysis.snapshot(day)
        payload={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.response(snapshot))}}]}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return json.dumps(payload).encode()
        requests=[]
        class Opener:
            def open(self,request,timeout):requests.append(request);return Response()
        with patch('analysis.urllib.request.build_opener',return_value=Opener()):
            result=self.store.analysis.start(day,background=False)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(requests[0].full_url,'https://api.deepseek.com/chat/completions')
        body=json.loads(requests[0].data)
        self.assertEqual(body['response_format'],{'type':'json_object'})
        self.assertEqual(body['thinking'],{'type':'disabled'})
        self.assertNotIn('synthetic-key-only',json.dumps(body))
    def test_source_change_during_call_rejects_stale_output(self):
        note,day=self.prepare()
        def changed(config,snapshot):
            self.store.knowledge.save_note({'title':note['title'],'text':'Changed while running','etag':note['etag']},note['id'])
            return self.response(snapshot)
        with patch.object(self.store.analysis,'complete',side_effect=changed):result=self.store.analysis.start(day,background=False)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(self.store._path('analysis/'+day+'.md').exists())
    def test_automatic_analysis_respects_opt_in_cutoff_and_changed_sources(self):
        note,day=self.prepare()
        self.store.configure_daily({'enabled':True,'time':'00:15','timeZone':'UTC'})
        day=self.store.catalog.query('note',{})['items'][0]['day']
        tomorrow=dt.datetime.combine(dt.date.fromisoformat(day)+dt.timedelta(days=1),dt.time(),dt.timezone.utc)
        with patch.object(self.store.analysis,'start') as start:
            self.store.analysis.catch_up(tomorrow+dt.timedelta(hours=1));start.assert_not_called()
            self.store.analysis.set_automatic({'automatic':True})
            self.store.analysis.catch_up(tomorrow);start.assert_not_called()
            self.store.analysis.catch_up(tomorrow+dt.timedelta(hours=1));start.assert_called_once_with(day)
        with patch.object(self.store.analysis,'complete',side_effect=lambda config,snapshot:self.response(snapshot)):
            self.store.analysis.start(day,background=False)
        with patch.object(self.store.analysis,'start') as start:
            self.store.analysis.catch_up(tomorrow+dt.timedelta(hours=1));start.assert_not_called()
            self.store.knowledge.save_note({'title':note['title'],'text':'A changed source','etag':note['etag']},note['id'])
            self.store.analysis.catch_up(tomorrow+dt.timedelta(hours=1));start.assert_called_once_with(day)
    def test_analysis_scope_does_not_expand_existing_grants(self):
        note,day=self.prepare()
        with patch.object(self.store.analysis,'complete',side_effect=lambda config,snapshot:self.response(snapshot)):
            self.store.analysis.start(day,background=False)
        with self.assertRaises(StoreError):
            self.store.knowledge.read_source('analysis',day,['notes'],{})
        result=self.store.knowledge.read_source('analysis',day,['analyses'],{})
        self.assertIn('Synthetic',result['text'])

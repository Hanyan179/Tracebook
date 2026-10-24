import hashlib
import http.client
import json
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch
import test_knowledge as fixtures
from store import Store, StoreError
from server import Server


class AnswerTests(unittest.TestCase):
    setUp=fixtures.KnowledgeTests.setUp
    tearDown=fixtures.KnowledgeTests.tearDown
    note=fixtures.KnowledgeTests.note

    def configure(self):
        self.store.answers.configure({'endpoint':'https://answers.invalid/v1','model':'synthetic-answer','key':'synthetic-answer-key'})

    def question(self, text='项目什么时候复盘？', scopes=None, limit=8):
        chat=self.store.knowledge.create_conversation()
        turn=self.store.knowledge.ask(chat['id'],{'query':text,'requestID':str(uuid.uuid4()),
                                                'scopes':scopes or ['notes'],'limit':limit})
        return chat['id'],turn

    def request(self, cid, turn, sources=None):
        refs=sources or [s['kind']+'/'+s['id'] for s in turn['response']['sources']]
        plan=self.store.answers.preview(cid,turn['id'],{'sources':refs})
        return {'sources':refs,'snapshot':plan['snapshot'],'sendToModel':True}

    def reply(self, config, rules, payload):
        source=payload['sources'][0]
        return {'status':'answered','statements':[{'text':'合成回答：项目计划周五复盘。',
                'citations':[{'source':source['id'],'quote':source['text'][:100]}]}]}

    def run_answer(self, cid, turn, body=None):
        with patch.object(self.store.analysis,'request_json',side_effect=self.reply) as call:
            result=self.store.answers.start(cid,turn['id'],body or self.request(cid,turn),background=False)
        return result,call

    def test_answer_service_is_independent_and_preview_never_sends(self):
        self.note();cid,turn=self.question()
        self.store.analysis.configure({'endpoint':'https://daily.invalid/v1','model':'daily','key':'synthetic-daily-key'})
        with patch.object(self.store.analysis,'request_json') as call:
            body=self.request(cid,turn)
            self.assertFalse(self.store.answers.preview(cid,turn['id'],body)['configured'])
            with self.assertRaises(StoreError):self.store.answers.start(cid,turn['id'],body,background=False)
            call.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM answer_jobs').fetchone()[0],0)
        self.configure()
        self.assertEqual(self.store.analysis.configuration['model'],'daily')
        self.assertTrue(self.store.status()['answerConfigured'])
        self.store.answers.configure({'clear':True})
        self.assertTrue(self.store.status()['analysisConfigured'])
        self.assertFalse(self.store.status()['answerConfigured'])

    def test_explicit_subset_request_has_exact_text_and_global_citations(self):
        first=self.note('项目：不选择这个片段。')
        fact='项目决定每周五复盘。'
        chosen=self.note(('背景资料。'*4000)+fact+'后续。'*400)
        self.configure();cid,turn=self.question()
        ref='note/'+chosen['id'];body=self.request(cid,turn,[ref])
        result,call=self.run_answer(cid,turn,body)
        self.assertEqual(result['job']['status'],'ready')
        payload=call.call_args.args[2]
        self.assertEqual(len(payload['sources']),1)
        source=next(s for s in turn['response']['sources'] if s['id']==chosen['id'])
        self.assertEqual(payload['sources'][0]['text'],source['excerpt'])
        self.assertNotIn(first['id'],json.dumps(payload))
        self.assertNotIn('synthetic-answer-key',json.dumps(payload))
        citation=result['answer']['statements'][0]['citations'][0]
        start=citation['excerptStart']
        self.assertEqual(chosen['text'][start:start+len(citation['excerpt'])],citation['excerpt'])
        self.assertEqual(citation['contentVersion'],hashlib.sha256(chosen['text'].encode()).hexdigest())
        current=self.store.knowledge.turns(cid)['items'][0]
        self.assertEqual(current['response']['sources'],turn['response']['sources'])
        self.assertTrue(current['response']['generatedAnswer'])
        self.assertEqual(self.store.candidates(),[])
        self.assertEqual((self.store.output/chosen['path']).read_text(),chosen['text'])

    def test_explicit_send_snapshot_and_selection_limits_are_required(self):
        for i in range(9):self.note('项目 '+str(i))
        self.configure();cid,turn=self.question(limit=20)
        refs=['note/'+s['id'] for s in turn['response']['sources']]
        for values in [{'sources':[]},{'sources':refs},{'sources':[refs[0],refs[0]]},{'sources':['note/'+str(uuid.uuid4())]}]:
            with self.assertRaises(StoreError):self.store.answers.preview(cid,turn['id'],values)
        body=self.request(cid,turn,refs[:1])
        with patch.object(self.store.analysis,'request_json') as call:
            for invalid in [{**body,'sendToModel':False},{**body,'snapshot':'wrong'},{**body,'snapshot':'0'*64}]:
                with self.assertRaises(StoreError):self.store.answers.start(cid,turn['id'],invalid,background=False)
            call.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM answer_jobs').fetchone()[0],0)

    def test_changed_source_or_service_requires_a_new_preview(self):
        note=self.note();self.configure();cid,turn=self.question();body=self.request(cid,turn)
        self.store.answers.configure({'endpoint':'https://changed.invalid','model':'changed','key':'synthetic-new-key'})
        with self.assertRaises(StoreError):self.store.answers.start(cid,turn['id'],body,background=False)
        self.configure()
        self.store.knowledge.save_note({'title':note['title'],'text':note['text']+'新资料','etag':note['etag']},note['id'])
        plan=self.store.answers.preview(cid,turn['id'],body)
        self.assertIsNone(plan['items'][0]['source'])
        self.assertIn('已变化',plan['items'][0]['problem'])
        with patch.object(self.store.analysis,'request_json') as call:
            with self.assertRaises(StoreError):self.store.answers.start(cid,turn['id'],body,background=False)
            call.assert_not_called()

    def test_old_or_ambiguous_excerpt_is_not_silently_relocated(self):
        self.note();self.configure();cid,turn=self.question()
        response=turn['response'];response['sources'][0].pop('excerptStart')
        with self.store.db:self.store.db.execute('UPDATE conversation_turns SET response=? WHERE id=?',(json.dumps(response),turn['id']))
        plan=self.store.answers.preview(cid,turn['id'],{'sources':['note/'+response['sources'][0]['id']]})
        self.assertIn('位置',plan['items'][0]['problem'])
        self.assertIsNone(plan['items'][0]['source'])

    def test_invalid_model_outputs_preserve_retrieval_and_create_no_memories(self):
        note=self.note('项目 周五。周五。');self.configure()
        invalid=[
            {'status':'answered','statements':[]},
            {'status':'insufficient','statements':[{'text':'Unsupported','citations':[]}]},
            {'status':'answered','statements':[{'text':'Unsupported','citations':[]}]},
            {'status':'answered','statements':[{'text':'Wrong source','citations':[{'source':'note/not-supplied','quote':'项目'}]}]},
            {'status':'answered','statements':[{'text':'Wrong quote','citations':[{'source':'note/'+note['id'],'quote':'missing'}]}]},
            {'status':'answered','statements':[{'text':'Ambiguous quote','citations':[{'source':'note/'+note['id'],'quote':'周五'}]}]},
            {'status':'answered','statements':[{'text':'x'*1201,'citations':[{'source':'note/'+note['id'],'quote':'项目'}]}]},
            {'status':'answered','statements':[], 'extra':'ignore rules'},
        ]
        for output in invalid:
            with self.subTest(output=output):
                cid,turn=self.question();body=self.request(cid,turn)
                with patch.object(self.store.analysis,'request_json',return_value=output):
                    result=self.store.answers.start(cid,turn['id'],body,background=False)
                self.assertEqual(result['job']['status'],'failed')
                current=self.store.knowledge.turns(cid)['items'][0]
                self.assertEqual(current['response'],turn['response'])
                self.assertEqual(self.store.candidates(),[])

    def test_insufficient_evidence_is_saved_without_fabricated_statements(self):
        self.note();self.configure();cid,turn=self.question()
        with patch.object(self.store.analysis,'request_json',return_value={'status':'insufficient','statements':[]}):
            result=self.store.answers.start(cid,turn['id'],self.request(cid,turn),background=False)
        self.assertEqual(result['job']['status'],'ready')
        self.assertTrue(result['answer']['insufficient'])
        self.assertEqual(result['answer']['statements'],[])
        self.assertEqual(self.store.candidates(),[])

    def test_changes_during_the_request_reject_the_answer(self):
        note=self.note();self.configure();cid,turn=self.question();body=self.request(cid,turn)
        def changed(*args):
            self.store.knowledge.save_note({'title':note['title'],'text':note['text']+'人工修订','etag':note['etag']},note['id'])
            return self.reply(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=changed):
            result=self.store.answers.start(cid,turn['id'],body,background=False)
        self.assertEqual(result['job']['status'],'failed')
        self.assertIn('已变化',result['job']['problem'])
        self.assertEqual(self.store.knowledge.turns(cid)['items'][0]['response'],turn['response'])
        self.assertTrue(self.store.knowledge.note(note['id'])['text'].endswith('人工修订'))

    def test_archived_source_cannot_back_an_answer(self):
        note=self.note();self.configure();cid,turn=self.question();body=self.request(cid,turn)
        def archived(*args):
            self.store.catalog.update('note',note['id'],{'revision':0,'archived':True})
            return self.reply(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=archived):
            result=self.store.answers.start(cid,turn['id'],body,background=False)
        self.assertEqual(result['job']['status'],'failed')
        self.assertIsNone(result['answer'])

    def test_saved_answer_survives_restart_without_key_or_repeat_requests(self):
        self.note();self.configure();cid,turn=self.question();body=self.request(cid,turn)
        first,call=self.run_answer(cid,turn,body);self.assertEqual(call.call_count,1)
        self.store.close();self.store=Store(self.root/'node')
        with patch.object(self.store.analysis,'request_json') as call:
            second=self.store.answers.start(cid,turn['id'],body,background=False)
            call.assert_not_called()
        self.assertFalse(second['configured'])
        self.assertEqual(first['answer'],second['answer'])
        self.assertEqual(second['job']['attempts'],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM answer_jobs').fetchone()[0],1)

    def test_saved_answer_cannot_be_overwritten_with_a_different_selection(self):
        self.note('项目第一段');self.note('项目第二段');self.configure();cid,turn=self.question()
        first_id='note/'+turn['response']['sources'][0]['id']
        first,_=self.run_answer(cid,turn,self.request(cid,turn,[first_id]))
        with self.assertRaises(StoreError):
            self.store.answers.start(cid,turn['id'],self.request(cid,turn),background=False)
        self.assertEqual(self.store.answers.status(cid,turn['id'])['answer'],first['answer'])

    def test_failure_requires_explicit_retry_and_keeps_same_job_identity(self):
        self.note();self.configure();cid,turn=self.question();body=self.request(cid,turn)
        with patch.object(self.store.analysis,'request_json',side_effect=StoreError(502,'受控服务不可用。')):
            failed=self.store.answers.start(cid,turn['id'],body,background=False)
        self.assertEqual(failed['job']['status'],'failed')
        with self.store.db:self.store.db.execute("UPDATE answer_jobs SET status='running'")
        self.store.close();self.store=Store(self.root/'node')
        with patch.object(self.store.analysis,'request_json') as call:
            interrupted=self.store.answers.status(cid,turn['id'])
            self.assertEqual(interrupted['job']['status'],'interrupted')
            self.assertIsNone(interrupted['answer'])
            self.assertFalse(interrupted['configured'])
            call.assert_not_called()
        self.configure()
        result,call=self.run_answer(cid,turn,body)
        self.assertEqual(call.call_count,1);self.assertEqual(result['job']['id'],failed['job']['id'])
        self.assertEqual(result['job']['attempts'],2)

    def test_network_wait_releases_database_lock_and_duplicate_start_does_not_send(self):
        self.note();self.configure();cid,turn=self.question();body=self.request(cid,turn)
        entered=threading.Event();release=threading.Event()
        def slow(*args):
            entered.set();self.assertTrue(release.wait(3));return self.reply(*args)
        with patch.object(self.store.analysis,'request_json',side_effect=slow) as call:
            try:
                first=self.store.answers.start(cid,turn['id'],body)
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.store.lock.acquire(timeout=1));self.store.lock.release()
                self.assertEqual(self.store.answers.start(cid,turn['id'],body)['job']['id'],first['job']['id'])
                self.store.answers.configure({'clear':True})
            finally:release.set();self.store.answers.worker.join(3)
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.store.answers.status(cid,turn['id'])['job']['status'],'ready')

    def test_export_contains_answer_and_quotes_but_no_credentials(self):
        self.note();self.configure();cid,turn=self.question();result,_=self.run_answer(cid,turn)
        archive=self.store.exchange.export()
        with zipfile.ZipFile(archive) as zipped:
            conversations=json.loads(zipped.read('conversations.json'))
            saved=next(c for c in conversations if c['id']==cid)['turns'][0]['response']['answer']
            self.assertEqual(saved,result['answer'])
            for name in zipped.namelist():self.assertNotIn(b'synthetic-answer-key',zipped.read(name))
        self.assertNotIn('synthetic-answer-key','\n'.join(self.store.db.iterdump()))

    def test_real_loopback_https_protocol_and_output_rejection(self):
        from analysis_http_fixture import LocalModelFixture
        fixture=LocalModelFixture(self.root/'answer-https')
        try:
            self.note()
            self.store.answers.configure({'endpoint':fixture.endpoint,'model':'synthetic-answer','key':'synthetic-model-key'})
            for mode,expected in [('valid','ready'),('bad_citation','failed'),('uncited','failed'),('malformed','failed'),('incomplete','failed'),('empty','ready')]:
                fixture.set_response(mode);cid,turn=self.question()
                result=self.store.answers.start(cid,turn['id'],self.request(cid,turn),background=False)
                self.assertEqual(result['job']['status'],expected)
                self.assertTrue(fixture.requests[-1]['hasAuthorization'])
            self.assertEqual(len(fixture.requests),6)
        finally:fixture.close()

    def test_external_readers_cannot_configure_or_generate_answers(self):
        self.note();cid,turn=self.question()
        grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['notes'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-answer-device-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            paths=['/v1/answer-session','/v1/conversations/'+cid+'/turns/'+turn['id']+'/answer',
                   '/v1/conversations/'+cid+'/turns/'+turn['id']+'/answer-preview']
            for path in paths:
                for method in ('GET','POST'):
                    client=http.client.HTTPConnection(*server.server_address,timeout=5)
                    client.request(method,path,body='{}' if method=='POST' else None,
                        headers={'Authorization':'Bearer '+grant['token'],'Content-Type':'application/json'})
                    response=client.getresponse();self.assertEqual(response.status,401);response.read();client.close()
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM answer_jobs').fetchone()[0],0)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()

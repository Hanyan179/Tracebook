import datetime as dt
import http.client
import json
from pathlib import Path
import threading
import unittest
import uuid
import test_node as fixtures
from store import Store, StoreError
from server import Server


class KnowledgeTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown
    receive = fixtures.NodeTests.receive
    fixture = fixtures.NodeTests.fixture
    text_etag = fixtures.NodeTests.text_etag

    def note(self, text='合成笔记：项目决定周五复盘。'):
        return self.store.knowledge.save_note({'title':'合成项目笔记','text':text})

    def grant(self, scopes=None):
        return self.store.knowledge.create_grant({'name':'isolated-test','scopes':scopes or ['notes'],'days':1})

    def test_notes_are_files_searchable_editable_and_survive_output_move(self):
        note=self.note()
        self.assertEqual((self.store.output/note['path']).read_text(),note['text'])
        # Daily collections have a noon sort date: before noon this must not outrank
        # the equally relevant original merely because it has a later timestamp.
        with self.store.db:
            self.store.db.execute("UPDATE library_items SET sort_date=9999999999 WHERE kind='daily'")
        self.assertEqual(self.store.knowledge.query({'query':'项目复盘'})['sources'][0]['id'],note['id'])
        updated=self.store.knowledge.save_note({'title':'合成修订','text':'修订内容','etag':note['etag']},note['id'])
        with self.assertRaises(StoreError):
            self.store.knowledge.save_note({'title':'过期','text':'不会覆盖','etag':note['etag']},note['id'])
        original=self.store.output
        self.store.configure({'outputDirectory':str(self.root/'moved')})
        self.assertEqual(self.store.knowledge.note(note['id'])['text'],'修订内容')
        self.assertTrue((original/updated['path']).exists())
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.catalog.query('note',{})['total'],1)

    def test_reader_tokens_are_hashed_scoped_expiring_and_revocable(self):
        grant=self.grant()
        self.assertEqual(self.store.knowledge.authorize(grant['token']),['notes'])
        row=dict(self.store.db.execute('SELECT * FROM reader_grants').fetchone())
        self.assertNotIn(grant['token'],json.dumps(row))
        self.assertNotIn('token',self.store.knowledge.list_grants()[0])
        with self.assertRaises(StoreError):self.store.knowledge.query({'query':'项目','scopes':['daily']},['notes'])
        self.store.knowledge.revoke_grant(grant['id'])
        with self.assertRaises(StoreError):self.store.knowledge.authorize(grant['token'])
        grant=self.grant()
        with self.store.db:self.store.db.execute("UPDATE reader_grants SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(grant['id'],))
        with self.assertRaises(StoreError):self.store.knowledge.authorize(grant['token'])

    def test_pending_ignored_and_archived_memory_are_not_exposed(self):
        rid,_,_=self.receive()
        pending=self.store.add_candidate('合成私人事实 pending',[rid])['id']
        ignored=self.store.add_candidate('合成私人事实 ignored',[rid])['id']
        confirmed=self.store.add_candidate('合成私人事实 confirmed',[rid])['id']
        archived=self.store.add_candidate('合成私人事实 archived',[rid])['id']
        self.store.decide_candidate(ignored,'rejected')
        self.store.decide_candidate(confirmed,'confirmed')
        self.store.decide_candidate(archived,'confirmed')
        self.store.catalog.update('memory',archived,{'revision':0,'archived':True})
        result=self.store.knowledge.query({'query':'私人事实'},['confirmedMemory'])
        self.assertEqual([s['id'] for s in result['sources']],[confirmed])
        for item in [pending,ignored,archived]:
            with self.assertRaises(StoreError):self.store.knowledge.read_source('memory',item,['confirmedMemory'],{})

    def test_content_chunking_version_conflicts_and_scope_enforcement(self):
        note=self.note('合成正文'+('x'*20000))
        first=self.store.knowledge.read_source('note',note['id'],['notes'],{'limit':100})
        self.assertEqual(len(first['text']),100);self.assertEqual(first['nextOffset'],100)
        self.assertNotIn(str(self.root),json.dumps(first))
        self.store.knowledge.save_note({'title':'新内容','text':'新版本','etag':note['etag']},note['id'])
        with self.assertRaises(StoreError):
            self.store.knowledge.read_source('note',note['id'],['notes'],{'offset':100,'version':first['contentVersion']})
        with self.assertRaises(StoreError):self.store.knowledge.read_source('note',note['id'],['daily'],{})
        with self.assertRaises(StoreError):self.store.knowledge.read_source('note','../../secret',['notes'],{})

    def test_chat_is_idempotent_persistent_and_honest_about_retrieval(self):
        self.note()
        chat=self.store.knowledge.create_conversation()
        request={'requestID':str(uuid.uuid4()),'query':'项目什么时候复盘？','language':'zh'}
        first=self.store.knowledge.ask(chat['id'],request)
        second=self.store.knowledge.ask(chat['id'],request)
        self.assertEqual(first['id'],second['id'])
        self.assertFalse(first['response']['generatedAnswer'])
        self.assertEqual(first['response']['mode'],'retrieval')
        self.assertTrue(first['response']['sources'])
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(len(self.store.knowledge.turns(chat['id'])['items']),1)
        with self.assertRaises(StoreError):
            self.store.knowledge.ask(chat['id'],{**request,'query':'不同的请求'})

    def test_date_filter_and_empty_answer_do_not_fabricate_content(self):
        self.note()
        result=self.store.knowledge.query({'query':'never-match-xyzz','language':'en'})
        self.assertEqual(result['sources'],[]);self.assertFalse(result['generatedAnswer'])
        with self.assertRaises(StoreError):
            self.store.knowledge.query({'query':'项目','dateFrom':'2026-10-20','dateTo':'2020-01-01'})
        result=self.store.knowledge.query({'query':'项目','dateTo':'2000-01-01'})
        self.assertEqual(result['sources'],[])

    def test_question_excerpt_finds_specific_statement_after_repeated_background(self):
        fact='本人陈述：我每周五回顾个人工作计划。'
        note=self.note(('合成的阅读背景，不是个人事实。\n'*2200)+fact+'\n'+('其他人的背景记录。\n'*200))
        source=self.store.knowledge.query({'query':'我什么时候回顾个人工作计划？','scopes':['notes']})['sources'][0]
        self.assertEqual(source['id'],note['id'])
        self.assertIn(fact,'\n'.join(source['excerpt'].splitlines()[:7]))
        self.assertEqual(note['text'][source['excerptStart']:source['excerptStart']+len(source['excerpt'])],source['excerpt'])
        self.assertTrue(source['excerptTruncated'])

    def test_long_single_paragraph_does_not_rewind_excerpt_to_the_start(self):
        fact='每周五回顾个人工作计划'
        note=self.note('背景资料。'*11000+fact+'。'+'无关内容。'*1000)
        source=self.store.knowledge.query({'query':'回顾工作计划','scopes':['notes']})['sources'][0]
        self.assertGreater(source['excerptStart'],50000)
        self.assertIn(fact,source['excerpt'][:200])
        self.assertEqual(note['text'][source['excerptStart']:source['excerptStart']+len(source['excerpt'])],source['excerpt'])

    def test_excerpt_offsets_follow_original_text_after_unicode_normalization(self):
        # Case folding and compatibility characters change the indexed length.
        text=' \n\t'+('Straße ﬃ e\u0301 👩🏽‍💻 background\n'*1700)+'我周五整理 ＰＬＡＮＮＩＮＧ 资料。\n'
        note=self.note(text)
        source=self.store.knowledge.query({'query':'planning','scopes':['notes']})['sources'][0]
        self.assertIn('ＰＬＡＮＮＩＮＧ',source['excerpt'])
        self.assertEqual(text[source['excerptStart']:source['excerptStart']+len(source['excerpt'])],source['excerpt'])
        self.assertEqual(source['contentVersion'],self.store.knowledge.read_source('note',note['id'],['notes'],{})['contentVersion'])

    def test_excerpt_keeps_request_budget_and_does_not_change_saved_history(self):
        note=self.note('背景。'*1000+'每周五回顾工作计划。'+'背景。'*1000)
        chat=self.store.knowledge.create_conversation()
        request={'requestID':str(uuid.uuid4()),'query':'回顾工作计划','scopes':['notes']}
        first=self.store.knowledge.ask(chat['id'],request)
        source=first['response']['sources'][0]
        self.assertLessEqual(len(source['excerpt']),1400)
        original=(self.store.output/note['path']).read_text()
        self.store.knowledge.save_note({'title':note['title'],'text':original+'人工补充','etag':note['etag']},note['id'])
        self.assertEqual(self.store.knowledge.ask(chat['id'],request),first)
        self.assertFalse(first['response']['generatedAnswer'])

    def test_http_reader_cannot_call_admin_or_audio_endpoints(self):
        note=self.note();grant=self.grant()
        server=Server(('127.0.0.1',0),self.store,'admin-test-only')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def call(method,path,body=None,token=None):
            connection=http.client.HTTPConnection(*server.server_address,timeout=5)
            headers={'Authorization':'Bearer '+(token or grant['token']),'Content-Type':'application/json'}
            connection.request(method,path,json.dumps(body) if body is not None else None,headers)
            response=connection.getresponse();status=response.status;data=response.read();connection.close()
            return status,json.loads(data)
        try:
            status,result=call('POST','/v1/assistant/query',{'query':'项目'})
            self.assertEqual(status,200);self.assertEqual(result['sources'][0]['id'],note['id'])
            for method,path,body in [('GET','/v1/status',None),('GET','/v1/integrations',None),
                                     ('POST','/v1/notes',{'title':'not allowed','text':'not allowed'}),
                                     ('PUT','/v1/assistant/items/note/'+note['id'],{}),
                                     ('GET','/v1/assistant/items/memory/'+str(uuid.uuid4()),None)]:
                status,_=call(method,path,body);self.assertIn(status,[401,403])
            self.assertEqual(call('GET','/v1/assistant/capabilities',token='wrong-token')[0],401)
            self.store.knowledge.revoke_grant(grant['id'])
            self.assertEqual(call('POST','/v1/assistant/query',{'query':'项目'})[0],401)
        finally:server.shutdown();server.server_close();worker.join()

    def test_conversations_page_back_without_repeating_turns(self):
        self.note();chat=self.store.knowledge.create_conversation()
        for i in range(33):
            self.store.knowledge.ask(chat['id'],{'requestID':str(uuid.uuid4()),'query':'项目 '+str(i)})
        latest=self.store.knowledge.turns(chat['id'])
        previous=self.store.knowledge.turns(chat['id'],latest['nextBefore'])
        self.assertEqual(len(latest['items']),30);self.assertEqual(len(previous['items']),3)
        self.assertFalse(set(x['id'] for x in latest['items']) & set(x['id'] for x in previous['items']))

    def test_note_only_day_is_organized_and_manual_daily_edits_are_preserved(self):
        note=self.note()
        day=self.store.catalog.query('note',{})['items'][0]['day']
        self.assertEqual(self.store.catalog.overview()['dayCount'],1)
        self.store.catch_up(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=2))
        daily=self.store.read_document('daily/'+day+'.md')
        self.assertIn(note['text'],daily['text']);self.assertIn('../notes/'+note['id'],daily['text'])
        self.store.edit_document('daily/'+day+'.md','Human edited daily',daily['etag'])
        self.store.knowledge.save_note({'title':note['title'],'text':'新增笔记版本','etag':note['etag']},note['id'])
        self.assertEqual(self.store.read_document('daily/'+day+'.md')['text'],'Human edited daily')
        self.assertEqual(self.store.catalog.query('daily',{})['items'][0]['noteCount'],1)

    def test_note_title_conflicts_and_broad_memory_query(self):
        note=self.note()
        updated=self.store.knowledge.save_note({'title':'New title','text':note['text'],'etag':note['etag']},note['id'])
        self.assertNotEqual(updated['etag'],note['etag'])
        with self.assertRaises(StoreError):self.store.knowledge.save_note({'title':'Stale title','text':note['text'],'etag':note['etag']},note['id'])
        rid,_,_=self.receive()
        candidate=self.store.add_candidate('Synthetic Friday review',[rid])
        self.store.decide_candidate(candidate['id'],'confirmed')
        for query in ['有哪些已经确认的长期记忆？','Show my confirmed memories']:
            result=self.store.knowledge.query({'query':query,'scopes':['confirmedMemory']})
            self.assertEqual(result['sources'][0]['id'],candidate['id'])

    def test_history_pages_without_hiding_older_conversations(self):
        for _ in range(57):self.store.knowledge.create_conversation()
        page=self.store.knowledge.conversations()
        older=self.store.knowledge.conversations(page[-1]['id'])
        self.assertEqual(len(page),50);self.assertEqual(len(older),7)
        self.assertEqual(len({r['id'] for r in page+older}),57)

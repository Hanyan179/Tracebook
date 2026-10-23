import http.client
import json
import threading
import unittest
import uuid
from unittest.mock import patch
import test_memories as fixtures
from store import StoreError, stamp
from server import Server


class RelatedMemoryTests(unittest.TestCase):
    setUp=fixtures.MemoryTests.setUp
    tearDown=fixtures.MemoryTests.tearDown
    note=fixtures.MemoryTests.note
    reference=fixtures.MemoryTests.reference
    propose=fixtures.MemoryTests.propose
    update=fixtures.MemoryTests.update

    def confirmed(self,text,note=None):
        cid=self.propose(note,text);self.update(cid,state='confirmed');return cid

    def test_wording_matches_unicode_and_does_not_mutate_memories(self):
        old=self.confirmed('CAFÉ: review the weekly plan!')
        new=self.propose(text='cafe\u0301 review the weekly plan')
        before=self.store.db.total_changes
        result=self.store.memories.related(new)
        self.assertEqual(result['items'][0]['id'],old)
        self.assertEqual(result['items'][0]['reasons'],['sameWording'])
        self.assertEqual(self.store.db.total_changes,before)
        self.assertEqual(self.store.memories.detail(new)['state'],'pending')
        self.assertEqual(self.store.memories.relations(new),[])

    def test_chinese_related_different_people_are_only_wording_hints(self):
        mine=self.confirmed('我每周五回顾工作计划。')
        colleague=self.confirmed('同事每周五回顾工作计划。')
        unrelated=self.confirmed('我喜欢在公园拍摄鸟类。')
        new=self.propose(text='我改为每周一回顾工作计划。')
        result=self.store.memories.related(new)
        self.assertEqual({x['id'] for x in result['items']},{mine,colleague})
        self.assertNotIn(unrelated,str(result['items']))
        self.assertTrue(all(x['reasons']==['similarWording'] for x in result['items']))
        self.assertTrue(all(set(x)=={'id','text','memoryRevision','reasons'} for x in result['items']))

    def test_same_source_can_help_without_claiming_same_fact_or_person(self):
        note=self.note('Synthetic shared source with several people and topics.')
        old=self.confirmed('Birdwatching in spring.',note)
        new=self.propose(note,'A colleague prefers jazz.')
        result=self.store.memories.related(new)
        self.assertEqual(result['items'][0]['id'],old)
        self.assertEqual(result['items'][0]['reasons'],['sharedSource'])
        self.assertEqual(self.store.memories.detail(old)['memoryRevision'],2)
        self.assertEqual(self.store.memories.detail(new)['memoryRevision'],1)

    def test_archived_inactive_and_pending_memories_are_excluded(self):
        text='I review the weekly work plan.'
        active=self.confirmed(text);inactive=self.confirmed(text);archived=self.confirmed(text)
        other_pending=self.propose(text=text);new=self.propose(text=text)
        self.update(inactive,state='inactive')
        self.store.catalog.update('memory',archived,{'revision':0,'archived':True})
        result=self.store.memories.related(new)
        self.assertEqual([x['id'] for x in result['items']],[active])
        self.assertEqual(result['totalCount'],1)
        self.update(new,state='confirmed')
        with self.assertRaises(StoreError) as error:self.store.memories.related(new)
        self.assertEqual(error.exception.status,409)
        self.store.catalog.update('memory',other_pending,{'revision':0,'archived':True})
        with self.assertRaises(StoreError):self.store.memories.related(other_pending)

    def test_short_or_unrelated_wording_does_not_fill_empty_results(self):
        self.confirmed('OK');self.confirmed('A visit to the museum.')
        result=self.store.memories.related(self.propose(text='OK'))
        self.assertEqual(result['items'],[])
        self.assertEqual(result['checkedCount'],2)
        self.assertFalse(result['truncated'])

    def test_bounded_recent_comparison_and_result_count_are_disclosed(self):
        new=self.propose(text='I review the weekly work plan.')
        with self.store.lock,self.store.db:
            for i in range(503):
                cid=str(uuid.uuid4()).upper();now=stamp()
                self.store.memories.insert(cid,'I review the weekly work plan.',[],now)
                self.store.db.execute("UPDATE candidates SET state='confirmed' WHERE id=?",(cid,))
        result=self.store.memories.related(new)
        self.assertEqual(len(result['items']),5)
        self.assertEqual(result['checkedCount'],500)
        self.assertEqual(result['totalCount'],503)
        self.assertTrue(result['truncated'])

    def test_recommendation_does_not_bypass_fresh_pair_review(self):
        old=self.confirmed('I review the weekly work plan.');new=self.propose(text='I review the weekly work plan.')
        hint=self.store.memories.related(new)['items'][0]
        self.update(old,text='A changed plan requires a new review.')
        with self.assertRaises(StoreError) as stale:
            self.store.memories.resolve(new,{'targetID':old,'memoryRevision':1,'targetRevision':hint['memoryRevision'],'relation':'replace','requestID':str(uuid.uuid4())})
        self.assertEqual(stale.exception.status,409)
        self.assertEqual(self.store.memories.detail(old)['state'],'confirmed')
        self.assertEqual(self.store.memories.detail(new)['state'],'pending')

    def test_related_endpoint_requires_device_token_and_sends_nothing(self):
        self.confirmed('Weekly review on Friday.');new=self.propose(text='Weekly review on Monday.')
        grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-related-token')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch('urllib.request.urlopen',side_effect=AssertionError('No external request')):
                for token,expected in [(grant['token'],401),(server.token,200)]:
                    client=http.client.HTTPConnection(*server.server_address,timeout=5)
                    client.request('GET','/v1/candidates/'+new+'/related',headers={'Authorization':'Bearer '+token})
                    response=client.getresponse();self.assertEqual(response.status,expected);raw=response.read();client.close()
                    if expected==200:self.assertEqual(len(json.loads(raw)['items']),1)
        finally:server.shutdown();server.server_close();worker.join()


if __name__=='__main__':unittest.main()

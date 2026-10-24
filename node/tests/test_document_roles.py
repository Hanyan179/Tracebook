import base64
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch
import test_node as fixtures
from test_pdf_documents import pdf_bytes
from documents import NAMESPACE
from store import Store, StoreError
from backup import create_backup, restore_backup


class DocumentRoleTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown

    def payload(self, role=None, text='I review my synthetic orchard every Monday.'):
        value={'name':'source.md','sourcePath':'/synthetic/source.md','text':text}
        if role is not None:value['contentRole']=role
        return value

    def imported(self, role=None, text='I review my synthetic orchard every Monday.'):
        preview=self.store.documents.preview({'files':[self.payload(role,text)]})
        self.store.documents.commit(preview['id'])
        role_value=role or 'unknown'
        return next(item for item in self.store.documents.listing({})['items'] if item['contentRole']==role_value)

    def request(self, source):
        return {'contentVersion':source['contentVersion'],'sendToModel':True,
                'model':'synthetic-model','endpoint':'https://synthetic.invalid/v1'}

    def configure(self):
        self.store.analysis.configure({'model':'synthetic-model','endpoint':'https://synthetic.invalid/v1','key':'synthetic-role-test-only'})

    def test_unclassified_imports_keep_existing_identity_and_never_infer_from_name(self):
        value=self.payload();value['name']='model-generated-summary.md'
        preview=self.store.documents.preview({'files':[value]})
        self.assertEqual(preview['sourceRoles'][0]['contentRole'],'unknown')
        self.store.documents.commit(preview['id'])
        source=self.store.documents.listing({})['items'][0]
        signature=hashlib.sha256(value['text'].encode()).hexdigest()
        self.assertEqual(source['id'],str(uuid.uuid5(NAMESPACE,signature)).upper())
        self.assertEqual(source['contentRole'],'unknown')
        self.assertEqual(source['evidenceType'],'imported_document')
        self.assertIsNone(source['documentInfo'])

    def test_role_change_is_a_new_version_and_preserves_manual_revision(self):
        first=self.imported('generated');path=self.store.output/'documents'/(first['id']+'.md')
        path.write_text('A human correction must remain exactly here.')
        same=self.store.documents.preview({'files':[self.payload('generated')]})
        self.assertEqual(self.store.documents.commit(same['id'])['skipped'],1)
        changed=self.store.documents.preview({'files':[self.payload('user_text')]})
        self.assertEqual(self.store.documents.commit(changed['id'])['versions'],1)
        self.assertEqual(path.read_text(),'A human correction must remain exactly here.')
        sources=self.store.documents.listing({})['items']
        second=next(s for s in sources if s['contentRole']=='user_text')
        self.assertNotEqual(first['id'],second['id'])
        self.assertIn(first['id'],(self.store.output/'documents'/(second['id']+'.md')).read_text())
        self.assertEqual(self.store.documents.content_role(first['id']),'generated')

    def test_invalid_role_rejected_before_import(self):
        for value in (None,False,{},'confirmed','generated\nuser_text'):
            with self.subTest(value=value),self.assertRaises(StoreError) as problem:
                self.store.documents.preview({'files':[{**self.payload(),'contentRole':value}]})
            self.assertEqual(problem.exception.status,400)
        self.assertEqual(self.store.documents.listing({})['count'],0)

    def test_generated_sources_are_readable_and_searchable_but_not_memory_evidence(self):
        source=self.imported('generated');self.configure()
        self.assertEqual(source['evidenceType'],'model_generated_document')
        result=self.store.knowledge.query({'query':'orchard','scopes':['documents']})['sources'][0]
        self.assertEqual(result['contentRole'],'generated')
        saved=self.store.knowledge.read_source('document',source['id'],['documents'],{})
        self.assertIn('synthetic orchard',saved['text'])
        state=self.store.memory_extraction.status('document',source['id'],source['contentVersion'])
        self.assertIn('模型生成',state['eligibilityProblem'])
        with patch.object(self.store.analysis,'request_json') as remote:
            with self.assertRaises(StoreError):
                self.store.memory_extraction.start('document',source['id'],self.request(source),background=False)
            remote.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM memory_extractions').fetchone()[0],0)
        quote='I review my synthetic orchard every Monday.'
        with self.assertRaises(StoreError):
            self.store.memories.add('Unverified proposal.',evidence=[{'kind':'document','id':source['id'],
                'contentVersion':source['contentVersion'],'quote':quote,'quoteStart':saved['text'].index(quote)}])
        self.assertEqual(self.store.candidates(),[])
        with self.assertRaises(StoreError):
            self.store.knowledge.read_source('document',source['id'],['notes'],{})

    def test_batch_and_segment_paths_cannot_send_generated_text(self):
        source=self.imported('generated','Synthetic model claim.\n'*2000);self.configure()
        refs=[{'kind':'document','id':source['id']}]
        plan=self.store.memory_batches.preview(refs)
        self.assertIn('模型生成',plan['items'][0]['problem']);self.assertEqual(plan['newRequests'],0)
        self.assertEqual(self.store.memory_batches.sources({'kind':'document'})['items'][0]['contentRole'],'generated')
        with patch.object(self.store.analysis,'request_json') as remote:
            with self.assertRaises(StoreError):
                self.store.memory_batches.start({'requestID':str(uuid.uuid4()),'sources':refs,
                    'snapshot':plan['snapshot'],'sendToModel':True},background=False)
            with self.assertRaises(StoreError):
                self.store.memory_segments.prepare('document',source['id'],source['contentVersion'])
            remote.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM memory_batches').fetchone()[0],0)

    def test_original_and_transcript_classification_reach_snapshot_but_stay_pending(self):
        self.configure()
        for role in ('unknown','user_text','transcript'):
            source=self.imported(role)
            response={'candidates':[{'text':'Synthetic weekly review.','quote':'I review my synthetic orchard every Monday.',
                                    'subject':'user','basis':'explicit'}]}
            with patch.object(self.store.analysis,'request_json',return_value=response) as remote:
                state=self.store.memory_extraction.start('document',source['id'],self.request(source),background=False)
            self.assertEqual(state['status'],'ready')
            self.assertEqual(remote.call_args.args[2]['contentRole'],role)
            self.assertIn('not verified authorship',remote.call_args.args[1])
            self.assertEqual(self.store.memories.detail(state['candidateIDs'][0])['state'],'pending')

    def test_pdf_role_keeps_page_offsets_and_change_detection(self):
        raw=pdf_bytes(['Synthetic original text on page one.'])
        preview=self.store.documents.preview({'files':[{'name':'source.pdf','sourcePath':'/synthetic/source.pdf',
            'contentRole':'transcript','data':base64.b64encode(raw).decode()}]})
        self.assertEqual(preview['pdfs'][0]['pages'],1)
        self.store.documents.commit(preview['id']);source=self.store.documents.listing({})['items'][0]
        info=source['documentInfo'];self.assertTrue(info['rangesValid']);self.assertEqual(source['contentRole'],'transcript')
        text=(self.store.output/'documents'/(source['id']+'.md')).read_text();page=info['pages'][0]
        self.assertIn('Synthetic original text',text[page['start']:page['end']])
        (self.store.output/'documents'/(source['id']+'.md')).write_text('Human edit.\n'+text)
        current=self.store.documents.listing({})['items'][0]
        self.assertFalse(current['documentInfo']['rangesValid']);self.assertEqual(current['contentRole'],'transcript')

    def test_answer_preview_carries_generated_classification_without_promoting_it(self):
        self.imported('generated')
        cid=self.store.knowledge.create_conversation()['id']
        turn=self.store.knowledge.ask(cid,{'query':'orchard','scopes':['documents'],'requestID':str(uuid.uuid4())})
        source=turn['response']['sources'][0]
        plan=self.store.answers.preview(cid,turn['id'],{'sources':['document/'+source['id']]})
        sent=plan['items'][0]['source']
        self.assertEqual(sent['contentRole'],'generated');self.assertEqual(sent['evidenceType'],'model_generated_document')
        self.assertEqual(self.store.candidates(),[])

    def test_backup_restore_retains_classification_and_original_text(self):
        source=self.imported('generated');path='documents/'+source['id']+'.md';original=(self.store.output/path).read_bytes()
        self.store.close();archive=self.root/'roles.zip';report=create_backup(self.root/'node',archive)
        target=self.root/'restored';restore_backup(archive,target,report['archiveSHA256'],self.root)
        self.store=Store(target)
        self.assertEqual(self.store.documents.content_role(source['id']),'generated')
        self.assertEqual((self.store.output/path).read_bytes(),original)
        self.assertIsNotNone(self.store.memory_extraction.status('document',source['id'],source['contentVersion'])['eligibilityProblem'])


if __name__=='__main__':unittest.main()

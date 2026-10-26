import hashlib
import http.client
import json
from pathlib import Path
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch
import test_transcription as fixtures
from backup import create_backup,inspect_backup,restore_backup
from library_location import inspect_library,prepare_library
from server import Server
from store import Store,StoreError,file_hash


class TranscriptReviewTests(unittest.TestCase):
    setUp=fixtures.TranscriptionTests.setUp
    tearDown=fixtures.TranscriptionTests.tearDown
    receive=fixtures.TranscriptionTests.receive

    def conflict(self):
        rid,m,a=self.receive();self.store.transcription.enqueue(rid)
        doc=self.store.read_document('transcripts/'+rid+'.md')
        self.store.save_transcript(rid,'Synthetic manual Monday note.',doc['etag'])
        self.store.transcription.run_once()
        self.assertEqual(self.store.transcription.status(rid)['status'],'conflict')
        return rid,m,a

    def decision(self,rid,action='merge',text='Synthetic merged Monday review with retained context.'):
        pair=self.store.transcript_reviews.preview(rid)
        body={'requestID':str(uuid.uuid4()),'snapshot':pair['snapshot'],'action':action,'nodeID':pair['nodeID']}
        if action=='merge':body['text']=text
        return body

    def test_each_decision_preserves_original_audio_machine_output_and_memory_facts(self):
        for action in ['keep','generated','merge']:
            with self.subTest(action=action):
                rid,m,a=self.conflict();pair=self.store.transcript_reviews.preview(rid);body=self.decision(rid,action)
                receipt=self.store.transcript_reviews.resolve(rid,body)
                text=self.store.read_document(pair['path'])['text']
                expected=pair['original']['text'] if action=='keep' else pair['generated']['text'] if action=='generated' else body['text']
                self.assertEqual(text,expected)
                self.assertEqual(self.store.read_document(pair['generated']['path'])['text'],pair['generated']['text'])
                if action!='keep':self.assertEqual(self.store.read_document(receipt['backupPath'])['text'],pair['original']['text'])
                self.assertEqual(file_hash(self.store.audio_path(rid)),m['audioSHA256'])
                self.assertFalse(self.store.candidates());self.assertEqual(self.store.transcription.status(rid)['status'],'ready')
                self.assertEqual(self.store.transcription.status(rid)['fileReview']['action'],action)
                self.assertTrue(self.store.transcription.status(rid)['fileReview']['matchesCurrent'])
                self.assertEqual(self.store.transcript_reviews.resolve(rid,body),receipt)
                self.assertFalse(self.store.transcription.run_once())
        self.assertEqual(len(self.model.requests),3)

    def test_search_daily_and_source_version_follow_merged_text(self):
        rid,_,_=self.conflict();pair=self.store.transcript_reviews.preview(rid);body=self.decision(rid,text='Synthetic uniquelymergedtoken manual text.')
        self.store.transcript_reviews.resolve(rid,body)
        indexed=self.store.db.execute("SELECT body FROM library_items WHERE kind='recording' AND id=?",(rid,)).fetchone()[0]
        self.assertEqual(indexed,body['text']);self.assertIn(body['text'],self.store.read_document('daily/2026-10-19.md')['text'])
        self.assertEqual(self.store.memories.source('recording',rid)[2],hashlib.sha256(body['text'].encode()).hexdigest())
        self.assertEqual(self.store.read_document(pair['generated']['path'])['text'],pair['generated']['text'])

    def test_existing_memory_evidence_and_revisions_keep_the_pre_merge_source(self):
        rid,_,_=self.conflict()
        cid=self.store.add_candidate('Synthetic Monday preference.',[rid])['id']
        self.store.decide_candidate(cid,'confirmed')
        before=self.store.memories.history(cid)
        evidence=self.store.memories.data(self.store.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone())['evidence']
        result=self.store.transcript_reviews.resolve(rid,self.decision(rid))
        self.assertEqual(self.store.memories.history(cid),before)
        self.assertEqual(self.store.memories.data(self.store.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone())['evidence'],evidence)
        self.assertEqual(self.store.read_document(result['backupPath'])['text'],'Synthetic manual Monday note.')
        self.assertNotEqual(self.store.memories.source('recording',rid)[2],evidence[0]['contentVersion'])

    def test_preview_change_blocks_commit_without_losing_current_or_creating_operation(self):
        rid,_,_=self.conflict();body=self.decision(rid);path=self.store.output/('transcripts/'+rid+'.md')
        path.write_text('Synthetic concurrent edit.')
        with self.assertRaises(StoreError) as error:self.store.transcript_reviews.resolve(rid,body)
        self.assertEqual(error.exception.status,409);self.assertEqual(path.read_text(),'Synthetic concurrent edit.')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM transcript_reviews').fetchone()[0],0)

    def test_changed_generated_and_wrong_library_are_rejected(self):
        rid,_,_=self.conflict();pair=self.store.transcript_reviews.preview(rid);body=self.decision(rid)
        with self.assertRaises(StoreError):self.store.transcript_reviews.resolve(rid,{**body,'nodeID':str(uuid.uuid4())})
        (self.store.output/pair['generated']['path']).write_text('Synthetic changed machine output.')
        with self.assertRaises(StoreError):self.store.transcript_reviews.resolve(rid,body)
        self.assertEqual(self.store.read_document(pair['path'])['text'],pair['original']['text'])

    def test_editing_current_transcript_keeps_machine_version_available_for_review(self):
        rid,_,_=self.conflict();pair=self.store.transcript_reviews.preview(rid);stale=self.decision(rid)
        self.store.save_transcript(rid,'Synthetic later editor change.',pair['original']['etag'])
        with self.assertRaises(StoreError):self.store.transcript_reviews.resolve(rid,stale)
        refreshed=self.store.transcript_reviews.preview(rid)
        self.assertEqual(refreshed['original']['text'],'Synthetic later editor change.')
        self.assertEqual(refreshed['generated']['text'],pair['generated']['text'])
        result=self.store.transcript_reviews.resolve(rid,self.decision(rid))
        self.assertEqual(self.store.read_document(result['backupPath'])['text'],'Synthetic later editor change.')
        self.assertEqual(len(self.model.requests),1)

    def test_empty_merge_is_explicit_and_does_not_trigger_retranscription(self):
        rid,_,_=self.conflict();self.store.transcript_reviews.resolve(rid,self.decision(rid,text=''))
        self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'],'')
        self.assertEqual(self.store.transcription.enqueue(rid)['status'],'ready');self.assertFalse(self.store.transcription.run_once())
        self.assertEqual(len(self.model.requests),1)

    def test_io_failure_before_write_can_retry_same_operation(self):
        rid,_,_=self.conflict();pair=self.store.transcript_reviews.preview(rid);body=self.decision(rid);atomic=self.store._atomic
        def failing(path,raw):
            if path==self.store.output/pair['path']:raise OSError('Synthetic unavailable target')
            return atomic(path,raw)
        with patch.object(self.store,'_atomic',side_effect=failing),self.assertRaises(OSError):self.store.transcript_reviews.resolve(rid,body)
        self.assertEqual(self.store.transcript_reviews.preview(rid)['pendingOperation']['requestID'],body['requestID'].upper())
        with self.assertRaises(StoreError):self.store.transcript_reviews.resolve(rid,self.decision(rid))
        result=self.store.transcript_reviews.retry(rid,{'requestID':body['requestID'],'nodeID':body['nodeID']})
        self.assertEqual(self.store.read_document(result['backupPath'])['text'],pair['original']['text'])
        self.assertEqual(self.store.read_document(pair['path'])['text'],body['text'])
        self.assertEqual(len(list((self.store.output/'.history'/pair['path']).glob('review-*'))),1)

    def test_restart_after_source_write_recovers_derived_files_and_receipt_without_model(self):
        rid,_,_=self.conflict();body=self.decision(rid)
        with patch.object(self.store,'build_daily',side_effect=OSError('Synthetic delivery failure')),self.assertRaises(OSError):self.store.transcript_reviews.resolve(rid,body)
        self.store.close();self.store=Store(self.root/'node')
        result=self.store.transcript_reviews.retry(rid,{'requestID':body['requestID'],'nodeID':body['nodeID']})
        self.assertEqual(result['action'],'merge');self.assertIn(body['text'],self.store.read_document('daily/2026-10-19.md')['text'])
        self.assertEqual(self.store.transcription.status(rid)['status'],'ready');self.assertEqual(len(self.model.requests),1)

    def test_edit_during_backup_is_preserved_and_operation_blocked(self):
        rid,_,_=self.conflict();body=self.decision(rid);path=self.store.output/('transcripts/'+rid+'.md');atomic=self.store._atomic
        def changing(target,raw):
            result=atomic(target,raw)
            if target.name.startswith('review-'):path.write_text('Synthetic edit during backup.')
            return result
        with patch.object(self.store,'_atomic',side_effect=changing),self.assertRaises(StoreError):self.store.transcript_reviews.resolve(rid,body)
        self.assertEqual(path.read_text(),'Synthetic edit during backup.')
        self.assertEqual(self.store.db.execute('SELECT state FROM transcript_reviews').fetchone()[0],'blocked')
        self.assertIsNone(self.store.transcript_reviews.pending(rid))

    def test_idempotence_rejects_different_text_and_cross_record_retries(self):
        rid,_,_=self.conflict();body=self.decision(rid);self.store.transcript_reviews.resolve(rid,body)
        with self.assertRaises(StoreError):self.store.transcript_reviews.resolve(rid,{**body,'text':'Different edit.'})
        other,_,_=self.conflict()
        with self.assertRaises(StoreError):self.store.transcript_reviews.retry(other,{'requestID':body['requestID'],'nodeID':body['nodeID']})

    def test_large_invalid_and_linked_files_do_not_get_applied(self):
        rid,_,_=self.conflict();body=self.decision(rid);path=self.store.output/('transcripts/'+rid+'.md')
        for text in [None,123,'x'*(1024*1024+1),'bad\0text']:
            with self.assertRaises(StoreError):self.store.transcript_reviews.resolve(rid,{**body,'text':text})
        path.write_bytes(b'x'*(1024*1024+1))
        with self.assertRaises(StoreError) as error:self.store.transcript_reviews.preview(rid)
        self.assertEqual(error.exception.status,413)
        path.unlink();path.symlink_to(self.store.output/'memory.md')
        with self.assertRaises(OSError):self.store.transcript_reviews.preview(rid)

    def test_current_manual_edit_after_decision_is_not_labelled_as_same_reviewed_version(self):
        rid,_,_=self.conflict();self.store.transcript_reviews.resolve(rid,self.decision(rid))
        (self.store.output/('transcripts/'+rid+'.md')).write_text('Later manual edit.')
        self.assertFalse(self.store.transcription.status(rid)['fileReview']['matchesCurrent'])

    def test_backup_roundtrip_keeps_decision_history_and_blocks_unfinished_writes(self):
        rid,_,_=self.conflict();body=self.decision(rid);receipt=self.store.transcript_reviews.resolve(rid,body)
        other,_,_=self.conflict();pending=self.decision(other)
        with patch.object(self.store,'_atomic',side_effect=OSError('Synthetic no space')),self.assertRaises(OSError):self.store.transcript_reviews.resolve(other,pending)
        self.store.close();archive=self.root/'review.zip';result=create_backup(self.root/'node',archive)
        restored=self.root/'restored';restore_backup(archive,restored,result['archiveSHA256'],self.root)
        copy=Store(restored)
        try:
            self.assertEqual(copy.transcription.status(rid)['fileReview']['action'],'merge')
            self.assertEqual(copy.read_document(receipt['backupPath'])['text'],'Synthetic manual Monday note.')
            row=copy.db.execute('SELECT state,root FROM transcript_reviews WHERE id=?',(pending['requestID'].upper(),)).fetchone()
            self.assertEqual(row['state'],'blocked');self.assertEqual(row['root'],str(copy.output))
            self.assertEqual(copy.read_document('transcripts/'+other+'.md')['text'],'Synthetic manual Monday note.')
        finally:copy.close()

    def test_archive_copy_maps_review_receipts_without_changing_content(self):
        rid,_,_=self.conflict();body=self.decision(rid);receipt=self.store.transcript_reviews.resolve(rid,body)
        self.store.configure({'outputDirectory':str(self.root/'new-archive')})
        self.assertEqual(self.store.transcription.status(rid)['fileReview']['action'],'merge')
        self.assertEqual(self.store.transcript_reviews.resolve(rid,body),receipt)
        self.assertEqual(self.store.read_document(receipt['backupPath'])['text'],'Synthetic manual Monday note.')

    def test_version_four_backup_remains_restorable_with_empty_review_history(self):
        rid,_,_=self.conflict();self.store.close();archive=self.root/'latest.zip';create_backup(self.root/'node',archive)
        with zipfile.ZipFile(archive) as z:members={name:z.read(name) for name in z.namelist()}
        manifest=json.loads(members['manifest.json']);manifest['version']=4
        manifest['tables'].pop('memory_review_batches');members.pop('tables/memory_review_batches.jsonl')
        manifest['files'] = [item for item in manifest['files'] if item['path'] != 'tables/memory_review_batches.jsonl']
        manifest['tables'].pop('transcript_reviews');members.pop('tables/transcript_reviews.jsonl')
        manifest['files']=[f for f in manifest['files'] if f['path']!='tables/transcript_reviews.jsonl']
        members['manifest.json']=json.dumps(manifest).encode();legacy=self.root/'version-four.zip'
        with zipfile.ZipFile(legacy,'w') as z:
            for name,raw in members.items():z.writestr(name,raw)
        checked=inspect_backup(legacy,self.root);destination=self.root/'legacy-restored'
        restore_backup(legacy,destination,checked['archiveSHA256'],self.root);copy=Store(destination)
        try:
            self.assertEqual(copy.db.execute('SELECT count(*) FROM transcript_reviews').fetchone()[0],0)
            self.assertEqual(copy.transcription.status(rid)['status'],'conflict')
            self.assertEqual(copy.transcript_reviews.preview(rid)['original']['text'],'Synthetic manual Monday note.')
        finally:copy.close()

    def test_explicit_library_selection_requires_new_review_instead_of_resuming_old_write(self):
        rid,_,_=self.conflict();body=self.decision(rid)
        with patch.object(self.store,'_atomic',side_effect=OSError('Synthetic storage failure')),self.assertRaises(OSError):
            self.store.transcript_reviews.resolve(rid,body)
        self.store.close();preview=inspect_library(self.root/'node')
        prepare_library(self.root/'node',preview['snapshot'])
        self.store=Store(self.root/'node')
        self.assertEqual(self.store.db.execute('SELECT state FROM transcript_reviews').fetchone()[0],'blocked')
        self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'],'Synthetic manual Monday note.')
        self.assertEqual(self.store.transcription.status(rid)['status'],'conflict')

    def test_device_routes_do_not_expand_external_read_grants(self):
        rid,_,_=self.conflict();body=self.decision(rid,'keep');grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['transcripts'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-review-token');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for method,suffix,data in [('GET','review',None),('POST','review',body),('POST','review-retry',{'nodeID':body['nodeID'],'requestID':body['requestID']})]:
                for token,expected in [(grant['token'],401),(server.token,200)]:
                    c=http.client.HTTPConnection(*server.server_address,timeout=5);c.request(method,'/v1/transcriptions/'+rid+'/'+suffix,
                        body=json.dumps(data) if data else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    r=c.getresponse();value=json.loads(r.read());c.close();self.assertEqual(r.status,expected,value)
        finally:server.shutdown();server.server_close();thread.join()

    def test_http_storage_failure_reports_retryable_reason_and_preserves_operation(self):
        rid,_,_=self.conflict();body=self.decision(rid);server=Server(('127.0.0.1',0),self.store,'synthetic-review-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post():
            c=http.client.HTTPConnection(*server.server_address,timeout=5)
            c.request('POST','/v1/transcriptions/'+rid+'/review',body=json.dumps(body),
                headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'})
            r=c.getresponse();value=json.loads(r.read());c.close();return r.status,value
        try:
            with patch.object(self.store,'_atomic',side_effect=OSError('Synthetic unavailable storage')):
                status,result=post()
            self.assertEqual(status,503);self.assertIn('资料夹连接和权限',result['error'])
            self.assertEqual(self.store.transcript_reviews.pending(rid)['requestID'],body['requestID'].upper())
            self.assertEqual(post()[0],200)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM transcript_reviews').fetchone()[0],1)
        finally:server.shutdown();server.server_close();thread.join()

    def test_http_accepts_valid_text_limit_even_when_json_escaping_expands_request(self):
        rid,_,_=self.conflict();body=self.decision(rid,text='\n'*(1024*1024))
        encoded=json.dumps(body);self.assertGreater(len(encoded.encode()),2*1024*1024)
        server=Server(('127.0.0.1',0),self.store,'synthetic-review-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            c=http.client.HTTPConnection(*server.server_address,timeout=15)
            c.request('POST','/v1/transcriptions/'+rid+'/review',body=encoded,
                headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'})
            r=c.getresponse();value=json.loads(r.read());c.close();self.assertEqual(r.status,200,value)
            self.assertEqual(self.store.read_document('transcripts/'+rid+'.md')['text'],body['text'])
        finally:server.shutdown();server.server_close();thread.join()

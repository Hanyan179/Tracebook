import fcntl
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch

import test_node as fixtures
import test_exchange as exchange_fixtures
import test_local_sources as local_fixtures
import test_answers as answer_fixtures
from backup import BackupError, create_backup, inspect_backup, restore_backup, TABLES
from store import Store, file_hash
from server import Server


class BackupTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown
    fixture = fixtures.NodeTests.fixture
    receive = fixtures.NodeTests.receive
    text_etag = fixtures.NodeTests.text_etag
    folder = local_fixtures.LocalSourceTests.folder
    write = local_fixtures.LocalSourceTests.write
    meta = local_fixtures.LocalSourceTests.meta
    user = local_fixtures.LocalSourceTests.user
    collect = local_fixtures.LocalSourceTests.collect

    def seed(self):
        rid, manifest, _ = self.receive()
        self.store.save_transcript(rid, 'Synthetic orchard review on Friday.', self.text_etag(rid))
        note = self.store.knowledge.save_note({'title': 'Restoration sample', 'text': 'Original note.'})
        self.store.knowledge.save_note({'title': note['title'], 'text': 'Human revision.', 'etag': note['etag']}, note['id'])
        preview = self.store.documents.preview({'files': [{'name':'sample.md','sourcePath':'/chosen/sample.md',
            'modifiedAt':'2026-10-24T00:00:00Z','text':'Synthetic imported orchard source.'}]})
        self.store.documents.commit(preview['id'])
        cid = self.store.add_candidate('Review the orchard on Friday.', [rid])['id']
        self.store.decide_candidate(cid, 'confirmed')
        original_memory = (self.store.output/'memory.md').read_bytes()
        (self.store.output/'memory.md').write_text('Human edited memory file.')
        self.store.export_memory()
        conversation = self.store.knowledge.create_conversation()
        self.store.knowledge.ask(conversation['id'], {'query':'orchard','requestID':str(uuid.uuid4())})
        imported = self.store.exchange.preview(exchange_fixtures.ExchangeTests.payload(self))
        self.store.exchange.commit(imported['id'])
        self.store.knowledge.create_grant({'name':'synthetic reader','scopes':['notes'],'days':1})
        # Deliberately not an application-owned setting or file.
        with self.store.db:self.store.db.execute("INSERT INTO settings VALUES ('testCredential','never-back-up-this-secret')")
        (self.store.data/'connection-token').write_text('synthetic-connection-secret')
        (self.store.output/'unrelated.key').write_text('synthetic-unrelated-secret')
        org = self.store.output/'organization'/'categories.json'
        org.parent.mkdir(exist_ok=True);org.write_text('{"synthetic":"category metadata"}')
        return rid, cid, note['id'], original_memory

    def backup(self):
        self.store.close()
        destination = self.root/'saved.zip'
        report = create_backup(self.root/'node', destination)
        return destination, report

    def rewrite(self, original, mutate, name='changed.zip'):
        with zipfile.ZipFile(original) as source:members={n:source.read(n) for n in source.namelist()}
        manifest=json.loads(members.pop('manifest.json'))
        mutate(members, manifest)
        manifest['files']=[{'path':n,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for n,data in members.items()]
        result=self.root/name
        with zipfile.ZipFile(result,'w') as archive:
            for n,data in members.items():archive.writestr(n,data)
            archive.writestr('manifest.json',json.dumps(manifest))
        return result

    def test_round_trip_preserves_files_history_sources_and_manual_edits(self):
        rid,cid,nid,_=self.seed()
        before={p.relative_to(self.store.output).as_posix():p.read_bytes() for p in self.store.output.rglob('*') if p.is_file() and p.suffix in ('.md','.json')}
        old_node_id=self.store.setting('nodeID')
        audio=self.store.audio_path(rid).read_bytes()
        archive,report=self.backup()
        self.assertEqual((report['recordings'],report['documents'],report['memories']),(1,1,1))
        with zipfile.ZipFile(archive) as zipped:
            combined=b''.join(zipped.read(name) for name in zipped.namelist())
            for secret in (b'never-back-up-this-secret',b'synthetic-connection-secret',b'synthetic-unrelated-secret'):
                self.assertNotIn(secret,combined)
            self.assertNotIn('tables/reader_grants.jsonl',zipped.namelist())
        target=self.root/'restored'
        restore_backup(archive,target,report['archiveSHA256'],self.root)
        for name,body in before.items():self.assertEqual((target/'Library'/name).read_bytes(),body,name)
        self.assertEqual((target/'recordings'/rid/(rid+'.caf')).read_bytes(),audio)
        self.assertFalse((target/'connection-token').exists())
        self.store=Store(target)
        self.assertNotEqual(self.store.setting('nodeID'),old_node_id)
        self.assertEqual(self.store.setting('dailyEnabled'),'false')
        self.assertEqual(self.store.setting('analysisEnabled'),'false')
        self.assertEqual(self.store.setting('memoryDeliveryPaused'),'true')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM reader_grants').fetchone()[0],0)
        self.assertEqual(self.store.memories.detail(cid)['state'],'confirmed')
        self.assertEqual(self.store.knowledge.note(nid)['text'],'Human revision.')
        self.assertTrue(self.store.knowledge.query({'query':'orchard','scopes':['documents']})['sources'])
        self.store.memories.deliver_pending()
        for name,body in before.items():self.assertEqual((target/'Library'/name).read_bytes(),body,name)
        self.store.memories.deliver_pending(force=True)
        self.assertEqual(self.store.setting('memoryDeliveryPaused'),'false')
        self.assertEqual((target/'Library'/'memory.md').read_text(),'Human edited memory file.')
        self.assertEqual((self.root/'node'/'Library'/'memory.md').read_text(),'Human edited memory file.')
        self.assertEqual(self.store.exchange.preview(exchange_fixtures.ExchangeTests.payload(self))['alreadyImported'],True)

    def test_restore_disables_work_without_discarding_completed_parts(self):
        rid,_,_=self.receive()
        source_id=str(uuid.uuid4()).upper()
        with self.store.db:
            self.store.db.execute('''INSERT INTO speech_jobs(id,checksum,service,status,baseline,output_root,updated_at)
                VALUES (?,?,?,'running',?,?,?)''',(rid,'checksum','synthetic','baseline',str(self.store.output),'2026-10-24T00:00:00Z'))
            self.store.db.execute('INSERT INTO speech_parts VALUES (?,0,0,100,?)',(rid,'Already saved segment.'))
            self.store.db.execute('''INSERT INTO local_sources(id,provider,directory,automatic,last_scan,config)
                VALUES (?,'codex','/do-not-read-this',1,?,'{}')''',(source_id,'2026-10-24T00:00:00Z'))
            self.store.db.execute('INSERT INTO memory_file_operations VALUES (?,?,?,?,?)',
                (str(uuid.uuid4()).upper(),'hash',str(self.store.output),'{}','pending'))
        archive,report=self.backup()
        target=self.root/'restored';restore_backup(archive,target,report['archiveSHA256'],self.root)
        self.store=Store(target)
        self.assertEqual(self.store.db.execute('SELECT status FROM speech_jobs').fetchone()[0],'failed')
        self.assertEqual(self.store.db.execute('SELECT text FROM speech_parts').fetchone()[0],'Already saved segment.')
        source=self.store.db.execute('SELECT directory,automatic FROM local_sources').fetchone()
        self.assertEqual(tuple(source),(str(target.resolve()/'DisconnectedSources'/source_id),0))
        self.assertFalse(Path(source[0]).exists())
        self.assertEqual(self.store.db.execute('SELECT state FROM memory_file_operations').fetchone()[0],'blocked')
        # Reusing this app's saved provider configuration does not authorize work
        # that was queued in the backed-up library.
        with patch.object(self.store.transcription,'request') as speech, patch.object(self.store.analysis,'request_json') as analysis:
            self.store.transcription.configure({'endpoint':'https://speech.invalid/v1','model':'synthetic','key':'synthetic-key'})
            self.store.analysis.configure({'endpoint':'https://text.invalid/v1','model':'synthetic','key':'synthetic-key'})
            self.store.transcription.run_once();self.store.analysis.catch_up()
            speech.assert_not_called();analysis.assert_not_called()

    def test_ready_and_conflicted_speech_states_remain_reviewable(self):
        for status in ('ready','conflict'):
            rid,_,_=self.receive()
            with self.store.db:self.store.db.execute('''INSERT INTO speech_jobs(id,checksum,service,status,baseline,output_root,updated_at)
                VALUES (?,?,?,?,?,?,?)''',(rid,'checksum','synthetic',status,'baseline',str(self.store.output),'2026-10-24T00:00:00Z'))
        archive,report=self.backup();target=self.root/'restored'
        restore_backup(archive,target,report['archiveSHA256'],self.root)
        self.store=Store(target)
        self.assertEqual({r[0] for r in self.store.db.execute('SELECT status FROM speech_jobs')},{'ready','conflict'})

    def test_backup_refuses_live_lock_and_existing_destination(self):
        with (self.store.data/'instance.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BackupError):create_backup(self.store.data,self.root/'live.zip')
        self.assertFalse((self.root/'live.zip').exists())
        destination=self.root/'existing.zip';destination.write_bytes(b'Keep this backup.')
        with self.assertRaises(BackupError):create_backup(self.store.data,destination)
        self.assertEqual(destination.read_bytes(),b'Keep this backup.')

    def test_change_during_backup_aborts_without_reverting_user_edit(self):
        self.seed();self.store.close()
        target=self.root/'node'/'Library'/'memory.md'
        def change(_):target.write_text('Concurrent human edit.')
        with self.assertRaises(BackupError):create_backup(self.root/'node',self.root/'partial.zip',progress=change)
        self.assertFalse((self.root/'partial.zip').exists())
        self.assertEqual(target.read_text(),'Concurrent human edit.')

    def test_cancelled_backup_removes_only_its_new_file(self):
        self.seed();self.store.close()
        def cancel(_):raise BackupError('cancelled')
        with self.assertRaises(BackupError):create_backup(self.root/'node',self.root/'cancelled.zip',progress=cancel)
        self.assertFalse((self.root/'cancelled.zip').exists())
        self.assertEqual(list((self.root/'node'/'staging').iterdir()),[])

    def test_corrupt_or_missing_audio_is_not_reported_as_backup_success(self):
        rid,_,_=self.receive();self.store.close()
        (self.root/'node'/'recordings'/rid/(rid+'.caf')).write_bytes(b'corrupt')
        with self.assertRaises(BackupError):create_backup(self.root/'node',self.root/'bad.zip')
        self.assertFalse((self.root/'bad.zip').exists())

    def test_unknown_table_and_symlink_rejected_without_touching_target(self):
        with self.store.db:self.store.db.execute('CREATE TABLE future_owned_data(id TEXT)')
        self.store.close()
        with self.assertRaises(BackupError):create_backup(self.root/'node',self.root/'future.zip')
        db=sqlite3.connect(self.root/'node'/'node.sqlite3');db.execute('DROP TABLE future_owned_data');db.commit();db.close()
        outside=self.root/'outside.md';outside.write_text('Untouched.')
        (self.root/'node'/'Library'/'notes').mkdir(exist_ok=True)
        (self.root/'node'/'Library'/'notes'/'linked.md').symlink_to(outside)
        with self.assertRaises(BackupError):create_backup(self.root/'node',self.root/'linked.zip')
        self.assertEqual(outside.read_text(),'Untouched.')

    def test_destination_alias_cannot_put_backup_inside_library(self):
        self.store.close()
        alias=self.root/'alias';alias.symlink_to(self.root/'node'/'Library',target_is_directory=True)
        with self.assertRaises(BackupError):create_backup(self.root/'node',alias/'nested.zip')
        self.assertFalse((alias/'nested.zip').exists())

    def test_malicious_paths_duplicates_and_symlinks_are_rejected(self):
        archive,report=self.backup()
        for index,name in enumerate(('../outside.md','library/../outside.md','/outside.md','library\\notes\\x.md','connection-token')):
            bad=self.rewrite(archive,lambda members,_:members.update({name:b'bad'}),str(index)+'.zip')
            with self.assertRaises(BackupError):inspect_backup(bad,self.root)
        for index,member in enumerate(('library/MEMORY.md','library/notes/symlink.md')):
            bad=self.root/('evil'+str(index)+'.zip');shutil.copyfile(archive,bad)
            with zipfile.ZipFile(bad,'a') as z:
                info=zipfile.ZipInfo(member)
                if index:info.external_attr=(stat.S_IFLNK|0o777)<<16
                z.writestr(info,b'outside')
            with self.assertRaises(BackupError):inspect_backup(bad,self.root)
        self.assertFalse((self.root/'outside.md').exists())

    def test_missing_table_wrong_row_count_and_schema_are_rejected(self):
        archive,_=self.backup()
        changes=[lambda members,_:members.pop('tables/notes.jsonl'),
            lambda _,manifest:manifest['tables']['notes'].update(rows=100),
            lambda _,manifest:manifest['tables']['notes'].update(columns=['injected_sql']),
            lambda _,manifest:manifest.update(version=True)]
        for index,change in enumerate(changes):
            bad=self.rewrite(archive,change,str(index)+'.zip')
            with self.assertRaises(BackupError):inspect_backup(bad,self.root)

    def test_preview_change_failed_restore_and_existing_destination_keep_original(self):
        self.seed();archive,report=self.backup()
        target=self.root/'restored';target.mkdir();(target/'keep').write_text('Do not replace.')
        with self.assertRaises(BackupError):restore_backup(archive,target,report['archiveSHA256'],self.root)
        self.assertEqual((target/'keep').read_text(),'Do not replace.')
        with self.assertRaises(BackupError):restore_backup(archive,self.root/'changed','0'*64,self.root)
        self.assertFalse((self.root/'changed').exists())
        def cancel(value):
            if value['phase']=='restoring':raise BackupError('cancelled')
        with self.assertRaises(BackupError):restore_backup(archive,self.root/'cancelled',report['archiveSHA256'],self.root,progress=cancel)
        self.assertFalse((self.root/'cancelled').exists())
        self.assertEqual(list(self.root.glob('.*-restoring-*')),[])
        self.assertEqual((self.root/'node'/'Library'/'memory.md').read_text(),'Human edited memory file.')

    def test_cli_rejects_bad_zip_without_traceback_or_private_contents(self):
        bad=self.root/'bad.zip';bad.write_bytes(b'not-a-zip PRIVATE-CONTENT')
        result=subprocess.run([sys.executable,str(Path(__file__).parents[1]/'backup_cli.py'),'inspect',
            '--file',str(bad),'--scratch',str(self.root/'scratch')],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertEqual(result.stderr,'')
        self.assertNotIn('PRIVATE-CONTENT',result.stdout)

    def test_local_inputs_preserve_archives_but_revoke_directory_selection(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()]);self.collect('codex',root,automatic=True)
        source=self.store.db.execute('SELECT id FROM local_sources').fetchone()[0]
        input_id=self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        relative='sources/codex/'+source+'/inputs/'+input_id+'.md'
        body=(self.store.output/relative).read_bytes()
        archive,report=self.backup();target=self.root/'restored'
        restore_backup(archive,target,report['archiveSHA256'],self.root)
        self.store=Store(target)
        self.assertEqual((self.store.output/relative).read_bytes(),body)
        self.assertEqual(self.store.db.execute('SELECT automatic FROM local_sources').fetchone()[0],0)
        self.assertIn('Synthetic personal plan.',self.store.memories.source('input',input_id)[1])
        self.store.close();(target/'Library'/relative).unlink()
        with self.assertRaises(BackupError):create_backup(target,self.root/'missing-input.zip')
        self.assertFalse((self.root/'missing-input.zip').exists())

    def test_saved_answer_and_citations_survive_without_provider_credentials(self):
        self.store.knowledge.save_note({'title':'Review plan','text':'We review the orchard on Friday.'})
        chat=self.store.knowledge.create_conversation()
        turn=self.store.knowledge.ask(chat['id'],{'query':'orchard','requestID':str(uuid.uuid4()),'scopes':['notes']})
        self.store.answers.configure({'endpoint':'https://answers.invalid/v1','model':'synthetic-answer','key':'backup-answer-test-key'})
        refs=['note/'+turn['response']['sources'][0]['id']]
        plan=self.store.answers.preview(chat['id'],turn['id'],{'sources':refs})
        with patch.object(self.store.analysis,'request_json',side_effect=lambda *args:answer_fixtures.AnswerTests.reply(self,*args)) as call:
            self.store.answers.start(chat['id'],turn['id'],{'sources':refs,'snapshot':plan['snapshot'],'sendToModel':True},background=False)
        self.assertEqual(call.call_count,1)
        saved=self.store.knowledge.turns(chat['id'])['items'][0]['response']['generatedAnswer']
        archive,report=self.backup();target=self.root/'restored'
        restore_backup(archive,target,report['archiveSHA256'],self.root)
        self.store=Store(target)
        with patch.object(self.store.analysis,'request_json') as call:
            self.assertEqual(self.store.knowledge.turns(chat['id'])['items'][0]['response']['generatedAnswer'],saved)
            call.assert_not_called()
        self.assertIsNone(self.store.answers.configuration)
        with zipfile.ZipFile(archive) as zipped:
            self.assertNotIn(b'backup-answer-test-key',b''.join(zipped.read(n) for n in zipped.namelist()))

    def test_shutdown_drains_active_write_and_refuses_new_keepalive_request(self):
        server=Server(('127.0.0.1',0),self.store,'synthetic-backup-token')
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        entered=threading.Event();release=threading.Event();closed=threading.Event();responses=[]
        headers={'Authorization':'Bearer synthetic-backup-token','Content-Type':'application/json'}
        idle=http.client.HTTPConnection(*server.server_address,timeout=3)
        idle.request('GET','/health');r=idle.getresponse();r.read()
        original=self.store.knowledge.save_note
        def delayed(values):
            entered.set();self.assertTrue(release.wait(5));return original(values)
        def write():
            connection=http.client.HTTPConnection(*server.server_address,timeout=5)
            connection.request('POST','/v1/notes',json.dumps({'title':'Drain test','text':'Keep the complete write.'}),headers)
            response=connection.getresponse();responses.append((response.status,json.loads(response.read())));connection.close()
        def close():server.server_close();closed.set()
        with patch.object(self.store.knowledge,'save_note',side_effect=delayed):
            writer=threading.Thread(target=write);writer.start();self.assertTrue(entered.wait(3))
            server.shutdown();serving.join()
            closer=threading.Thread(target=close);closer.start()
            try:
                self.assertFalse(closed.wait(.1))
                idle.request('POST','/v1/notes',json.dumps({'title':'Refused','text':'Do not write.'}),headers)
                reply=idle.getresponse();self.assertEqual(reply.status,503);reply.read()
            finally:release.set();writer.join();closer.join();idle.close()
        self.assertTrue(closed.is_set())
        self.assertEqual(responses[0][0],201)
        self.assertEqual(self.store.knowledge.note(responses[0][1]['id'])['text'],'Keep the complete write.')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0],1)

import copy
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
import uuid
import test_node as fixtures
from server import Server
from store import Store, StoreError, markers_checksum
import recording_repairs as repairs
from backup import BackupError, create_backup, inspect_backup, restore_backup


class RecordingRepairTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown
    fixture=fixtures.NodeTests.fixture

    def broken(self):
        rid,manifest,source=self.fixture();self.store.accept(manifest,source)
        audio=self.store.audio_path(rid);audio.write_bytes(b'keep damaged audio')
        metadata=audio.parent/'manifest.json';metadata.write_text('{"manual":"keep this received copy"}')
        self.store.save_transcript(rid,'Manual transcript remains unchanged.',self.store.read_document('transcripts/'+rid+'.md')['etag'])
        value=self.store.memories.add('Synthetic memory remains.',sources=[rid]);self.store.memories.update(value['id'],{'state':'confirmed','memoryRevision':1})
        old={p.name:p.read_bytes() for p in audio.parent.iterdir()}
        source_info={'audioSHA256':manifest['audioSHA256'],'byteCount':manifest['recording']['byteCount'],'markersSHA256':markers_checksum(manifest['recording'])}
        return rid,manifest,source,source_info,old

    def approve(self,rid,source_info):
        preview=self.store.recording_repairs.preview(rid,source_info);key=str(uuid.uuid4()).upper()
        body={**source_info,'snapshot':preview['snapshot']}
        self.store.recording_repairs.prepare(rid,key,body)
        return key,body

    def test_repair_retains_received_files_and_does_not_change_text_memory_or_registration(self):
        rid,manifest,source,info,old=self.broken()
        before={str(f.relative_to(self.store.output)):f.read_bytes() for f in self.store.output.rglob('*') if f.is_file()}
        tables={n:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+n)] for n in ['recordings','candidates','memory_details','memory_versions']}
        preview=self.store.recording_repairs.preview(rid,info)
        self.assertEqual((preview['audioState'],preview['manifestState'],preview['keptFiles']),('changed','changed',2))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM recording_repairs').fetchone()[0],0)
        key,body=self.approve(rid,info);receipt=self.store.recording_repairs.accept(rid,key,source)
        self.assertEqual(receipt,self.store.receipt(rid,verify_files=True))
        archive=self.store.data/'recording-repairs'/key/'original'
        self.assertEqual({f.name:f.read_bytes() for f in archive.iterdir()},old)
        self.assertEqual(self.store.audio_path(rid).read_bytes(),source.read_bytes())
        self.assertEqual(self.store.recording_repairs.prepare(rid,key,body)['state'],'done')
        self.assertEqual(self.store.recording_repairs.accept(rid,key,source),receipt)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM recording_repairs').fetchone()[0],1)
        self.assertEqual(before,{str(f.relative_to(self.store.output)):f.read_bytes() for f in self.store.output.rglob('*') if f.is_file()})
        self.assertEqual(tables,{n:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+n)] for n in tables})

    def test_missing_package_can_be_restored_without_inventing_an_old_copy(self):
        rid,manifest,source,info,_=self.broken();shutil.rmtree(self.store.recordings/rid)
        key,_=self.approve(rid,info);self.store.recording_repairs.accept(rid,key,source)
        result=self.store.recording_repairs.status(rid,key)
        self.assertEqual((result['state'],result['keptFiles'],result['archivePath']),('done',0,None))
        self.assertEqual(self.store.manifest(rid),manifest)

    def test_stale_preview_or_changed_after_approval_never_replaces_received_files(self):
        rid,_,source,info,_=self.broken();preview=self.store.recording_repairs.preview(rid,info)
        audio=self.store.audio_path(rid);audio.write_bytes(b'concurrent edit')
        with self.assertRaises(StoreError):self.store.recording_repairs.prepare(rid,str(uuid.uuid4()),{**info,'snapshot':preview['snapshot']})
        self.assertEqual(audio.read_bytes(),b'concurrent edit')
        key,_=self.approve(rid,info);audio.write_bytes(b'a later edit')
        with self.assertRaises(StoreError):self.store.recording_repairs.accept(rid,key,source)
        self.assertEqual(audio.read_bytes(),b'a later edit')
        self.assertEqual(self.store.recording_repairs.status(rid,key)['state'],'blocked')

    def test_wrong_source_or_bad_upload_does_not_modify_the_received_package(self):
        rid,_,source,info,old=self.broken()
        for change in [{'audioSHA256':'0'*64},{'markersSHA256':'0'*64},{'byteCount':1}]:
            with self.assertRaises(StoreError):self.store.recording_repairs.preview(rid,{**info,**change})
        key,_=self.approve(rid,info);source.write_bytes(b'not the registered original')
        with self.assertRaises(StoreError):self.store.recording_repairs.accept(rid,key,source)
        self.assertEqual({f.name:f.read_bytes() for f in (self.store.recordings/rid).iterdir()},old)

    def test_preview_rejects_links_extra_files_and_pipes_without_reading_them(self):
        rid,_,_,info,_=self.broken();folder=self.store.recordings/rid;extra=folder/'unrecognized.txt';extra.write_text('preserve')
        with self.assertRaises(StoreError):self.store.recording_repairs.preview(rid,info)
        extra.unlink();audio=folder/(rid+'.caf');audio.unlink();outside=self.root/'outside';outside.write_text('never read or change');audio.symlink_to(outside)
        with self.assertRaises(StoreError):self.store.recording_repairs.preview(rid,info)
        audio.unlink();os.mkfifo(audio)
        with self.assertRaises(StoreError):self.store.recording_repairs.preview(rid,info)
        self.assertEqual(outside.read_text(),'never read or change')

    def test_crash_after_each_directory_move_recovers_without_a_second_archive(self):
        for destination in ['original','publish']:
            with self.subTest(destination=destination):
                rid,_,source,info,old=self.broken();key,_=self.approve(rid,info);real=repairs.rename_exclusive
                def fail_after_move(a,b,c,d):
                    real(a,b,c,d)
                    if d==('original' if destination=='original' else rid):raise OSError(errno.EIO,'synthetic interruption')
                with patch.object(repairs,'rename_exclusive',side_effect=fail_after_move):
                    with self.assertRaises(StoreError):self.store.recording_repairs.accept(rid,key,source)
                self.store.close();self.store=Store(self.root/'node')
                self.assertEqual(self.store.recording_repairs.status(rid,key)['state'],'done')
                self.assertEqual({f.name:f.read_bytes() for f in (self.store.data/'recording-repairs'/key/'original').iterdir()},old)
                self.assertEqual(self.store.audio_path(rid).read_bytes(),source.read_bytes())

    def test_publish_never_overwrites_a_destination_created_after_archival(self):
        rid,_,source,info,old=self.broken();key,_=self.approve(rid,info);real=repairs.rename_exclusive
        def competing(a,b,c,d):
            real(a,b,c,d)
            if d=='original':
                target=self.store.recordings/rid;target.mkdir();(target/'manual.txt').write_text('keep concurrent directory')
        with patch.object(repairs,'rename_exclusive',side_effect=competing):
            with self.assertRaises(StoreError):self.store.recording_repairs.accept(rid,key,source)
        self.assertEqual((self.store.recordings/rid/'manual.txt').read_text(),'keep concurrent directory')
        self.assertEqual({f.name:f.read_bytes() for f in (self.store.data/'recording-repairs'/key/'original').iterdir()},old)

    def test_individual_preview_states_are_accurate_when_only_audio_is_damaged(self):
        rid,manifest,source=self.fixture();self.store.accept(manifest,source);self.store.audio_path(rid).write_bytes(b'bad')
        preview=self.store.recording_repairs.preview(rid,{'audioSHA256':manifest['audioSHA256'],'byteCount':manifest['recording']['byteCount'],'markersSHA256':None})
        self.assertEqual((preview['audioState'],preview['manifestState']),('changed','matching'))

    def test_edit_arriving_during_archival_is_preserved_and_put_back(self):
        rid,_,source,info,_=self.broken();key,_=self.approve(rid,info);real=repairs.rename_exclusive
        def editing(a,b,c,d):
            real(a,b,c,d)
            if d=='original':
                (self.store.data/'recording-repairs'/key/'original'/'manual.txt').write_text('preserve concurrent edit')
        with patch.object(repairs,'rename_exclusive',side_effect=editing):
            with self.assertRaises(StoreError):self.store.recording_repairs.accept(rid,key,source)
        self.assertEqual((self.store.recordings/rid/'manual.txt').read_text(),'preserve concurrent edit')
        self.assertEqual(self.store.recording_repairs.status(rid,key)['state'],'blocked')

    def test_backup_restores_repaired_audio_history_and_retained_damaged_files(self):
        rid,_,source,info,old=self.broken();key,_=self.approve(rid,info)
        self.store.recording_repairs.accept(rid,key,source);self.store.close()
        archive=self.root/'repaired.zip';summary=create_backup(self.root/'node',archive)
        self.assertEqual((summary['recordingRepairs'],summary['retainedRecordingFiles']),(1,2))
        destination=self.root/'restored';restore_backup(archive,destination,summary['archiveSHA256'],self.root)
        restored=Store(destination)
        try:
            self.assertEqual(restored.recording_repairs.status(rid,key)['state'],'done')
            self.assertEqual({f.name:f.read_bytes() for f in (destination/'recording-repairs'/key/'original').iterdir()},old)
            self.assertEqual(restored.audio_path(rid).read_bytes(),source.read_bytes())
            self.assertNotEqual(restored.setting('nodeID'),restored.recording_repairs.status(rid,key)['nodeID'])
        finally:restored.close()
        self.store=Store(self.root/'node')

    def test_backup_cannot_silently_omit_a_retained_damaged_original(self):
        rid,_,source,info,_=self.broken();key,_=self.approve(rid,info)
        self.store.recording_repairs.accept(rid,key,source);self.store.close()
        original=self.root/'node/recording-repairs'/key/'original'/(rid+'.caf')
        original.rename(self.root/'kept-damaged.caf')
        with self.assertRaises(BackupError):create_backup(self.root/'node',self.root/'incomplete.zip')
        self.store=Store(self.root/'node')

    def test_explicit_library_selection_and_restore_block_old_unfinished_repair(self):
        from library_location import inspect_library, prepare_library
        rid,manifest,source,info,_=self.broken();key,_=self.approve(rid,info)
        # The files were restored independently before selecting this library.
        self.store.audio_path(rid).write_bytes(source.read_bytes())
        (self.store.recordings/rid/'manifest.json').write_text(json.dumps(manifest))
        self.store.close();archive=self.root/'pending.zip';summary=create_backup(self.root/'node',archive)
        destination=self.root/'pending-restored';restore_backup(archive,destination,summary['archiveSHA256'],self.root)
        restored=Store(destination)
        try:self.assertEqual(restored.recording_repairs.status(rid,key)['state'],'blocked')
        finally:restored.close()
        preview=inspect_library(self.root/'node');prepare_library(self.root/'node',preview['snapshot'])
        self.store=Store(self.root/'node')
        self.assertEqual(self.store.recording_repairs.status(rid,key)['state'],'blocked')
        self.assertEqual(self.store.audio_path(rid).read_bytes(),source.read_bytes())

    def test_repair_rejects_redirected_staging_without_touching_outside_files(self):
        rid,_,source,info,old=self.broken();key,_=self.approve(rid,info)
        staging=self.store.staging;staging.rename(staging.with_name('kept-staging'))
        outside=self.root/'outside-staging';outside.mkdir();(outside/'keep.txt').write_text('unchanged')
        staging.symlink_to(outside,target_is_directory=True)
        with self.assertRaises(OSError):
            with self.store.recording_repairs.incoming():self.fail('must not open redirected staging')
        with self.assertRaises(OSError):self.store.recording_repairs.accept(rid,key,source)
        self.assertEqual([f.name for f in outside.iterdir()],['keep.txt'])
        self.assertEqual({f.name:f.read_bytes() for f in (self.store.recordings/rid).iterdir()},old)

    def test_pending_repair_rejects_replaced_recordings_parent(self):
        rid,_,source,info,old=self.broken();key,_=self.approve(rid,info)
        records=self.store.recordings;records.rename(records.with_name('kept-recordings'))
        records.mkdir();shutil.copytree(records.with_name('kept-recordings')/rid,records/rid)
        with self.assertRaises(StoreError):self.store.recording_repairs.accept(rid,key,source)
        self.assertEqual({f.name:f.read_bytes() for f in (records/rid).iterdir()},old)
        self.assertEqual({f.name:f.read_bytes() for f in (records.with_name('kept-recordings')/rid).iterdir()},old)

    def test_repair_http_requires_device_authorization_and_current_library_identity(self):
        rid,_,source,info,_=self.broken();node=self.store.setting('nodeID')
        grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-repair-token');worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def call(method,path,body,token=server.token,receiver=node):
            c=http.client.HTTPConnection(*server.server_address,timeout=10);headers={'Authorization':'Bearer '+token}
            if receiver is not None:headers['X-Recording-Node-ID']=receiver
            c.request(method,path,body,headers=headers);r=c.getresponse();value=json.loads(r.read());c.close();return r.status,value
        try:
            path='/v1/recordings/'+rid
            self.assertEqual(call('POST',path+'/repair-preview',json.dumps(info),grant['token'])[0],401)
            self.assertEqual(call('POST',path+'/repair-preview',json.dumps(info),receiver=None)[0],409)
            self.assertEqual(call('POST',path+'/repair-preview',json.dumps(info),receiver='different-library')[0],409)
            code,preview=call('POST',path+'/repair-preview',json.dumps(info));self.assertEqual(code,200)
            key=str(uuid.uuid4()).upper();route=path+'/repair/'+key
            self.assertEqual(call('POST',route,json.dumps({**info,'snapshot':preview['snapshot']}))[0],200)
            self.assertEqual(call('PUT',route+'/audio',source.read_bytes())[0],200)
            code,status=call('GET',route,b'');self.assertEqual((code,status['state']),(200,'done'))
        finally:server.shutdown();server.server_close();worker.join()

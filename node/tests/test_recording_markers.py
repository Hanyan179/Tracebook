import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import threading
import time
import unittest
import uuid
import test_node as fixtures
from store import Store, StoreError, markers_checksum, validate_manifest
from server import Server
from backup import create_backup, inspect_backup, restore_backup


class RecordingMarkerTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown
    fixture = fixtures.NodeTests.fixture

    def marked(self, count=2):
        rid, manifest, audio = self.fixture()
        manifest['schemaVersion'] = 2
        manifest['recording']['markers'] = [{'id':str(uuid.uuid4()).upper(),'offsetMilliseconds':i+1} for i in range(count)]
        return rid, manifest, audio

    def test_positions_receipts_restart_and_same_audio_conflicts(self):
        rid,m,audio=self.marked();receipt=self.store.accept(m,audio)
        self.assertEqual(receipt['markersSHA256'],markers_checksum(m['recording']))
        self.assertEqual(self.store.list_recordings()[0]['markers'],m['recording']['markers'])
        self.assertEqual(self.store.accept(m,audio),receipt)
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.receipt(rid),receipt)
        changed=copy.deepcopy(m);changed['recording']['markers'][0]['offsetMilliseconds']=0
        with self.assertRaises(StoreError):self.store.accept(changed,audio)
        self.assertEqual(self.store.manifest(rid),m)
        self.assertEqual(self.store.audio_path(rid).read_bytes(),audio.read_bytes())

    def test_marker_checksum_matches_swift_and_legacy_format_remains_valid(self):
        recording={'markers':[{'id':'00000000-0000-4000-8000-000000000001','offsetMilliseconds':1234},
                              {'id':'00000000-0000-4000-8000-000000000002','offsetMilliseconds':9876}]}
        self.assertEqual(markers_checksum(recording),'412ef8122d515d8211e2ac94a23e576f6e9dab8e77f32a6880b17336e03af332')
        rid,m,audio=self.fixture();self.assertIsNone(self.store.accept(m,audio)['markersSHA256'])
        m['recording']['markers']=recording['markers']
        with self.assertRaises(StoreError):validate_manifest(m)

    def test_invalid_or_unbounded_markers_are_rejected_before_receipt(self):
        _,m,audio=self.marked();markers=m['recording']['markers']
        bad=[None,[],{},False,'wrong',[{}],markers*501,[markers[0],markers[0]],list(reversed(markers))]
        bad += [[{**markers[0],'offsetMilliseconds':v}] for v in [-1,True,1.5,float('nan'),31536000001]]
        bad += [[{**markers[0],'unexpected':'field'}]]
        for value in bad:
            with self.subTest(value=type(value)):
                changed=copy.deepcopy(m);changed['recording']['markers']=value
                with self.assertRaises(StoreError):self.store.accept(changed,audio)
        self.assertEqual(self.store.list_recordings(),[])

    def test_marker_beyond_recovered_audio_is_retained_without_fabricating_a_position(self):
        rid,m,audio=self.marked();m['recording']['status']='recovered';m['recording']['markers'][-1]['offsetMilliseconds']=9000
        self.store.accept(m,audio)
        self.assertEqual(self.store.manifest(rid)['recording']['markers'][-1]['offsetMilliseconds'],9000)
        self.assertEqual(self.store.list_recordings()[0]['duration'],2)

    def test_prepared_upload_is_bounded_expires_and_rejects_wrong_identity_or_content(self):
        rid,m,_=self.marked(1000);prepared=self.store.prepare_recording_upload(rid,m)
        self.assertEqual(self.store.prepare_recording_upload(rid,m),prepared)
        self.assertEqual(self.store.prepared_recording_upload(rid,prepared['manifestID']),m)
        directory=self.store.staging/'recording-manifests';path=directory/(prepared['manifestID']+'.json')
        with self.assertRaises(StoreError):self.store.prepared_recording_upload(str(uuid.uuid4()),prepared['manifestID'])
        path.write_text('{}')
        with self.assertRaises(StoreError):self.store.prepared_recording_upload(rid,prepared['manifestID'])
        self.store.prepare_recording_upload(rid,m)
        old=time.time()-86401;os.utime(path,(old,old))
        with self.assertRaises(StoreError):self.store.prepared_recording_upload(rid,prepared['manifestID'])
        for index in range(64):(directory/(str(index).zfill(64)+'.json')).write_text('{}')
        with self.assertRaises(StoreError) as full:self.store.prepare_recording_upload(rid,m)
        self.assertEqual(full.exception.status,503)
        self.assertFalse(path.exists())

    def test_prepared_http_upload_handles_max_markers_and_excludes_read_only_tokens(self):
        rid,m,audio=self.marked(1000)
        grant=self.store.knowledge.create_grant({'name':'Synthetic marker reader','scopes':['confirmedMemory'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-marker-token');worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def call(method,route,body,token,extra=None):
            connection=http.client.HTTPConnection(*server.server_address,timeout=10)
            connection.request(method,route,body,headers={'Authorization':'Bearer '+token,**(extra or {})})
            response=connection.getresponse();value=json.loads(response.read());connection.close();return response.status,value
        try:
            route='/v1/recordings/'+rid+'/prepare'
            self.assertEqual(call('POST',route,json.dumps(m),grant['token'])[0],401)
            status,prepared=call('POST',route,json.dumps(m),server.token);self.assertEqual(status,200)
            self.assertEqual(self.store.list_recordings(),[])
            headers={'X-Recording-Upload':prepared['manifestID']}
            status,receipt=call('PUT','/v1/recordings/'+rid,audio.read_bytes(),server.token,headers)
            self.assertEqual(status,200,receipt);self.assertEqual(receipt['markersSHA256'],markers_checksum(m['recording']))
            self.assertEqual(len(self.store.list_recordings()[0]['markers']),1000)
            self.assertFalse((self.store.staging/'recording-manifests'/(prepared['manifestID']+'.json')).exists())
            _,prepared=call('POST',route,json.dumps(m),server.token)
            status,retry=call('PUT','/v1/recordings/'+rid,audio.read_bytes(),server.token,{'X-Recording-Upload':prepared['manifestID']})
            self.assertEqual((status,retry),(200,receipt))
        finally:server.shutdown();server.server_close();worker.join()

    def test_backup_preserves_markers_and_does_not_restore_temporary_uploads(self):
        rid,m,audio=self.marked();receipt=self.store.accept(m,audio)
        self.store.prepare_recording_upload(rid,m);self.store.close()
        archive=self.root/'markers.zip';report=create_backup(self.root/'node',archive)
        inspect_backup(archive,self.root);target=self.root/'restored';restore_backup(archive,target,report['archiveSHA256'],self.root)
        restored=Store(target)
        try:
            self.assertEqual(restored.manifest(rid),m)
            self.assertEqual(restored.receipt(rid)['markersSHA256'],receipt['markersSHA256'])
            self.assertFalse((restored.staging/'recording-manifests').exists())
            self.assertEqual(restored.audio_path(rid).read_bytes(),audio.read_bytes())
        finally:restored.close()
        self.store=Store(self.root/'node')

    def test_unindexed_recording_never_acknowledges_a_different_marker_list(self):
        rid,m,audio=self.marked();self.store.accept(m,audio)
        with self.store.db:self.store.db.execute('delete from recordings where id=?',(rid,))
        changed=copy.deepcopy(m);changed['recording']['markers'][0]['offsetMilliseconds']=0
        with self.assertRaises(StoreError):self.store.accept(changed,audio)
        self.assertEqual(json.loads((self.store.recordings/rid/'manifest.json').read_text()),m)
        self.store.accept(m,audio)
        self.assertEqual(self.store.receipt(rid)['markersSHA256'],markers_checksum(m['recording']))

    def test_prepared_upload_does_not_follow_links_or_read_a_pipe(self):
        rid,m,_=self.marked();prepared=self.store.prepare_recording_upload(rid,m)
        path=self.store.staging/'recording-manifests'/(prepared['manifestID']+'.json')
        original=path.read_bytes();held=self.root/'held.json';held.write_bytes(original)
        path.unlink();path.symlink_to(held)
        with self.assertRaises(StoreError):self.store.prepared_recording_upload(rid,prepared['manifestID'])
        path.unlink();os.mkfifo(path)
        with self.assertRaises(StoreError):self.store.prepared_recording_upload(rid,prepared['manifestID'])
        self.assertEqual(held.read_bytes(),original)

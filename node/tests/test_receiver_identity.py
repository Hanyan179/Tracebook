import copy
import http.client
import json
import os
import threading
import unittest
import uuid
from pathlib import Path
import test_node as fixtures
from server import Server
from store import StoreError


class ReceiverIdentityTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown
    fixture = fixtures.NodeTests.fixture

    def test_verified_receipt_requires_original_audio_and_manifest(self):
        rid,manifest,audio=self.fixture();receipt=self.store.accept(manifest,audio)
        original=self.store.audio_path(rid);raw=original.read_bytes()
        self.assertEqual(self.store.receipt(rid,verify_files=True),receipt)
        for changed in [raw[:-1],bytes([raw[0]^1])+raw[1:]]:
            original.write_bytes(changed)
            with self.assertRaises(StoreError) as error:self.store.receipt(rid,verify_files=True)
            self.assertEqual(error.exception.status,409)
        original.unlink()
        with self.assertRaises(StoreError):self.store.receipt(rid,verify_files=True)
        original.write_bytes(raw)
        metadata=original.parent/'manifest.json';before=metadata.read_bytes()
        changed=copy.deepcopy(manifest);changed['recording']['title']='Changed on receiver'
        metadata.write_text(json.dumps(changed))
        with self.assertRaises(StoreError):self.store.receipt(rid,verify_files=True)
        metadata.write_bytes(before)
        self.assertEqual(self.store.receipt(rid,verify_files=True),receipt)
        self.assertEqual(self.store.manifest(rid),manifest)

    def test_verified_receipt_rejects_links_pipes_and_replaced_directory(self):
        rid,manifest,audio=self.fixture();self.store.accept(manifest,audio)
        original=self.store.audio_path(rid);saved=original.read_bytes();held=self.root/'held.caf';held.write_bytes(saved)
        original.unlink();original.symlink_to(held)
        with self.assertRaises(StoreError):self.store.receipt(rid,verify_files=True)
        original.unlink();os.mkfifo(original)
        with self.assertRaises(StoreError):self.store.receipt(rid,verify_files=True)
        original.unlink();original.write_bytes(saved)
        directory=original.parent;renamed=self.root/'held-package';directory.rename(renamed);directory.symlink_to(renamed)
        with self.assertRaises(StoreError):self.store.receipt(rid,verify_files=True)
        self.assertEqual(held.read_bytes(),saved)

    def test_identity_header_guards_prepare_upload_and_receipt_before_writing(self):
        rid,manifest,audio=self.fixture();node=self.store.setting('nodeID')
        server=Server(('127.0.0.1',0),self.store,'synthetic-receiver-token')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def call(method,path,body=b'',receiver=None,extra=None):
            c=http.client.HTTPConnection(*server.server_address,timeout=10)
            headers={'Authorization':'Bearer '+server.token}
            headers.update(extra or {})
            if receiver is not None:headers['X-Recording-Node-ID']=receiver
            c.request(method,path,body,headers=headers);r=c.getresponse();value=json.loads(r.read());c.close();return r.status,value
        try:
            self.assertTrue(call('GET','/v1/status')[1]['recordingIdentitySupported'])
            path='/v1/recordings/'+rid
            self.assertEqual(call('POST',path+'/prepare',json.dumps(manifest),'old-library')[0],409)
            self.assertEqual(call('PUT',path,audio.read_bytes(),'old-library')[0],409)
            self.assertEqual(self.store.list_recordings(),[])
            self.assertFalse((self.store.staging/'recording-manifests').exists())
            self.assertEqual(call('GET',path+'/receipt',receiver='old-library')[0],409)
            self.assertEqual(call('GET',path+'/receipt',receiver=node)[0],404)
            receipt=self.store.accept(manifest,audio)
            self.assertEqual(call('GET',path+'/receipt',receiver=node),(200,receipt))
            self.store.audio_path(rid).unlink()
            self.assertEqual(call('GET',path+'/receipt',receiver=node)[0],409)
            status,prepared=call('POST',path+'/prepare',json.dumps(manifest),node)
            self.assertEqual(status,200)
            self.assertEqual(call('PUT',path,audio.read_bytes(),node,{'X-Recording-Upload':prepared['manifestID']})[0],409)
            # Existing clients retain their response contract; identity-aware clients
            # require the capability and verified receipt path before showing success.
            self.assertEqual(call('GET',path+'/receipt')[0],200)
        finally:server.shutdown();server.server_close();worker.join()

    def test_library_change_between_preparation_and_audio_upload_rejects_stale_request(self):
        rid,manifest,audio=self.fixture();original=self.store.setting('nodeID')
        server=Server(('127.0.0.1',0),self.store,'synthetic-receiver-token')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def call(method,suffix,body,extra=None):
            c=http.client.HTTPConnection(*server.server_address,timeout=10)
            c.request(method,'/v1/recordings/'+rid+suffix,body,headers={
                'Authorization':'Bearer '+server.token,'X-Recording-Node-ID':original,**(extra or {})})
            r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
        try:
            status,prepared=call('POST','/prepare',json.dumps(manifest));self.assertEqual(status,200)
            with self.store.db:self.store.db.execute("UPDATE settings SET value=? WHERE key='nodeID'",(str(uuid.uuid4()),))
            status,_=call('PUT','',audio.read_bytes(),{'X-Recording-Upload':prepared['manifestID']})
            self.assertEqual(status,409)
            self.assertEqual(self.store.list_recordings(),[])
            self.assertFalse((self.store.recordings/rid).exists())
        finally:server.shutdown();server.server_close();worker.join()

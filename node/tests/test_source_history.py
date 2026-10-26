import hashlib
import http.client
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import urllib.parse
import test_node as fixtures
import test_local_sources as local_fixtures
from backup import create_backup, inspect_backup, restore_backup
from store import Store, StoreError
from server import Server
from source_history import BATCH, MAX_BYTES


def sha(text): return hashlib.sha256(text.encode()).hexdigest()


class SourceHistoryTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown
    receive = fixtures.NodeTests.receive
    fixture = fixtures.NodeTests.fixture
    text_etag = fixtures.NodeTests.text_etag
    folder = local_fixtures.LocalSourceTests.folder
    write = local_fixtures.LocalSourceTests.write
    meta = local_fixtures.LocalSourceTests.meta
    user = local_fixtures.LocalSourceTests.user
    collect = local_fixtures.LocalSourceTests.collect

    def note(self, body='before\nSame quote.\nMiddle 👩🏽‍💻 e\u0301\nSame quote.\nafter'):
        return self.store.knowledge.save_note({'title':'Synthetic source', 'text':body})

    def read(self, note, version=None, **parameters):
        return self.store.source_history.read('note', note['id'], version or sha(note['text']),
            {'nodeID':self.store.setting('nodeID'), **parameters})

    def changed(self):
        note = self.note()
        self.store.knowledge.save_note({'title':note['title'], 'text':'new text\nSame quote.', 'etag':note['etag']}, note['id'])
        return note

    def test_exact_old_version_scalar_offsets_full_read_and_restart(self):
        note = self.changed(); start = note['text'].rfind('Same quote.')
        value = self.read(note, offset=start-4, limit=15)
        self.assertEqual(value['state'], 'historical')
        self.assertEqual(value['source']['text'], note['text'][start-4:start+11])
        self.assertEqual(value['source']['contentVersion'], sha(note['text']))
        self.assertNotIn(str(self.root), json.dumps(value))
        self.assertEqual(self.read(note, locator=value['locator'])['source']['text'], note['text'])
        self.store.close(); self.store = Store(self.root/'node')
        self.assertEqual(self.read(note)['source']['text'], note['text'])
        self.assertEqual(self.store.knowledge.note(note['id'])['text'], 'new text\nSame quote.')

    def test_current_does_not_need_history_and_missing_never_searches_quote(self):
        note = self.note(); self.assertEqual(self.read(note)['state'], 'current')
        self.assertEqual(self.read(note, 'f'*64)['state'], 'missing')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM candidates').fetchone()[0], 0)

    def test_failed_directory_scan_releases_owned_descriptor(self):
        note = self.changed(); opened = []; duplicate = os.dup
        def track(fd):
            held = duplicate(fd); opened.append(held); return held
        with patch('source_history.os.dup', side_effect=track), patch('source_history.os.scandir', side_effect=OSError('synthetic scan failure')):
            with self.assertRaises(StoreError): self.read(note)
        self.assertTrue(opened)
        for fd in opened:
            with self.assertRaises(OSError): os.fstat(fd)
        self.assertFalse(self.store.source_history.scans)

    def test_missing_current_can_read_history_but_archived_source_is_denied(self):
        note = self.changed(); (self.store.output/note['path']).unlink()
        self.assertEqual(self.read(note)['state'], 'historical')
        with self.store.db: self.store.db.execute("UPDATE library_items SET archived=1 WHERE kind='note' AND id=?", (note['id'],))
        with self.assertRaises(StoreError) as caught: self.read(note)
        self.assertEqual(caught.exception.status, 404)

    def test_whole_digest_not_filename_prefix_and_modified_locator_is_rejected(self):
        note = self.changed(); value = self.read(note)
        history = self.store.output/'.history'/note['path']/value['locator']
        history.write_text('same name, different body')
        result = self.read(note)
        self.assertEqual(result['state'], 'incomplete')
        self.assertEqual(result['skipped'], 1)
        with self.assertRaises(StoreError): self.read(note, locator=value['locator'])
        for locator in ['../outside.md', '/etc/passwd', 'revision-1']:
            with self.assertRaises(StoreError): self.read(note, locator=locator)

    def test_symlinks_and_nonregular_oversize_invalid_text_are_not_followed(self):
        note = self.changed(); value = self.read(note)
        history = self.store.output/'.history'/note['path']/value['locator']
        history.unlink(); outside = self.root/'outside'; outside.write_text(note['text']); history.symlink_to(outside)
        self.assertEqual(self.read(note)['state'], 'incomplete')
        with self.assertRaises(StoreError): self.read(note, locator=value['locator'])
        history.unlink(); os.mkfifo(history)
        self.assertEqual(self.read(note)['state'], 'incomplete')
        history.unlink(); history.write_bytes(b'\xff')
        self.assertEqual(self.read(note)['state'], 'incomplete')
        with history.open('wb') as f: f.truncate(MAX_BYTES+1)
        self.assertEqual(self.read(note)['state'], 'incomplete')
        self.assertEqual(outside.read_text(), note['text'])

    def test_symlink_directory_and_current_file_are_rejected(self):
        note = self.changed(); folder = self.store.output/'.history'/note['path']
        moved = folder.with_name('moved'); folder.rename(moved); folder.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(StoreError): self.read(note)
        folder.unlink(); moved.rename(folder)
        current = self.store.output/note['path']; moved = current.with_suffix('.saved'); current.rename(moved); current.symlink_to(moved)
        with self.assertRaises(StoreError): self.read(note)

    def test_bounded_continuation_expiry_and_wrong_source(self):
        note = self.changed(); folder = self.store.output/'.history'/note['path']
        # Unknown records count as unverified, rather than falsely complete.
        for i in range(BATCH+4): (folder/('unknown-'+str(i))).write_text('synthetic')
        result = self.read(note, 'f'*64)
        self.assertEqual(result['state'], 'searching'); self.assertEqual(result['checked'], BATCH)
        other = self.changed()
        with self.assertRaises(StoreError): self.read(other, 'f'*64, cursor=result['nextCursor'])
        final = self.read(note, 'f'*64, cursor=result['nextCursor'])
        self.assertEqual(final['state'], 'incomplete'); self.assertEqual(final['skipped'], BATCH+4)
        self.assertFalse(self.store.source_history.scans)
        result = self.read(note, 'f'*64)
        with patch('source_history.time.monotonic', return_value=10**12):
            with self.assertRaises(StoreError): self.read(note, 'f'*64, cursor=result['nextCursor'])
        self.assertFalse(self.store.source_history.scans)

    def test_mutated_directory_invalidates_continuation_and_source_binding(self):
        note = self.changed(); folder = self.store.output/'.history'/note['path']
        for i in range(BATCH+2): (folder/('unknown-'+str(i))).write_text('synthetic')
        result = self.read(note, 'f'*64); (folder/'another').write_text('new')
        with self.assertRaises(StoreError): self.read(note, 'f'*64, cursor=result['nextCursor'])
        self.assertFalse(self.store.source_history.scans)
        for parameters in [{'nodeID':'different-library'}, {'offset':-1}, {'limit':16001}]:
            with self.assertRaises(StoreError): self.read(note, **parameters)
        for version in ['../escape', 'a'*10, 'z'*64]:
            with self.assertRaises(StoreError): self.read(note, version)

    def test_parent_replaced_during_read_is_rejected(self):
        note = self.changed(); value = self.read(note); folder = self.store.output/'.history'/note['path']
        original = self.store.source_history.read_file
        def read(fd, name):
            body = original(fd, name)
            if name == value['locator']:
                folder.rename(folder.with_name('held')); folder.mkdir()
            return body
        with patch.object(self.store.source_history, 'read_file', side_effect=read):
            with self.assertRaises(StoreError): self.read(note, locator=value['locator'])

    def test_file_changes_during_read_are_rejected(self):
        note = self.changed(); value = self.read(note); folder = self.store.output/'.history'/note['path']
        real_stat = os.stat
        def changed(path, *args, **kwargs):
            if path == value['locator']: (folder/path).write_text('changed during read')
            return real_stat(path, *args, **kwargs)
        with patch('source_history.os.stat', side_effect=changed):
            with self.assertRaises(StoreError): self.read(note, locator=value['locator'])

    def test_device_route_does_not_expand_external_grants_or_current_read_contract(self):
        note = self.changed(); grant = self.store.knowledge.create_grant({'name':'synthetic', 'scopes':['notes'], 'days':1})
        server = Server(('127.0.0.1',0), self.store, 'device-test-token'); worker = threading.Thread(target=server.serve_forever); worker.start()
        path = '/v1/sources/note/'+note['id']+'/versions/'+sha(note['text'])+'?'+urllib.parse.urlencode({'nodeID':self.store.setting('nodeID')})
        def call(path, token):
            conn = http.client.HTTPConnection(*server.server_address)
            conn.request('GET',path,headers={'Authorization':'Bearer '+token}); response=conn.getresponse()
            code, body = response.status, json.loads(response.read()); conn.close(); return code,body
        try:
            self.assertEqual(call(path,'device-test-token')[1]['state'], 'historical')
            self.assertEqual(call(path,grant['token'])[0],401)
            external='/v1/assistant/items/note/'+note['id']
            self.assertEqual(call(external+'/versions/'+sha(note['text']),grant['token'])[0],403)
            self.assertEqual(call(external+'?version='+sha(note['text']),grant['token'])[0],409)
            self.assertEqual(call(external,grant['token'])[1]['text'], 'new text\nSame quote.')
            self.assertEqual(call(path,'wrong')[0],401)
        finally: server.shutdown();server.server_close();worker.join()

    def test_confirmed_memory_versions_and_revoked_visibility(self):
        note = self.note(); source={'kind':'note','id':note['id'],'contentVersion':sha(note['text']),'quote':note['text']}
        memory=self.store.add_candidate('Synthetic old memory',evidence=[source])
        memory=self.store.memories.detail(memory['id'])
        memory=self.store.memories.update(memory['id'],{'memoryRevision':memory['memoryRevision'],'state':'confirmed'})
        version=sha(memory['text'])
        self.store.memories.update(memory['id'],{'memoryRevision':memory['memoryRevision'],'text':'Synthetic new memory'})
        params={'nodeID':self.store.setting('nodeID')}
        result=self.store.source_history.read('memory',memory['id'],version,params)
        self.assertEqual(result['state'],'historical');self.assertEqual(result['source']['text'],'Synthetic old memory')
        self.assertEqual(self.store.source_history.read('memory',memory['id'],version,{**params,'locator':result['locator']})['source']['text'],'Synthetic old memory')
        with self.store.db:self.store.db.execute("UPDATE library_items SET state='inactive' WHERE kind='memory' AND id=?",(memory['id'],))
        with self.assertRaises(StoreError):self.store.source_history.read('memory',memory['id'],version,params)

    def test_imported_inputs_documents_transcripts_and_daily_files(self):
        rid,_,_=self.receive();self.store.save_transcript(rid,'Synthetic spoken text',self.text_etag(rid))
        preview=self.store.documents.preview({'files':[{'name':'synthetic.txt','text':'Synthetic document text'}]})
        self.store.documents.commit(preview['id']);doc=self.store.db.execute('SELECT id FROM documents').fetchone()[0]
        root,path=self.folder();self.write(path,[self.meta(),self.user()]);self.collect('codex',root)
        item=self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        self.store.edit_document('analysis/2026-10-19.md','Synthetic analysis','');self.store.catalog.index_analysis('2026-10-19')
        rows=[('recording',rid,'transcripts/'+rid+'.md'),('document',doc,'documents/'+doc+'.md'),
              ('input',item,json.loads(self.store.db.execute("SELECT data FROM library_items WHERE kind='input' AND id=?",(item,)).fetchone()[0])['path']),
              ('daily','2026-10-19','daily/2026-10-19.md'),('analysis','2026-10-19','analysis/2026-10-19.md')]
        for kind,identifier,path in rows:
            with self.subTest(kind=kind):
                old=self.store.read_document(path);self.store.edit_document(path,'changed body',old['etag'])
                result=self.store.source_history.read(kind,identifier,sha(old['text']),{'nodeID':self.store.setting('nodeID')})
                self.assertEqual(result['state'],'historical');self.assertEqual(result['source']['text'],old['text'])

    def test_full_history_survives_backup_restore_without_exporting_readers(self):
        note=self.changed();archive=self.root/'archive.zip'
        self.store.close();create_backup(self.root/'node',archive);self.store=Store(self.root/'node')
        checked=inspect_backup(archive,self.root)
        target=self.root/'restored';restore_backup(archive,target,checked['archiveSHA256'],self.root)
        restored=Store(target)
        try:
            result=restored.source_history.read('note',note['id'],sha(note['text']),{'nodeID':restored.setting('nodeID')})
            self.assertEqual(result['source']['text'],note['text']);self.assertEqual(result['state'],'historical')
            self.assertNotEqual(restored.setting('nodeID'),self.store.setting('nodeID'))
        finally:restored.close()

    def test_history_lookup_checks_whole_file_again_on_each_page(self):
        note=self.note('old '+('a'*20000)+' end')
        self.store.knowledge.save_note({'title':note['title'],'text':'current','etag':note['etag']},note['id'])
        first=self.read(note,limit=16000);self.assertEqual(first['source']['nextOffset'],16000)
        second=self.read(note,offset=16000,locator=first['locator'])
        self.assertEqual(first['source']['text']+second['source']['text'],note['text'])
        (self.store.output/'.history'/note['path']/first['locator']).write_text('tampered')
        with self.assertRaises(StoreError):self.read(note,offset=16000,locator=first['locator'])

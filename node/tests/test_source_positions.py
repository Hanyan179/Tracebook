import hashlib
import http.client
import json
from pathlib import Path
import threading
import unittest
import urllib.parse
import zipfile
from unittest.mock import patch

import test_local_sources as fixtures
from backup import create_backup, inspect_backup, restore_backup
from compressed_io import pyzstd
from server import Server
from store import Store, StoreError

SYNTHETIC_SECRET = b'sk-' + b'syntheticsecret' + b'123456789'


class SourcePositionTests(unittest.TestCase):
    setUp = fixtures.LocalSourceTests.setUp
    tearDown = fixtures.LocalSourceTests.tearDown
    folder = fixtures.LocalSourceTests.folder
    write = fixtures.LocalSourceTests.write
    meta = fixtures.LocalSourceTests.meta
    user = fixtures.LocalSourceTests.user
    collect = fixtures.LocalSourceTests.collect

    def raw(self, value):
        return (json.dumps(value,ensure_ascii=False)+'\n').encode()

    def positions(self, rid, path, **query):
        return self.store.local_sources.file_issues(rid,{'path':'sessions/2026/10/20/'+path.name,**query})

    def status(self, rid, path):
        return next(x for x in self.store.local_sources.file_coverage(rid,{'attention':'false'})['items'] if x['path'].endswith('/'+path.name))

    def mixed(self):
        root,path=self.folder()
        lines=[self.raw(self.meta()),self.raw(self.user('中文 input preserved.','good')),
               b'{"private":"'+SYNTHETIC_SECRET+b'"\n',
               self.raw(self.user('Unknown date private prose.','date','bad-time')),
               self.raw(self.user('<send_user_message_question_reply>broken</send_user_message_question_reply>','reply')),
               self.raw({'type':'tool','data':'x'*3000}),self.raw(self.user('x'*700,'large-input'))]
        path.write_bytes(b''.join(lines))
        with patch('local_sources.LINE_BYTES',2000),patch('local_sources.MAX_INPUT',600):
            preview,report=self.collect('codex',root)
        return root,path,lines,preview['sourceID'],report

    def test_exact_lines_byte_offsets_and_reasons_without_raw_data(self):
        root,path,lines,rid,report=self.mixed()
        page=self.positions(rid,path)
        expected=[{'offset':sum(map(len,lines[:i])),'line':i+1,'reason':reason} for i,reason in
                  [(2,'invalidJSON'),(3,'invalidDate'),(4,'invalidReply'),(5,'recordTooLarge'),(6,'inputTooLarge')]]
        self.assertEqual(page['items'],expected);self.assertEqual(page['total'],5)
        self.assertEqual(page['unlocatedCount'],0);self.assertTrue(page['complete'])
        self.assertEqual(report['summary']['inputs'],1)
        self.assertEqual(report['summary']['skippedRecords'],5)
        for secret in ('syntheticsecret','private prose','中文 input'):
            self.assertNotIn(secret,json.dumps(page))
        self.assertEqual(path.read_bytes(),b''.join(lines))

    def test_issue_budget_resumes_all_positions_across_restarts(self):
        root,path=self.folder();path.write_bytes(self.raw(self.meta())+b'broken\n'*7+self.raw(self.user()))
        with patch('local_sources.SCAN_ISSUES',2):
            preview,report=self.collect('codex',root);rid=preview['sourceID']
            self.assertEqual(self.positions(rid,path)['total'],2);self.assertEqual(report['summary']['pendingFiles'],1)
            for _ in range(4):
                self.store.close();self.store=Store(self.root/'node')
                self.store.local_sources.refresh(rid)
                if self.positions(rid,path)['complete']:break
        page=self.positions(rid,path);self.assertTrue(page['complete']);self.assertEqual(page['total'],7)
        self.assertEqual([x['line'] for x in page['items']],list(range(2,9)))
        self.assertEqual(self.store.local_sources.listing()[0]['summary']['inputs'],1)
        before=page['items'];self.store.local_sources.refresh(rid)
        self.assertEqual(self.positions(rid,path)['items'],before)

    def test_oversized_line_and_unfinished_tail_keep_correct_next_line(self):
        root,path=self.folder();huge=self.raw({'type':'tool','data':'x'*5000})
        start=self.raw(self.meta());tail=self.raw(self.user('Completed later.','tail'))
        path.write_bytes(start+huge+b'broken\n'+tail[:50])
        with patch('local_sources.SCAN_BYTES',600),patch('local_sources.LINE_BYTES',512):
            preview,_=self.collect('codex',root);rid=preview['sourceID']
            for _ in range(20):
                if self.status(rid,path)['status']=='waiting':break
                self.store.close();self.store=Store(self.root/'node');self.store.local_sources.refresh(rid)
            page=self.positions(rid,path)
            self.assertEqual([(x['line'],x['reason']) for x in page['items']],[(2,'recordTooLarge'),(3,'invalidJSON')])
            self.assertEqual(page['items'][0]['offset'],len(start));self.assertFalse(page['complete'])
            with path.open('ab') as stream:stream.write(tail[50:]+b'bad-after-tail\n')
            self.store.local_sources.refresh(rid)
        page=self.positions(rid,path);self.assertEqual(page['items'][-1]['line'],5)
        self.assertEqual(page['items'][-1]['offset'],len(start+huge+b'broken\n'+tail))
        self.assertEqual(self.store.local_sources.listing()[0]['summary']['inputs'],1)

    def test_compressed_positions_use_decompressed_bytes_and_continue_without_duplicates(self):
        root,path=self.folder();raw=self.raw(self.meta())+self.raw(self.user('中文 bytes.'))+b'bad\n'*5
        path=path.with_name(path.name+'.zst');path.write_bytes(pyzstd.compress(raw));before=path.read_bytes()
        with patch('local_sources.SCAN_ISSUES',2):
            preview,_=self.collect('codex',root);rid=preview['sourceID']
            for _ in range(4):
                if self.positions(rid,path)['complete']:break
                self.store.close();self.store=Store(self.root/'node');self.store.local_sources.refresh(rid)
        page=self.positions(rid,path);self.assertTrue(page['compressed']);self.assertEqual(page['total'],5)
        first=len(self.raw(self.meta())+self.raw(self.user('中文 bytes.')))
        self.assertEqual([x['offset'] for x in page['items']],[first+4*i for i in range(5)])
        self.assertEqual([x['line'] for x in page['items']],[3,4,5,6,7]);self.assertEqual(path.read_bytes(),before)

    def test_cursor_requires_same_committed_scan_and_valid_inventory_path(self):
        root,path=self.folder();path.write_bytes(b'bad\n'*5);preview,_=self.collect('codex',root);rid=preview['sourceID']
        first=self.positions(rid,path,limit=2);self.assertEqual(len(first['items']),2)
        second=self.positions(rid,path,limit=2,after=first['nextAfter'],stateVersion=first['stateVersion'])
        self.assertEqual([x['line'] for x in second['items']],[3,4])
        for query in ({'after':first['nextAfter']},{'after':-2},{'after':2**64},{'limit':'wrong'}):
            with self.assertRaises(StoreError):self.positions(rid,path,**query)
        with path.open('ab') as stream:stream.write(b'bad\n')
        self.store.local_sources.refresh(rid)
        with self.assertRaises(StoreError) as error:self.positions(rid,path,after=second['nextAfter'],stateVersion=second['stateVersion'])
        self.assertEqual(error.exception.status,409)
        for invalid in ('../outside.jsonl',str(path),'sessions/missing.jsonl'):
            with self.assertRaises(StoreError):self.store.local_sources.file_issues(rid,{'path':invalid})

    def test_old_counters_and_checkpoint_line_numbers_are_not_invented(self):
        root,path=self.folder();path.write_bytes(self.raw(self.meta())+b'bad\n');preview,_=self.collect('codex',root);rid=preview['sourceID']
        state=json.loads(self.store.db.execute('SELECT state FROM local_source_files WHERE source_id=?',(rid,)).fetchone()[0]);state.pop('nextLine')
        with self.store.db:
            self.store.db.execute('DELETE FROM local_source_issues')
            self.store.db.execute('UPDATE local_source_files SET state=? WHERE source_id=?',(json.dumps(state),rid))
        self.assertEqual(self.positions(rid,path)['unlocatedCount'],1)
        with path.open('ab') as stream:stream.write(b'new-bad\n')
        self.store.local_sources.refresh(rid);page=self.positions(rid,path)
        self.assertEqual(page['unlocatedCount'],1);self.assertIsNone(page['items'][0]['line'])
        self.store.local_sources.retry_file(rid,self.status(rid,path));page=self.positions(rid,path)
        self.assertEqual(page['unlocatedCount'],0);self.assertEqual([x['line'] for x in page['items']],[2,3])

    def test_non_newline_eof_and_later_separator_preserve_physical_line_numbers(self):
        root,path=self.folder();path.write_bytes(self.raw(self.meta()).rstrip(b'\n'));preview,_=self.collect('codex',root);rid=preview['sourceID']
        with path.open('ab') as stream:stream.write(b'\nbad\n')
        self.store.local_sources.refresh(rid)
        self.assertEqual(self.positions(rid,path)['items'][0]['line'],2)

    def test_cancel_and_changed_preview_do_not_publish_diagnostics(self):
        root,path=self.folder();path.write_bytes(b'bad\n');preview,_=self.collect('codex',root);rid=preview['sourceID'];before=self.positions(rid,path)
        with path.open('ab') as stream:stream.write(b'bad-again\n')
        preview=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        self.store.local_sources.cancel(preview['id']);self.assertEqual(self.positions(rid,path),before)
        preview=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        path.write_bytes(path.read_bytes().replace(b'bad-again',b'new-again'))
        with self.assertRaises(StoreError):self.store.local_sources.commit(preview['id'])
        self.assertEqual(self.positions(rid,path),before)

    def test_retry_and_removal_replace_only_selected_diagnostics(self):
        root,path=self.folder();path.write_bytes(self.raw(self.meta())+self.raw(self.user())+b'bad\n')
        other=path.with_name('other.jsonl');other.write_bytes(b'other-bad\n')
        preview,_=self.collect('codex',root);rid=preview['sourceID'];before=self.positions(rid,other)
        path.write_bytes(self.raw(self.meta())+self.raw(self.user()))
        self.store.local_sources.retry_file(rid,self.status(rid,path))
        self.assertEqual(self.positions(rid,path)['total'],0);self.assertEqual(self.positions(rid,other),before)
        other.rename(other.with_suffix('.held'));self.store.local_sources.refresh(rid)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM local_source_issues').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM local_inputs').fetchone()[0],1)

    def test_backup_preserves_positions_without_bad_record_bodies(self):
        root,path,lines,rid,_=self.mixed();before=self.positions(rid,path);self.store.close()
        archive=self.root/'positions.zip';report=create_backup(self.root/'node',archive)
        self.assertEqual(report['localInputs'],1);self.assertEqual(report['sourceIssuePositions'],5)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(json.loads(z.read('manifest.json'))['version'],5)
            self.assertNotIn(SYNTHETIC_SECRET,b''.join(z.read(n) for n in z.namelist()))
        destination=self.root/'restored';restore_backup(archive,destination,report['archiveSHA256'],self.root)
        self.store=Store(destination);self.assertEqual(self.positions(rid,path),before)
        source=self.store.local_sources.listing()[0]
        self.assertFalse(source['automatic'])
        self.assertEqual(Path(source['directory']).parent,destination.resolve()/'DisconnectedSources')
        self.assertFalse(Path(source['directory']).exists())

    def test_http_diagnostics_require_owner_and_loopback(self):
        root,path=self.folder();path.write_bytes(b'bad\n');preview,_=self.collect('codex',root);rid=preview['sourceID']
        grant=self.store.knowledge.create_grant({'name':'Synthetic reader','scopes':['confirmedMemory'],'days':1})
        server=Server(('127.0.0.1',0),self.store,'synthetic-position-token');worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def get(token):
            c=http.client.HTTPConnection(*server.server_address,timeout=5)
            c.request('GET','/v1/local-sources/'+rid+'/file-issues?'+urllib.parse.urlencode({'path':'sessions/2026/10/20/'+path.name}),headers={'Authorization':'Bearer '+token})
            r=c.getresponse();body=json.loads(r.read());c.close();return r.status,body
        try:
            self.assertEqual(get('wrong')[0],401);self.assertEqual(get(grant['token'])[0],401)
            self.assertEqual(get(server.token)[1]['items'][0]['line'],1)
            with patch('server.ipaddress.ip_address') as address:
                address.return_value.is_loopback=False
                self.assertEqual(get(server.token)[0],403)
        finally:server.shutdown();server.server_close();worker.join()

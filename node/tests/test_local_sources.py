import datetime as dt
import json
import hashlib
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_node as fixtures
from store import Store, StoreError
from compressed_io import pyzstd


class LocalSourceTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown

    def folder(self, provider='codex'):
        root = self.root/provider
        child = root/('sessions/2026/10/20' if provider=='codex' else 'projects/test-project')
        child.mkdir(parents=True, exist_ok=True)
        return root, child/'fixture.jsonl'

    def write(self, path, values, mode='w'):
        with path.open(mode) as f:
            for value in values:f.write(json.dumps(value, ensure_ascii=False)+'\n')

    def meta(self):
        return {'type':'session_meta','payload':{'id':'13129702-36AB-4964-A8F1-23FC995A9966'}}

    def user(self, text='Synthetic personal plan.', mid='input-1', time='2026-10-20T16:30:00Z'):
        return {'type':'response_item','timestamp':time,
                'metadata':{'client_authored':True},
                'payload':{'type':'message','role':'user','id':mid,'content':[{'type':'input_text','text':text}]}}

    def collect(self, provider, root, automatic=False):
        preview = self.store.local_sources.preview({'provider':provider,'directory':str(root)})
        result = self.store.local_sources.commit(preview['id'], automatic)
        return preview, result

    def compressed(self, path, values, known_size=True):
        self.assertIsNotNone(pyzstd, 'Run scripts/prepare-node-runtime.py before decoder tests')
        raw = ''.join(json.dumps(v)+'\n' for v in values).encode()
        path = path.with_name(path.name+'.zst')
        if known_size:
            path.write_bytes(pyzstd.compress(raw))
        else:
            with pyzstd.ZstdFile(path, 'w') as stream:
                stream.write(raw)
        return path

    def wait_job(self, rid, state):
        end = time.monotonic()+8
        while time.monotonic() < end:
            job = self.store.local_sources.job(rid)
            if job and job['state'] == state:
                return job
            if job and job['state'] in ('blocked', 'failed') and state not in ('blocked', 'failed'):
                self.fail(str(job))
            time.sleep(0.02)
        self.fail('History job did not reach '+state)

    def test_compressed_active_and_archived_mirrors_are_read_once(self):
        root,path=self.folder()
        rows=[self.meta(),self.user()]
        self.write(path,rows)
        self.compressed(path,rows)
        archived=root/'archived_sessions';archived.mkdir()
        self.compressed(archived/'archived.jsonl',rows)
        _,report=self.collect('codex',root)
        self.assertEqual(report['summary']['files'],3)
        self.assertEqual(report['summary']['compressedFiles'],2)
        self.assertEqual(report['summary']['completedFiles'],3)
        self.assertEqual(report['summary']['inputs'],1)
        self.assertEqual(self.collect('codex',root)[1]['added'],0)

    def test_injected_references_and_plugin_context_are_not_personal_inputs(self):
        root,path=self.folder()
        values=[self.meta(),self.user('<recommended_plugins>Automatic tool inventory</recommended_plugins>','plugins'),
                self.user('## Referenced ChatGPT conversation:\nQuoted third-party history','reference'),
                self.user('<app-context>Application metadata</app-context>','app'),
                self.user('My actual instruction.','actual')]
        # Current rollouts can carry only a turn identifier, with no authored flag.
        for value in values:
            value.pop('metadata',None)
        self.write(path,values)
        _,report=self.collect('codex',root)
        self.assertEqual(report['summary']['inputs'],1)
        self.assertEqual(report['summary']['excludedContextBlocks'],3)
        self.assertIn('My actual instruction.',next((self.store.output/'sources').rglob('*.md')).read_text())

    def test_compressed_batches_resume_across_restart_without_expanded_files(self):
        root,path=self.folder()
        compressed=self.compressed(path,[self.meta(),{'type':'tool','content':'x'*5000},self.user()])
        original=hashlib.sha256(compressed.read_bytes()).hexdigest()
        with patch('local_sources.SCAN_BYTES',600),patch('local_sources.LINE_BYTES',512):
            _,first=self.collect('codex',root)
            self.assertGreater(first['summary']['pendingBytes'],0)
            self.store.close();self.store=Store(self.root/'node')
            for _ in range(20):
                _,report=self.collect('codex',root)
                if report['summary']['pendingFiles']==0:break
        self.assertEqual(report['summary']['pendingFiles'],0)
        self.assertEqual(report['summary']['inputs'],1)
        self.assertEqual(hashlib.sha256(compressed.read_bytes()).hexdigest(),original)
        self.assertFalse(list(root.rglob('*.jsonl')))
        self.assertFalse(list(self.store.data.rglob('*.jsonl')))

    def test_compressed_unknown_size_finishes_and_truncated_file_is_visible(self):
        root,path=self.folder()
        self.compressed(path,[self.meta(),self.user()],known_size=False)
        broken=self.compressed(path.with_name('broken.jsonl'),[self.meta(),self.user('Invalid stream','broken')])
        broken.write_bytes(broken.read_bytes()[:-5])
        _,report=self.collect('codex',root)
        self.assertEqual(report['summary']['inputs'],1)
        self.assertEqual(report['summary']['completedFiles'],1)
        self.assertEqual(report['summary']['unreadableFiles'],1)
        self.assertEqual(report['summary']['pendingFiles'],1)

    def test_compressed_preview_refuses_changed_source(self):
        root,path=self.folder()
        compressed=self.compressed(path,[self.meta(),self.user()])
        preview=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        compressed.write_bytes(pyzstd.compress(b'{}\n'))
        with self.assertRaises(StoreError):self.store.local_sources.commit(preview['id'])
        self.assertEqual(self.store.local_sources.listing(),[])

    def test_file_batch_limit_does_not_hide_later_history(self):
        root,path=self.folder()
        for n in range(7):
            self.write(path.with_name(str(n)+'.jsonl'),[self.meta(),self.user(mid='id-'+str(n))])
        with patch('local_sources.MAX_FILES',2):
            _,first=self.collect('codex',root)
            self.assertEqual(first['summary']['files'],7)
            self.assertEqual(first['summary']['completedFiles'],2)
            for _ in range(5):
                _,report=self.collect('codex',root)
                if report['summary']['pendingFiles']==0:break
        self.assertEqual(report['summary']['completedFiles'],7)
        self.assertEqual(report['summary']['inputs'],7)

    def test_history_job_can_pause_restart_and_complete(self):
        root,path=self.folder()
        self.compressed(path,[self.meta(),{'type':'tool','content':'x'*5000},self.user()],known_size=False)
        with patch('local_sources.SCAN_BYTES',600),patch('local_sources.LINE_BYTES',512):
            preview,_=self.collect('codex',root)
            self.assertEqual(preview['summary']['pendingBytes'],0)
            self.assertEqual(preview['summary']['pendingFiles'],1)
            self.assertEqual(preview['summary']['unknownSizeFiles'],1)
            source=self.store.local_sources
            refresh=source.refresh
            def pause_after_batch(rid):
                report=refresh(rid);source.pause_import(rid);return report
            with patch.object(source,'refresh',side_effect=pause_after_batch):
                source.start_import(preview['sourceID'])
                self.wait_job(preview['sourceID'],'paused')
            self.store.close();self.store=Store(self.root/'node')
            self.assertEqual(self.store.local_sources.job(preview['sourceID'])['state'],'paused')
            self.store.local_sources.start_import(preview['sourceID'])
            job=self.wait_job(preview['sourceID'],'complete')
        self.assertGreater(job['batches'],2)
        summary=self.store.local_sources.listing()[0]['summary']
        self.assertEqual(summary['pendingFiles'],0);self.assertEqual(summary['inputs'],1)

    def test_running_history_job_recovers_without_duplicate_outputs(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        preview,_=self.collect('codex',root)
        with patch.object(self.store.local_sources,'resume_jobs'):
            self.store.local_sources.start_import(preview['sourceID'])
        with self.store.db:self.store.db.execute("UPDATE local_source_jobs SET state='running'")
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.store.local_sources.job(preview['sourceID'])['state'],'queued')
        self.store.local_sources.resume_jobs()
        self.wait_job(preview['sourceID'],'complete')
        self.assertEqual(len(list((self.store.output/'sources').rglob('*.md'))),1)

    def test_codex_retains_only_user_inputs_and_safe_config(self):
        root, path = self.folder()
        (root/'config.toml').write_text('model = "fixture-model"\napi_key = "DO-NOT-COPY"\n[env]\nSECRET = "DO-NOT-COPY"\n')
        (root/'auth.json').write_text('{"secret":"AUTH-DO-NOT-COPY"}')
        context = self.user('<environment_context>injected</environment_context>', 'ctx')
        assistant = self.user('Assistant-only secret context.', 'a');assistant['payload']['role']='assistant'
        self.write(path,[self.meta(),context,assistant,self.user('My key: sk-proj-abcdefghijklmnopqrstuv and a Friday plan.')])
        preview, report = self.collect('codex', root)
        self.assertEqual(report['added'],1)
        self.assertEqual(preview['config'],{'file':'config.toml','present':True,'model':'fixture-model'})
        self.assertEqual(report['summary']['redactedInputs'],1)
        self.assertEqual(report['summary']['days'],[{'day':'2026-10-21','count':1}])
        files = list((self.store.output/'sources').rglob('*.md'))
        self.assertEqual(len(files),1)
        text=files[0].read_text()
        self.assertIn('Friday plan',text);self.assertIn('[凭证已隐藏]',text)
        self.assertNotIn('sk-proj-',text);self.assertNotIn('Assistant-only',text);self.assertNotIn('injected',text)
        for file in (self.store.output/'sources').rglob('*'):
            if file.is_file():self.assertNotIn('DO-NOT-COPY',file.read_text())
        self.assertEqual(self.store.analysis.snapshot('2026-10-21')['sources'],[])
        self.assertEqual(self.store.candidates(),[])

    def test_claude_skips_tools_sidechains_and_meta_messages(self):
        root,path=self.folder('claude')
        base={'type':'user','uuid':'user-1','sessionId':'8a59b8e7-bcdc-4d77-b840-d7f57e296623','timestamp':'2026-10-20T08:00:00Z',
              'message':{'role':'user','content':'Synthetic user input.'}}
        tool={**base,'uuid':'tool','message':{'role':'user','content':[{'type':'tool_result','content':'Do not import tool output.'}]}}
        self.write(path,[base,tool,{**base,'uuid':'meta','isMeta':True},{**base,'uuid':'sub','isSidechain':True}])
        _,result=self.collect('claude',root)
        self.assertEqual(result['summary']['inputs'],1)

    def test_repeat_append_restart_and_mirrors_do_not_duplicate(self):
        root,path=self.folder()
        event={'type':'event_msg','timestamp':'2026-10-20T16:30:00.100Z','payload':{'type':'user_message','message':'Synthetic personal plan.'}}
        self.write(path,[self.meta(),self.user(),event])
        _,a=self.collect('codex',root);self.assertEqual(a['summary']['inputs'],1)
        _,b=self.collect('codex',root);self.assertEqual(b['added'],0)
        self.write(path,[self.user(mid='input-2',time='2026-10-20T16:31:00Z')],mode='a')
        _,c=self.collect('codex',root);self.assertEqual(c['summary']['inputs'],2)
        self.store.close();self.store=Store(self.root/'node')
        _,d=self.collect('codex',root);self.assertEqual(d['summary']['inputs'],2);self.assertEqual(d['added'],0)
        self.assertEqual(len(list((self.store.output/'sources').rglob('*.md'))),2)

    def test_bounded_scan_advances_over_large_tool_records(self):
        root,path=self.folder()
        self.write(path,[self.meta(),{'type':'tool','content':'x'*5000},self.user()])
        offsets=[]
        with patch('local_sources.SCAN_BYTES',600),patch('local_sources.LINE_BYTES',512):
            for _ in range(15):
                _,result=self.collect('codex',root)
                offsets.append(result['summary']['processedBytes'])
                if result['summary']['pendingBytes']==0:break
        self.assertEqual(result['summary']['inputs'],1)
        self.assertEqual(result['summary']['skippedRecords'],1)
        self.assertEqual(result['summary']['pendingBytes'],0)
        self.assertEqual(offsets,sorted(offsets))
        self.assertGreater(len(offsets),2)

    def test_half_written_record_is_retried_without_loss(self):
        root,path=self.folder()
        self.write(path,[self.meta()])
        raw=json.dumps(self.user())
        with path.open('a') as f:f.write(raw[:-4])
        _,first=self.collect('codex',root);self.assertEqual(first['summary']['inputs'],0)
        self.assertGreater(first['summary']['pendingBytes'],0)
        with path.open('a') as f:f.write(raw[-4:]+'\n')
        _,second=self.collect('codex',root);self.assertEqual(second['summary']['inputs'],1)

    def test_preview_rejects_changed_bytes_but_accepts_append(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        p=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        path.write_text(path.read_text().replace('personal','modified'))
        with self.assertRaises(StoreError):self.store.local_sources.commit(p['id'])
        self.assertEqual(self.store.local_sources.listing(),[])
        p=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        self.write(path,[self.user('Later input.','later')],mode='a')
        self.store.local_sources.commit(p['id'])
        _,r=self.collect('codex',root)
        self.assertEqual(r['summary']['inputs'],2)

    def test_manual_edit_survives_source_revision_and_output_move(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        p,_=self.collect('codex',root)
        document=next((self.store.output/'sources').rglob('*.md'))
        document.write_text('Human edit must remain.')
        self.write(path,[self.meta(),self.user('Corrected personal plan.')])
        _,r=self.collect('codex',root)
        self.assertEqual(r['conflicts'],1)
        self.assertEqual(document.read_text(),'Human edit must remain.')
        destination=self.root/'chosen'
        self.store.configure({'outputDirectory':str(destination)})
        relative=document.relative_to((self.root/'node/Library').resolve())
        self.assertEqual((destination/relative).read_text(),'Human edit must remain.')
        self.assertEqual(document.read_text(),'Human edit must remain.')
        conflict=self.store.local_sources.listing()[0]['conflicts'][0]
        self.assertIn('Corrected personal plan.',(destination/conflict['generatedPath']).read_text())

    def test_small_conversations_are_available_before_huge_tool_logs(self):
        root,path=self.folder()
        self.write(path,[self.meta(),self.user()])
        large=path.parent/'newest.jsonl'
        self.write(large,[{'type':'tool','content':'x'*10000}])
        with patch('local_sources.SCAN_BYTES',1000),patch('local_sources.LINE_BYTES',512):
            _,result=self.collect('codex',root)
        self.assertEqual(result['summary']['inputs'],1)
        self.assertGreater(result['summary']['pendingBytes'],0)

    def test_automatic_is_opt_in_and_recovers_after_missing_directory(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        preview,_=self.collect('codex',root)
        self.write(path,[self.user('New automatic input.','second')],mode='a')
        self.store.local_sources.catch_up()
        self.assertEqual(self.store.local_sources.listing()[0]['summary']['inputs'],1)
        self.store.local_sources.configure(preview['sourceID'],{'automatic':True})
        renamed=root.with_name('temporarily-unavailable');root.rename(renamed)
        self.store.local_sources.catch_up()
        self.assertTrue(self.store.local_sources.listing()[0]['problem'])
        renamed.rename(root)
        self.store.local_sources.catch_up()
        self.assertEqual(self.store.local_sources.listing()[0]['summary']['inputs'],2)
        self.assertIsNone(self.store.local_sources.listing()[0]['problem'])

    def test_partial_file_write_failure_can_retry_without_duplicates(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user(),self.user('Second input.','second')])
        preview=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        export=self.store.export
        calls=[]
        def interrupted(relative,*args):
            calls.append(relative)
            if len(calls)==2:raise OSError('Synthetic disk interruption')
            return export(relative,*args)
        with patch.object(self.store,'export',interrupted):
            with self.assertRaises(OSError):self.store.local_sources.commit(preview['id'])
        # A partial source remains decodable, and the checkpoint has not advanced.
        self.assertEqual(self.store.local_sources.listing()[0]['summary']['inputs'],0)
        self.store.close();self.store=Store(self.root/'node')
        result=self.store.local_sources.commit(preview['id'])
        self.assertEqual(result['summary']['inputs'],2)
        self.assertEqual(len(list((self.store.output/'sources').rglob('*.md'))),2)
        self.assertEqual(self.store.local_sources.commit(preview['id']),result)

    def test_authenticated_http_preview_cancel_commit_and_input_read(self):
        import http.client
        import threading
        from server import Server
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        server=Server(('127.0.0.1',0),self.store,'synthetic-local-source-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,values=None,authenticated=True):
            conn=http.client.HTTPConnection(*server.server_address,timeout=5)
            headers={'Content-Type':'application/json'}
            if authenticated:headers['Authorization']='Bearer '+server.token
            conn.request(method,path,body=json.dumps(values) if values is not None else None,headers=headers)
            reply=conn.getresponse();code=reply.status;value=json.loads(reply.read());conn.close()
            return code,value
        try:
            self.assertEqual(request('GET','/v1/local-sources',authenticated=False)[0],401)
            with patch('server.ipaddress.ip_address') as address:
                address.return_value.is_loopback=False
                self.assertEqual(request('GET','/v1/local-sources')[0],403)
            code,p=request('POST','/v1/local-sources/preview',{'provider':'codex','directory':str(root)})
            self.assertEqual(code,200);self.assertEqual(p['summary']['inputs'],1)
            self.assertEqual(request('POST','/v1/local-sources/previews/'+p['id']+'/cancel',{})[0],200)
            self.assertEqual(self.store.local_sources.listing(),[])
            _,p=request('POST','/v1/local-sources/preview',{'provider':'codex','directory':str(root)})
            self.assertEqual(request('POST','/v1/local-sources/previews/'+p['id']+'/commit',{'automatic':False})[0],200)
            _,page=request('GET','/v1/local-sources/'+p['sourceID']+'/inputs')
            self.assertEqual(len(page['items']),1)
            self.assertEqual(page['items'][0]['kind'],'input')
            code,recent=request('GET','/v1/local-sources/recent-inputs?limit=8')
            self.assertEqual(code,200)
            self.assertEqual(recent['items'][0]['provider'],'codex')
            self.assertEqual(recent['items'][0]['id'],page['items'][0]['id'])
            self.assertEqual(request('POST','/v1/local-sources/'+p['sourceID']+'/refresh',{})[1]['added'],0)
            coverage_path='/v1/local-sources/'+p['sourceID']+'/files?attention=false'
            self.assertEqual(request('GET',coverage_path,authenticated=False)[0],401)
            with patch('server.ipaddress.ip_address') as address:
                address.return_value.is_loopback=False
                self.assertEqual(request('GET',coverage_path)[0],403)
            code,coverage=request('GET',coverage_path)
            self.assertEqual(code,200);self.assertEqual(coverage['total'],1)
            code,report=request('POST','/v1/local-sources/'+p['sourceID']+'/retry-file',coverage['items'][0])
            self.assertEqual(code,200);self.assertEqual(report['added'],0)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_rewritten_growing_file_restarts_scan_without_duplicate_inputs(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user('Short plan.')])
        self.collect('codex',root)
        self.write(path,[self.meta(),self.user('A corrected and considerably longer plan.')])
        _,r=self.collect('codex',root)
        self.assertEqual(r['summary']['inputs'],1)
        self.assertEqual(r['updated'],1)
        self.assertIn('considerably longer',next((self.store.output/'sources').rglob('*.md')).read_text())

    def test_stale_preview_cannot_roll_back_progress_or_reenable_collection(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        a=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        b=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        self.store.local_sources.commit(a['id'],True)
        with self.assertRaises(StoreError):self.store.local_sources.commit(b['id'])
        c=self.store.local_sources.preview({'provider':'codex','directory':str(root)})
        self.store.local_sources.configure(c['sourceID'],{'automatic':False})
        with self.assertRaises(StoreError):self.store.local_sources.commit(c['id'],True)
        self.assertFalse(self.store.local_sources.listing()[0]['automatic'])

    def test_symlinks_are_not_followed_and_missing_dates_are_not_invented(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user(time='bad-date')])
        outside=self.root/'outside.jsonl';self.write(outside,[self.user('Do not import.')])
        (path.parent/'linked.jsonl').symlink_to(outside)
        _,r=self.collect('codex',root)
        self.assertEqual(r['summary']['inputs'],0);self.assertEqual(r['summary']['skippedRecords'],1)
        link=self.root/'linked-root';link.symlink_to(root,target_is_directory=True)
        with self.assertRaises(StoreError):self.store.local_sources.preview({'provider':'codex','directory':str(link)})

    def test_read_scope_does_not_expand_old_grants_and_export_has_only_owned_text(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        _,report=self.collect('codex',root)
        rid=self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        with self.assertRaises(StoreError):self.store.knowledge.read_source('input',rid,['notes'],{})
        value=self.store.knowledge.read_source('input',rid,['localInputs'],{})
        self.assertEqual(value['evidenceType'],'local_user_input')
        self.assertEqual(len(self.store.knowledge.query({'query':'personal','scopes':['localInputs']})['sources']),1)
        import zipfile
        package=self.store.exchange.export()
        with zipfile.ZipFile(package) as archive:
            names=archive.namelist()
            self.assertTrue(any(n.startswith('sources/codex/') and n.endswith('.md') for n in names))
            self.assertFalse(any('auth.json' in n or 'settings.json' in n or 'config.toml' in n for n in names))

    def test_recent_inputs_are_bounded_ordered_and_read_owned_versions(self):
        codex,path=self.folder();self.write(path,[self.meta(),self.user(time='2026-10-21T08:00:00Z')])
        self.collect('codex',codex)
        claude,path=self.folder('claude')
        self.write(path,[{'type':'user','uuid':'claude-input','sessionId':'claude-session',
                         'timestamp':'2026-10-20T08:00:00Z','message':{'role':'user','content':'Earlier Claude input.'}}])
        self.collect('claude',claude)
        document=next((self.store.output/'sources/codex').rglob('*.md'))
        document.write_text('User-owned revision.')
        page=self.store.local_sources.inputs(None,{'limit':1})
        self.assertEqual(len(page['items']),1)
        self.assertEqual(page['items'][0]['provider'],'codex')
        self.assertEqual(page['items'][0]['excerpt'],'User-owned revision.')
        self.assertEqual(page['nextOffset'],1)
        second=self.store.local_sources.inputs(None,{'limit':1,'offset':1})
        self.assertEqual(second['items'][0]['provider'],'claude')
        self.assertIsNone(second['nextOffset'])

    def file_status(self, rid, path):
        return next(item for item in self.store.local_sources.file_coverage(rid, {'attention':'false'})['items']
                    if item['path'].endswith(path.name))

    def test_file_coverage_has_reasons_pagination_and_no_source_text(self):
        root,path=self.folder()
        self.write(path,[self.meta(),self.user('Private fixture prose.'),self.user(time='invalid'),self.user('x'*100,'too-long')])
        with path.open('a') as stream:stream.write('{invalid}\n')
        healthy=path.with_name('healthy.jsonl');self.write(healthy,[self.meta(),self.user('Second input.','second')])
        with patch('local_sources.MAX_INPUT',64):preview,_=self.collect('codex',root)
        rid=preview['sourceID'];source=self.store.local_sources
        coverage=source.file_coverage(rid,{})
        self.assertEqual(coverage['total'],1)
        item=coverage['items'][0]
        self.assertEqual(item['status'],'skipped')
        self.assertEqual(item['issues'],{'oversized':1,'invalidDates':1,'malformed':1,'other':0})
        self.assertNotIn('Private fixture prose',json.dumps(coverage))
        first=source.file_coverage(rid,{'attention':'false','limit':1})
        self.assertEqual(first['total'],2);self.assertEqual(first['nextOffset'],1)
        second=source.file_coverage(rid,{'attention':'false','limit':1,'offset':1})
        self.assertEqual(second['items'][0]['status'],'complete');self.assertIsNone(second['nextOffset'])
        with self.assertRaises(StoreError):source.file_coverage(rid,{'limit':'invalid'})

    def test_selected_file_retry_preserves_other_progress_edits_and_source_bytes(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user('Original input.')])
        other=path.with_name('other.jsonl');self.write(other,[self.meta(),self.user('Other input.','other')])
        preview,_=self.collect('codex',root);rid=preview['sourceID'];source=self.store.local_sources
        before_other=dict(self.store.db.execute('SELECT * FROM local_source_files WHERE source_id=? AND path LIKE ?', (rid,'%other.jsonl')).fetchone())
        output=self.store.db.execute("SELECT id FROM local_inputs WHERE text='Original input.'").fetchone()[0]
        archived=self.store.output/('sources/codex/'+rid+'/inputs/'+output+'.md')
        archived.write_text('Manual correction stays here.')
        self.write(path,[self.meta(),self.user('Revised input.'),self.user('Recovered input.','recovered')])
        self.write(other,[self.user('Not selected this time.','not-selected')],'a')
        original={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path,other)}
        report=source.retry_file(rid,self.file_status(rid,path))
        self.assertEqual(report['added'],1);self.assertEqual(report['updated'],1);self.assertEqual(report['conflicts'],1)
        self.assertEqual(report['summary']['inputs'],3)
        self.assertEqual(archived.read_text(),'Manual correction stays here.')
        after_other=dict(self.store.db.execute('SELECT * FROM local_source_files WHERE source_id=? AND path LIKE ?', (rid,'%other.jsonl')).fetchone())
        self.assertEqual(before_other,after_other)
        self.assertEqual(original,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path,other)})
        again=source.retry_file(rid,self.file_status(rid,path))
        self.assertEqual(again['added'],0);self.assertEqual(again['updated'],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM local_inputs').fetchone()[0],3)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)

    def test_selected_file_retry_rejects_stale_unknown_and_symlink_paths(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        preview,_=self.collect('codex',root);rid=preview['sourceID'];source=self.store.local_sources
        stale=self.file_status(rid,path)
        self.write(path,[self.user('Appended input.','append')],'a');source.refresh(rid)
        with self.assertRaises(StoreError) as error:source.retry_file(rid,stale)
        self.assertEqual(error.exception.status,409)
        for bad in ('../outside.jsonl','sessions/not-in-inventory.jsonl',str(path)):
            with self.assertRaises(StoreError):source.retry_file(rid,{'path':bad,'stateVersion':stale['stateVersion']})
        latest=self.file_status(rid,path);path.rename(path.with_suffix('.saved'))
        outside=self.root/'outside.jsonl';self.write(outside,[self.meta(),self.user('Outside input.','outside')])
        path.symlink_to(outside)
        with self.assertRaises(StoreError):source.retry_file(rid,latest)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM local_inputs').fetchone()[0],2)

    def test_unfinished_tail_is_waiting_and_recovers_after_restart(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        tail=json.dumps(self.user('Completed later.','tail'))+'\n'
        with path.open('a') as stream:stream.write(tail[:50])
        preview,report=self.collect('codex',root,True);rid=preview['sourceID']
        self.assertEqual(report['summary']['waitingFiles'],1)
        self.assertEqual(self.file_status(rid,path)['status'],'waiting')
        self.store.local_sources.start_import(rid);self.wait_job(rid,'waiting')
        self.store.close();self.store=Store(self.root/'node')
        self.assertEqual(self.file_status(rid,path)['status'],'waiting')
        with path.open('a') as stream:stream.write(tail[50:])
        self.store.local_sources.catch_up()
        summary=self.store.local_sources.listing()[0]['summary']
        self.assertEqual(summary['inputs'],2);self.assertEqual(summary['waitingFiles'],0)
        self.assertEqual(summary['skippedRecords'],0);self.assertEqual(summary['pendingFiles'],0)
        self.assertEqual(self.store.local_sources.job(rid)['state'],'complete')

    def test_selected_large_file_continues_in_bounded_batches(self):
        root,path=self.folder();self.write(path,[self.meta(),{'type':'tool','content':'x'*5000},self.user()])
        with patch('local_sources.SCAN_BYTES',600),patch('local_sources.LINE_BYTES',512):
            preview,report=self.collect('codex',root);rid=preview['sourceID']
            self.assertGreater(report['summary']['pendingFiles'],0)
            for _ in range(20):
                before=self.file_status(rid,path)
                report=self.store.local_sources.retry_file(rid,before)
                self.assertGreater(self.file_status(rid,path)['processedBytes'],before['processedBytes'])
                if report['summary']['pendingFiles']==0:break
        self.assertEqual(report['summary']['inputs'],1)
        self.assertEqual(report['summary']['skippedRecords'],1)
        self.assertEqual(report['summary']['pendingFiles'],0)

    def test_legacy_skip_counters_stay_visible_without_invented_reasons(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        preview,_=self.collect('codex',root);rid=preview['sourceID']
        row=self.store.db.execute('SELECT * FROM local_source_files WHERE source_id=?',(rid,)).fetchone()
        state=json.loads(row['state']);state['skipped']=3;state.pop('oversized',None)
        with self.store.db:self.store.db.execute('UPDATE local_source_files SET state=? WHERE source_id=?',(json.dumps(state),rid))
        item=self.file_status(rid,path)
        self.assertEqual(item['status'],'skipped');self.assertEqual(item['issues']['other'],3)
        self.assertEqual(item['issues']['oversized'],0)

    def test_selected_retry_refuses_active_import(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user()])
        preview,_=self.collect('codex',root);rid=preview['sourceID']
        with patch.object(self.store.local_sources,'resume_jobs'):
            self.store.local_sources.start_import(rid)
        with self.assertRaises(StoreError) as error:self.store.local_sources.retry_file(rid,self.file_status(rid,path))
        self.assertEqual(error.exception.status,409)

    def test_retry_revalidates_read_bytes_even_if_checkpoint_is_unchanged(self):
        root,path=self.folder();self.write(path,[self.meta(),self.user('First input.')])
        preview,_=self.collect('codex',root);rid=preview['sourceID'];source=self.store.local_sources
        item=self.file_status(rid,path)
        reread=source.preview({'provider':'codex','directory':str(root)},item['path'],item['stateVersion'])
        self.assertEqual(reread['changedFiles'],1)
        path.write_text(path.read_text().replace('First input.','Other input.'))
        with self.assertRaises(StoreError) as error:source.commit(reread['id'])
        self.assertEqual(error.exception.status,409)
        self.assertEqual(self.store.db.execute('SELECT text FROM local_inputs').fetchone()[0],'First input.')

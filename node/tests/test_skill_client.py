import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import test_node as fixtures
from server import Server

class SkillClientTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown

    def test_published_schema_describes_all_actual_source_scopes(self):
        from knowledge import SCOPES
        path = Path(__file__).resolve().parents[2]/'integrations/lifto-library/references/openapi.json'
        schema = json.loads(path.read_text())
        enums = []
        def visit(value):
            if isinstance(value, dict):
                if 'enum' in value: enums.append(set(value['enum']))
                for child in value.values(): visit(child)
            elif isinstance(value, list):
                for child in value: visit(child)
        visit(schema)
        scope_enums = [value for value in enums if 'confirmedMemory' in value]
        kind_enums = [value for value in enums if 'recording' in value]
        self.assertEqual(len(scope_enums), 3)
        self.assertEqual(len(kind_enums), 2)
        for value in scope_enums: self.assertEqual(value, set(SCOPES))
        for value in kind_enums: self.assertEqual(value, set(SCOPES.values()))

    def test_packaged_client_uses_read_credential_and_rejects_write_scope(self):
        note = self.store.knowledge.save_note({'title':'Synthetic integration note','text':'A synthetic project review on Friday.'})
        preview = self.store.documents.preview({'files':[{'name':'source.txt','text':'Synthetic imported orchard document.'}]})
        self.store.documents.commit(preview['id'])
        document = self.store.documents.listing({})['items'][0]
        source = self.root/'codex-fixture'
        (source/'sessions').mkdir(parents=True)
        events = [{'type':'session_meta','payload':{'id':'830b0fd7-1a36-49a9-94e9-c972754f4b77'}},
                  {'type':'response_item','timestamp':'2026-10-21T00:00:00Z','metadata':{'client_authored':True},
                   'payload':{'type':'message','role':'user','id':'synthetic-input',
                              'content':[{'type':'input_text','text':'Synthetic saffron project input.'}]}}]
        (source/'sessions/fixture.jsonl').write_text(''.join(json.dumps(event)+'\n' for event in events))
        preview = self.store.local_sources.preview({'provider':'codex','directory':str(source)})
        self.store.local_sources.commit(preview['id'])
        input_id = self.store.db.execute('SELECT id FROM local_inputs').fetchone()[0]
        grant = self.store.knowledge.create_grant({'name':'temporary test only','scopes':['notes'],'days':1})
        token_file = self.root/'reader-key';token_file.write_text(grant['token']);token_file.chmod(0o600)
        server = Server(('127.0.0.1',0),self.store,'isolated-admin')
        worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        script = Path(__file__).resolve().parents[2]/'integrations/lifto-library/scripts/lifto.py'
        env = {**os.environ,'LIFTO_URL':'http://127.0.0.1:'+str(server.server_port),'LIFTO_TOKEN_FILE':str(token_file)}
        def call(*args):
            result = subprocess.run([sys.executable,str(script),*args],env=env,capture_output=True,text=True,timeout=10)
            self.assertNotIn(grant['token'],result.stdout+result.stderr)
            return result
        try:
            result=call('capabilities');self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout)['scopes'],['notes'])
            result=call('search','project');self.assertEqual(json.loads(result.stdout)['sources'][0]['id'],note['id'])
            result=call('read','note',note['id']);self.assertEqual(json.loads(result.stdout)['text'],note['text'])
            result=call('query','project','--scope','daily');self.assertEqual(result.returncode,1)
            self.assertEqual(json.loads(result.stderr)['status'],403)
            result=call('read','document',document['id']);self.assertEqual(json.loads(result.stderr)['status'],403)
            result=call('read','input',input_id);self.assertEqual(json.loads(result.stderr)['status'],403)
            token_file.chmod(0o644);self.assertEqual(call('capabilities').returncode,1)
            token_file.chmod(0o600)
            self.store.knowledge.revoke_grant(grant['id'])
            result=call('capabilities');self.assertEqual(json.loads(result.stderr)['status'],401)
            grant = self.store.knowledge.create_grant({'name':'explicit document fixture','scopes':['documents'],'days':1})
            token_file.write_text(grant['token'])
            result=call('query','orchard','--scope','documents');self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout)['sources'][0]['id'],document['id'])
            result=call('read','document',document['id']);self.assertEqual(result.returncode,0)
            self.assertIn('orchard',json.loads(result.stdout)['text'])
            self.store.knowledge.revoke_grant(grant['id'])
            grant = self.store.knowledge.create_grant({'name':'explicit tool input fixture','scopes':['localInputs'],'days':1})
            token_file.write_text(grant['token'])
            result=call('query','saffron','--scope','localInputs');self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout)['sources'][0]['id'],input_id)
            result=call('read','input',input_id);self.assertEqual(result.returncode,0)
            self.assertIn('saffron',json.loads(result.stdout)['text'])
        finally:server.shutdown();server.server_close();worker.join()

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import test_node as fixtures
from backup import BackupError
from library_location import inspect_library, prepare_library
from store import Store


class LibraryLocationTests(unittest.TestCase):
    setUp=fixtures.NodeTests.setUp
    tearDown=fixtures.NodeTests.tearDown

    def files(self):
        return {str(f.relative_to(self.store.output)):hashlib.sha256(f.read_bytes()).hexdigest()
                for f in self.store.output.rglob('*') if f.is_file()}

    def test_live_preview_is_read_only_and_never_returns_bodies_or_credentials(self):
        self.store.knowledge.save_note({'title':'Private synthetic title','text':'Do not emit this body.'})
        with self.store.db:self.store.db.execute("INSERT INTO settings VALUES ('credential','Do not emit this secret.')")
        before=self.files();dump=list(self.store.db.iterdump())
        result=inspect_library(self.store.data)
        self.assertEqual(result['notes'],1);self.assertEqual(result['recordings'],0)
        self.assertEqual(result['nodeID'],self.store.setting('nodeID'))
        self.assertTrue(result['outputAvailable'])
        encoded=json.dumps(result)
        for text in ('Private synthetic title','Do not emit this body.','Do not emit this secret.'):self.assertNotIn(text,encoded)
        self.assertEqual(dump,list(self.store.db.iterdump()));self.assertEqual(before,self.files())

    def test_explicit_prepare_preserves_identity_text_and_history_but_pauses_external_work(self):
        note=self.store.knowledge.save_note({'title':'Selection test','text':'Initial source.'})
        self.store.knowledge.save_note({'title':note['title'],'text':'Human revision.','etag':note['etag']},note['id'])
        rid=str(uuid.uuid4()).upper()
        with self.store.db:
            self.store.db.execute("UPDATE settings SET value='true' WHERE key='analysisEnabled'")
            self.store.db.execute('''INSERT INTO speech_jobs(id,checksum,service,status,baseline,output_root,updated_at)
                VALUES (?,?,?,'queued',?,?,?)''',(rid,'synthetic','synthetic','baseline',str(self.store.output),'2026-10-24T00:00:00Z'))
            self.store.db.execute("INSERT INTO local_sources(id,provider,directory,automatic) VALUES (?,'codex','/must-not-scan',1)",(rid,))
            self.store.db.execute("INSERT INTO local_source_jobs(source_id,state,started_at,updated_at) VALUES (?,'queued',?,?)",(rid,'2026-10-24T00:00:00Z','2026-10-24T00:00:00Z'))
        before=self.files();original=self.store.setting('nodeID');preview=inspect_library(self.store.data)
        self.store.close()
        result=prepare_library(self.root/'node',preview['snapshot'])
        self.assertTrue(result['automaticProcessingPaused'])
        self.store=Store(self.root/'node')
        self.store.memories.deliver_pending()
        self.assertEqual(self.store.setting('nodeID'),original)
        self.assertEqual(self.store.knowledge.note(note['id'])['text'],'Human revision.')
        self.assertEqual(before,self.files())
        self.assertEqual(self.store.setting('dailyEnabled'),'false')
        self.assertEqual(self.store.setting('analysisEnabled'),'false')
        self.assertEqual(self.store.setting('speechAutomatic'),'')
        self.assertEqual(self.store.db.execute('SELECT status FROM speech_jobs').fetchone()[0],'failed')
        self.assertEqual(tuple(self.store.db.execute('SELECT directory,automatic FROM local_sources').fetchone()),('/must-not-scan',0))
        self.assertEqual(self.store.db.execute('SELECT state FROM local_source_jobs').fetchone()[0],'paused')
        with patch.object(self.store.local_sources,'refresh') as scan:
            self.store.local_sources.resume_jobs()
            worker=self.store.local_sources.worker
            if worker:worker.join(timeout=2);self.assertFalse(worker.is_alive())
            self.store.local_sources.catch_up();scan.assert_not_called()

    def test_running_library_cannot_be_prepared_by_another_process(self):
        preview=inspect_library(self.store.data)
        with (self.store.data/'instance.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BackupError):prepare_library(self.store.data,preview['snapshot'])
        self.assertEqual(self.store.setting('dailyEnabled'),'true')

    def test_checkpoint_does_not_invalidate_selected_library_identity(self):
        self.store.knowledge.save_note({'title':'Checkpoint','text':'Keep this library.'})
        preview=inspect_library(self.store.data);self.store.close()
        self.assertEqual(preview['snapshot'],inspect_library(self.root/'node')['snapshot'])
        prepare_library(self.root/'node',preview['snapshot'])

    def test_identity_or_output_change_requires_reselection_and_never_pauses_new_library(self):
        preview=inspect_library(self.store.data)
        with self.store.db:self.store.db.execute("UPDATE settings SET value=? WHERE key='nodeID'",(str(uuid.uuid4()),))
        with self.assertRaises(BackupError):prepare_library(self.store.data,preview['snapshot'])
        self.assertEqual(self.store.setting('dailyEnabled'),'true')
        preview=inspect_library(self.store.data)
        another=self.root/'another-output';another.mkdir()
        with self.store.db:self.store.db.execute("UPDATE settings SET value=? WHERE key='outputDirectory'",(str(another),))
        with self.assertRaises(BackupError):prepare_library(self.store.data,preview['snapshot'])
        self.assertEqual(list(another.iterdir()),[])

    def test_database_replacement_is_detected_even_with_same_node_id(self):
        preview=inspect_library(self.store.data);self.store.close()
        database=self.root/'node/node.sqlite3';replacement=self.root/'copy.sqlite3'
        replacement.write_bytes(database.read_bytes());os.replace(replacement,database)
        with self.assertRaises(BackupError):prepare_library(self.root/'node',preview['snapshot'])

    def test_missing_folder_or_database_does_not_create_an_empty_library(self):
        missing=self.root/'disconnected-drive'/'library'
        with self.assertRaises(BackupError):inspect_library(missing)
        self.assertFalse(missing.parent.exists())
        empty=self.root/'ordinary-text-folder';empty.mkdir();(empty/'note.txt').write_text('Keep this ordinary file.')
        with self.assertRaises(BackupError):inspect_library(empty)
        self.assertEqual([p.name for p in empty.iterdir()],['note.txt'])

    def test_missing_output_is_reported_without_recreating_it(self):
        output=self.root/'unavailable-output'
        with self.store.db:self.store.db.execute("UPDATE settings SET value=? WHERE key='outputDirectory'",(str(output),))
        preview=inspect_library(self.store.data);self.assertFalse(preview['outputAvailable'])
        with self.assertRaises(BackupError):prepare_library(self.store.data,preview['snapshot'])
        self.assertFalse(output.exists());self.assertEqual(self.store.setting('dailyEnabled'),'true')

    def test_trigger_schema_and_symlinks_are_rejected_before_any_update(self):
        with self.store.db:self.store.db.execute('''CREATE TRIGGER unexpected AFTER UPDATE ON settings
            BEGIN DELETE FROM notes; END''')
        with self.assertRaises(BackupError):inspect_library(self.store.data)
        self.store.close()
        linked=self.root/'linked';linked.symlink_to(self.root/'node',target_is_directory=True)
        with self.assertRaises(BackupError):inspect_library(linked)

    def test_invalid_cli_file_gives_a_safe_error_without_traceback(self):
        bad=self.root/'broken';bad.mkdir();(bad/'node.sqlite3').write_bytes(b'Private invalid database content')
        script=Path(__file__).parents[1]/'library_location_cli.py'
        result=subprocess.run([sys.executable,str(script),'inspect','--data',str(bad)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1);self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertEqual(result.stderr,'');self.assertNotIn('Private invalid',result.stdout)

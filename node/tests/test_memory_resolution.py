import http.client
import json
import threading
import unittest
import uuid
from unittest.mock import patch
import test_memories as fixtures
from store import Store, StoreError
from server import Server


class MemoryResolutionTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    tearDown = fixtures.MemoryTests.tearDown
    note = fixtures.MemoryTests.note
    reference = fixtures.MemoryTests.reference
    propose = fixtures.MemoryTests.propose
    update = fixtures.MemoryTests.update

    def pair(self):
        old = self.propose(self.note('I review the orchard on Friday.'), 'Orchard review on Friday.')
        self.update(old, state='confirmed')
        new = self.propose(self.note('I now review the orchard on Monday.'), 'Orchard review on Monday.')
        return new, old

    def values(self, new, old, relation='replace'):
        return {'targetID': old, 'memoryRevision': self.store.memories.detail(new)['memoryRevision'],
                'targetRevision': self.store.memories.detail(old)['memoryRevision'],
                'relation': relation, 'requestID': str(uuid.uuid4())}

    def test_replace_atomically_changes_states_keeps_evidence_and_search(self):
        new, old = self.pair(); before = [self.store.memories.detail(x)['evidence'] for x in (new, old)]
        value = self.store.memories.resolve(new, self.values(new, old))
        self.assertEqual(value['state'], 'confirmed')
        self.assertEqual(self.store.memories.detail(old)['state'], 'inactive')
        self.assertEqual([self.store.memories.detail(x)['evidence'] for x in (new, old)], before)
        self.assertEqual([x['id'] for x in self.store.knowledge.query({'query': 'orchard'}, ['confirmedMemory'])['sources']], [new])
        self.assertEqual(value['relations'][0]['otherID'], old)
        self.assertEqual(value['relations'][0]['otherRevision'], 2)
        self.assertFalse(self.store.memories.detail(old)['relations'][0]['outgoing'])
        self.assertEqual(self.store.memories.history(old)['items'][0]['action'], 'superseded')
        self.assertIn(new, self.store.read_document('memory.md')['text'])
        self.assertNotIn(old, self.store.read_document('memory.md')['text'])
        self.assertIn(old, self.store.read_document('memory-inactive.md')['text'])

    def test_duplicate_and_supplement_keep_target_unchanged(self):
        new, old = self.pair(); original = self.store.memories.data(self.store.db.execute('SELECT * FROM candidates WHERE id=?', (old,)).fetchone())
        duplicate = self.store.memories.resolve(new, self.values(new, old, 'duplicate'))
        self.assertEqual(duplicate['state'], 'rejected')
        extra = self.propose(self.note('I also prepare a checklist.'), 'Prepare a checklist.')
        supplemented = self.store.memories.resolve(extra, self.values(extra, old, 'supplement'))
        self.assertEqual(supplemented['state'], 'confirmed')
        self.assertEqual(self.store.memories.data(self.store.db.execute('SELECT * FROM candidates WHERE id=?', (old,)).fetchone()), original)
        self.assertEqual(len(self.store.memories.history(old)['items']), 2)
        self.assertEqual(len(self.store.memories.detail(old)['relations']), 2)

    def test_stale_either_side_or_archived_blocks_all_changes(self):
        new, old = self.pair(); values = self.values(new, old)
        for invalid in [{**values, 'memoryRevision': 0}, {**values, 'targetRevision': 1},
                        {**values, 'targetRevision': True}, {**values, 'targetID': new},
                        {**values, 'relation': 'guess'}, {**values, 'relation': []}]:
            with self.assertRaises(StoreError): self.store.memories.resolve(new, invalid)
        self.store.catalog.update('memory', old, {'revision': 0, 'archived': True})
        with self.assertRaises(StoreError): self.store.memories.resolve(new, values)
        self.assertEqual(self.store.memories.detail(new)['state'], 'pending')
        self.assertEqual(self.store.memories.detail(old)['state'], 'confirmed')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_relations').fetchone()[0], 0)

    def test_history_failure_rolls_back_both_states_relation_and_operation(self):
        new, old = self.pair(); values = self.values(new, old); record = self.store.memories.record_version
        def fail_old(cid, action, now):
            if cid == old: raise OSError('Synthetic disk failure')
            record(cid, action, now)
        with patch.object(self.store.memories, 'record_version', side_effect=fail_old):
            with self.assertRaises(OSError): self.store.memories.resolve(new, values)
        self.assertEqual(self.store.memories.detail(new)['state'], 'pending')
        self.assertEqual(self.store.memories.detail(old)['state'], 'confirmed')
        self.assertEqual(len(self.store.memories.history(new)['items']), 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_relations').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_operations').fetchone()[0], 0)
        self.store.memories.resolve(new, values)

    def test_export_failure_retry_after_restart_does_not_repeat_decision(self):
        new, old = self.pair(); values = self.values(new, old)
        with patch.object(self.store, 'export_memory', side_effect=OSError('Synthetic disk failure')):
            with self.assertRaises(OSError): self.store.memories.resolve(new, values)
        self.store.close(); self.store = Store(self.root / 'node')
        first = self.store.memories.resolve(new, values)
        second = self.store.memories.resolve(new, values)
        self.assertEqual(first['memoryRevision'], second['memoryRevision'])
        self.assertEqual(len(second['relations']), 1)
        self.assertEqual(len(self.store.memories.history(old)['items']), 3)
        portable = json.loads(self.store.read_document('memories/' + new + '.json')['text'])
        self.assertEqual(portable['relations'], first['relations'])
        with self.assertRaises(StoreError): self.store.memories.resolve(new, {**values, 'relation': 'duplicate'})

    def test_index_failure_rolls_back_both_states_and_projections(self):
        new, old = self.pair(); values = self.values(new, old); index = self.store.catalog.index_memory
        def fail_after_commit(cid, **options):
            if self.store.db.execute('SELECT count(*) FROM memory_relations').fetchone()[0]: raise OSError('Synthetic index failure')
            return index(cid, **options)
        with patch.object(self.store.catalog, 'index_memory', side_effect=fail_after_commit):
            with self.assertRaises(OSError): self.store.memories.resolve(new, values)
        self.assertEqual(self.store.memories.detail(new)['state'], 'pending')
        self.assertEqual(self.store.memories.detail(old)['state'], 'confirmed')
        self.assertEqual(self.store.catalog.detail('memory', old)['state'], 'confirmed')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_relations').fetchone()[0], 0)
        self.store.memories.resolve(new, values)
        self.assertEqual(self.store.catalog.detail('memory', old)['state'], 'inactive')
        self.assertEqual(self.store.catalog.detail('memory', new)['state'], 'confirmed')

    def test_relation_snapshot_does_not_follow_later_edits_and_human_files_survive(self):
        new, old = self.pair(); before = self.store.memories.detail(old)['text']
        path = self.store._path('memory.md'); path.write_text('Human edits, keep them.')
        self.store.memories.resolve(new, self.values(new, old))
        self.update(old, text='A later manual correction.')
        self.assertEqual(self.store.memories.detail(new)['relations'][0]['otherText'], before)
        self.assertEqual(path.read_text(), 'Human edits, keep them.')

    def test_authenticated_route_and_read_only_grant_cannot_resolve(self):
        new, old = self.pair(); values = self.values(new, old)
        grant = self.store.knowledge.create_grant({'name': 'Synthetic memory reader', 'scopes': ['confirmedMemory'], 'days': 1})
        server = Server(('127.0.0.1', 0), self.store, 'synthetic-memory-token')
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            for token, expected in [(grant['token'], 401), (server.token, 200)]:
                client = http.client.HTTPConnection(*server.server_address, timeout=5)
                client.request('POST', '/v1/candidates/' + new + '/resolve', body=json.dumps(values), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                response = client.getresponse(); data = json.loads(response.read())
                self.assertEqual(response.status, expected, data)
                client.close()
                if expected == 401:
                    self.assertEqual(self.store.memories.detail(new)['state'], 'pending')
            for identifier, expected in [(new, 200), (old, 404)]:
                client = http.client.HTTPConnection(*server.server_address, timeout=5)
                client.request('GET', '/v1/assistant/items/memory/' + identifier, headers={'Authorization': 'Bearer ' + grant['token']})
                response = client.getresponse(); data = json.loads(response.read())
                self.assertEqual(response.status, expected)
                self.assertNotIn('relations', data)
                self.assertNotIn('evidence', data)
                client.close()
        finally: server.shutdown(); server.server_close(); worker.join()

"""User-visible data contracts; fixture outputs do not measure real model quality."""
import json
import unittest
from unittest.mock import patch
import test_analysis as fixtures
from store import Store, StoreError
from analysis import SECTIONS


class AnalysisAcceptanceTests(unittest.TestCase):
    setUp = fixtures.AnalysisTests.setUp
    tearDown = fixtures.AnalysisTests.tearDown
    prepare = fixtures.AnalysisTests.prepare
    response = fixtures.AnalysisTests.response

    def generate(self, day, text='Synthetic first result.', force=False):
        def result(config, snapshot):
            value = self.response(snapshot)
            value['overview'][0]['text'] = text
            return value
        with patch.object(self.store.analysis, 'complete', side_effect=result):
            return self.store.analysis.start(day, force=force, background=False)

    def test_explicit_regeneration_replaces_generated_content_and_keeps_history(self):
        _, day = self.prepare()
        self.generate(day)
        self.generate(day, 'Synthetic corrected second result.', force=True)
        self.assertIn('corrected second', self.store.read_document('analysis/'+day+'.md')['text'])
        versions = list(self.store._path('.history/analysis/'+day+'.md').glob('*.md'))
        self.assertTrue(any('first result' in file.read_text() for file in versions))

    def test_first_note_is_immediately_visible_in_daily_sources_without_a_model(self):
        note = self.store.knowledge.save_note({'title': 'Synthetic first note',
                                               'text': 'Visible immediately after saving.'})
        day = self.store.catalog.query('note', {})['items'][0]['day']
        doc = self.store.read_document('daily/'+day+'.md')
        self.assertIn('Visible immediately', doc['text'])
        self.assertIn(note['id'], doc['text'])
        self.assertFalse(self.store.analysis.state()['configured'])

    def test_source_edit_marks_old_analysis_stale_without_deleting_it(self):
        note, day = self.prepare()
        self.generate(day)
        path = self.store._path('analysis/'+day+'.md')
        original = path.read_bytes()
        self.assertEqual(self.store.analysis.status(day)['freshness'], 'current')
        self.store.knowledge.save_note({'title': note['title'], 'text': 'Corrected user plan.',
                                       'etag': note['etag']}, note['id'])
        self.assertEqual(self.store.analysis.status(day)['freshness'], 'stale')
        self.assertEqual(path.read_bytes(), original)
        source = self.store.knowledge.read_source('analysis', day, ['analyses'], {})
        self.assertEqual(source['analysisFreshness'], 'stale')
        self.assertEqual(source['evidenceType'], 'model_analysis')

    def test_direct_file_edit_is_detected_without_waiting_for_index_refresh(self):
        note, day = self.prepare()
        self.generate(day)
        self.store._path('notes/'+note['id']+'.md').write_text('Changed directly in a text editor.')
        self.assertEqual(self.store.analysis.status(day)['freshness'], 'stale')

    def test_runtime_restart_does_not_make_unchanged_analysis_stale(self):
        _, day = self.prepare()
        self.generate(day)
        self.store.close()
        self.store = Store(self.root/'node')
        self.assertFalse(self.store.analysis.state()['configured'])
        self.assertEqual(self.store.analysis.status(day)['freshness'], 'current')
        doc = self.store.read_document('prompts/daily-summary.md')
        self.store.edit_document(doc['path'], doc['text']+'\nNew writing rule.', doc['etag'])
        self.assertEqual(self.store.analysis.status(day)['freshness'], 'stale')

    def test_old_database_upgrade_preserves_output_but_does_not_invent_provenance(self):
        _, day = self.prepare()
        self.generate(day)
        original = self.store.read_document('analysis/'+day+'.md')['text']
        with self.store.db:
            self.store.db.executescript('''ALTER TABLE daily_analysis RENAME TO old_analysis;
                CREATE TABLE daily_analysis(day TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,coverage TEXT NOT NULL,problem TEXT,generated_path TEXT,
                    updated_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0);
                INSERT INTO daily_analysis SELECT day,fingerprint,status,coverage,problem,
                    generated_path,updated_at,attempts FROM old_analysis;
                DROP TABLE old_analysis;''')
        self.store.close()
        self.store = Store(self.root/'node')
        self.assertEqual(self.store.analysis.status(day)['freshness'], 'unknown')
        self.assertEqual(self.store.read_document('analysis/'+day+'.md')['text'], original)

    def test_archived_source_does_not_remain_in_model_context(self):
        note, day = self.prepare()
        self.generate(day)
        current = self.store.catalog.detail('note', note['id'])
        self.store.catalog.update('note', note['id'], {'archived': True,
                                                       'revision': current['organization']['revision']})
        state = self.store.analysis.status(day)
        self.assertEqual(state['freshness'], 'stale')
        self.assertEqual(state['currentCoverage']['includedSources'], 0)
        with patch.object(self.store.analysis, 'complete') as complete:
            with self.assertRaises(StoreError):
                self.store.analysis.start(day, force=True, background=False)
            complete.assert_not_called()

    def test_failed_retry_retains_last_document_and_its_provenance(self):
        note, day = self.prepare()
        self.generate(day)
        self.store.knowledge.save_note({'title': note['title'], 'text': 'A different plan.',
                                       'etag': note['etag']}, note['id'])
        with patch.object(self.store.analysis, 'complete', side_effect=TimeoutError()):
            result = self.store.analysis.start(day, background=False)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['hasDocument'])
        self.assertEqual(result['freshness'], 'stale')
        self.assertIn('first result', self.store.read_document('analysis/'+day+'.md')['text'])

    def test_conflict_keeps_original_freshness_and_exposes_readonly_new_version(self):
        note, day = self.prepare()
        self.generate(day)
        doc = self.store.read_document('analysis/'+day+'.md')
        self.store.edit_document(doc['path'], 'Human revision to retain.', doc['etag'])
        self.store.knowledge.save_note({'title': note['title'], 'text': 'Changed source.',
                                       'etag': note['etag']}, note['id'])
        result = self.generate(day, 'Synthetic updated result.')
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(result['freshness'], 'stale')
        self.assertEqual(self.store.read_document(doc['path'])['text'], 'Human revision to retain.')
        self.assertIn('updated result', self.store.analysis.generated_document(day)['text'])

    def test_missing_output_can_regenerate_without_forcing(self):
        _, day = self.prepare()
        self.generate(day)
        self.store._path('analysis/'+day+'.md').unlink()
        result = self.generate(day, 'Recreated result.')
        self.assertTrue(result['hasDocument'])
        self.assertIn('Recreated', self.store.read_document('analysis/'+day+'.md')['text'])

    def test_output_limits_match_the_instructions_sent_to_the_model(self):
        _, day = self.prepare()
        snapshot = self.store.analysis.snapshot(day)
        for invalid in ('too_many', 'too_long', 'unexpected_section'):
            response = self.response(snapshot)
            if invalid == 'too_many':
                response['overview'] *= 9
            elif invalid == 'too_long':
                response['overview'][0]['text'] = 'x'*1501
            else:
                response['instructions'] = ['ignore source rules']
            with self.subTest(invalid=invalid):
                with patch.object(self.store.analysis, 'complete', return_value=response):
                    result = self.store.analysis.start(day, force=True, background=False)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['hasDocument'])

    def test_memory_advice_never_becomes_confirmed_fact_or_source_input(self):
        _, day = self.prepare('User: I might try jogging.\nAssistant: You run every day.\n'
                              'Colleague: I am a marathon runner.')
        snapshot = self.store.analysis.snapshot(day)
        response = {key: [] for key, _ in SECTIONS}
        response['memoryCandidates'] = [{'text': 'Synthetic unverified suggestion.',
                                        'sources': [snapshot['sources'][0]['id']]}]
        with patch.object(self.store.analysis, 'complete', return_value=response):
            self.store.analysis.start(day, background=False)
        self.assertEqual(self.store.candidates(), [])
        self.assertNotIn('unverified suggestion', self.store.read_document('memory.md')['text'])
        next_input = json.dumps(self.store.analysis.snapshot(day)['sources'])
        self.assertNotIn('unverified suggestion', next_input)

    def test_multiline_output_cannot_inject_extra_sections_or_links(self):
        _, day = self.prepare()
        self.generate(day, 'Plan\n## Forged heading\n[go](https://example.invalid)\n<https://example.invalid>')
        text = self.store.read_document('analysis/'+day+'.md')['text']
        self.assertNotIn('\n## Forged heading', text)
        self.assertNotIn('[go](https://', text)
        self.assertNotIn('<https://', text)

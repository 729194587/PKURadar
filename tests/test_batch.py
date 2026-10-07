import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from unittest.mock import Mock, patch

from pku_radar.observability import LiveObserver
from pku_radar.pipeline import run_pipeline
from pku_radar.ranking import LLMRanker
from pku_radar.storage import Store
from test_lifecycle import NOW, PREFS, Source, Writer, notice
from test_live import response


def decision(identity, **changes):
    return dict(external_id=identity, recommend=True, priority='high', reason='相关机会', **changes)


class BatchTests(unittest.TestCase):
    def setUp(self):
        network = patch('socket.socket', side_effect=AssertionError('No network'))
        network.start()
        self.addCleanup(network.stop)
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def run_batch(self, content, identities=('a', 'b'), **kwargs):
        output = content if isinstance(content, Exception) else response({
            'choices': [{'message': {'content': content if isinstance(content, str) else json.dumps(content)}}],
            'usage': {'total_tokens': 7}})
        ranker = LLMRanker('https://example.test', '', 'test', opener=Mock(side_effect=[output]))
        result = run_pipeline(self.store, Source([notice(i) for i in identities]), ranker,
                              PREFS, clock=lambda: NOW, writer=kwargs.pop('writer', Writer()), **kwargs)
        self.assertEqual(ranker.opener.call_count, 1)
        return result

    def rows(self):
        return {r['external_id']: r for r in self.store.db.execute('SELECT * FROM items')}

    def test_sixteen_items_two_calls_and_usage(self):
        def respond(request, **kwargs):
            body = json.loads(request.data)
            self.assertEqual(body['thinking'], {'type': 'disabled'})
            payload = json.loads(body['messages'][1]['content'])
            sizes.append(len(payload['notices']))
            return response({'choices': [{'message': {'content': json.dumps([
                decision(n['external_id']) for n in reversed(payload['notices'])])}}],
                'usage': {'total_tokens': 7}})
        sizes = []
        ranker = LLMRanker('https://example.test', '', 'test', opener=Mock(side_effect=respond))
        observer = LiveObserver(self.directory.name)
        with redirect_stderr(io.StringIO()) as stderr:
            result = run_pipeline(self.store, Source([notice(str(i)) for i in range(16)]), ranker,
                                  PREFS, clock=lambda: NOW, writer=Writer(), observer=observer)
        self.assertEqual(sizes, [15, 1])
        self.assertEqual((result['status'], result['ranked_count'], result['surfaced_count']), ('success', 16, 16))
        events = [json.loads(line) for line in observer.path.read_text(encoding='utf-8').splitlines()]
        end = events[-1]
        self.assertEqual((end['llm_calls'], end['ranking_succeeded'], end['ranking_failed'], end['total_tokens']), (2, 16, 0, 14))
        batches = [e for e in events if e['event'] == 'ranking_batch_finished']
        self.assertEqual(end['ranking_duration_ms'], sum(e['duration_ms'] for e in batches))
        self.assertTrue(all(e['success'] for e in batches))
        self.assertEqual(stderr.getvalue().count('[rank batch'), 2)
        self.assertTrue(all('usage' not in e for e in events if e['event'] == 'ranking_finished'))

    def test_schema_invalid_isolated(self):
        bad = decision('a'); bad['priority'] = 'urgent'
        result = self.run_batch([bad, decision('b')])
        self.assertEqual((result['status'], result['ranked_count']), ('partial', 1))
        self.assertEqual(self.rows()['a']['rank_status'], 'failed')
        self.assertEqual(self.rows()['b']['rank_status'], 'succeeded')

    def test_missing_result(self):
        self.run_batch([decision('b')])
        self.assertEqual(self.rows()['a']['rank_status'], 'failed')
        self.assertEqual(self.rows()['b']['rank_status'], 'succeeded')

    def test_missing_id_field_isolated(self):
        bad = decision('a'); del bad['external_id']
        self.run_batch([bad, decision('b')])
        self.assertEqual(self.rows()['a']['rank_status'], 'failed')
        self.assertEqual(self.rows()['b']['rank_status'], 'succeeded')

    def test_duplicate_never_overwrites(self):
        bad = decision('a'); bad['reason'] = 'conflicting'
        self.run_batch([decision('a'), decision('b'), bad])
        self.assertEqual(self.rows()['a']['rank_status'], 'failed')
        self.assertIsNone(self.rows()['a']['recommendation_reason'])
        self.assertEqual(self.rows()['b']['rank_status'], 'succeeded')

    def test_unknown_never_written(self):
        result = self.run_batch([decision('a'), decision('unknown'), decision('b')])
        self.assertEqual(result['ranked_count'], 2)
        self.assertEqual(set(self.rows()), {'a', 'b'})

    def test_unattributable_junk_preserves_valid(self):
        self.run_batch([None, [], {'external_id': []}, decision('b')])
        self.assertEqual(self.rows()['a']['rank_status'], 'failed')
        self.assertEqual(self.rows()['b']['rank_status'], 'succeeded')

    def test_provider_failure_and_cross_run_retry(self):
        first = self.run_batch(TimeoutError('timeout'))
        self.assertEqual(first['status'], 'failed')
        self.assertTrue(all(r['rank_attempts'] == 1 for r in self.rows().values()))
        second = self.run_batch([decision('a'), decision('b')], identities=())
        self.assertEqual(second['ranked_count'], 2)
        self.assertTrue(all(r['rank_attempts'] == 2 for r in self.rows().values()))

    def test_malformed_top_level(self):
        for content in ('not JSON', '{}', 'null', '[null, []]'):
            with self.subTest(content=content):
                self.store.db.execute('DELETE FROM items')
                result = self.run_batch(content)
                self.assertEqual((result['status'], result['ranked_count']), ('failed', 0))
                self.assertTrue(all(r['rank_attempts'] == 1 for r in self.rows().values()))

    def test_rerank_preserves_result_and_writer_failure_not_surfaced(self):
        self.run_batch([decision('a'), decision('b')], writer=Writer(fail=True))
        self.assertTrue(all(r['surfaced_at'] is None for r in self.rows().values()))
        result = self.run_batch(OSError('failed'), identities=(), rerank=True)
        self.assertEqual((result['status'], result['surfaced_count']), ('failed', 2))
        self.assertTrue(all(r['rank_status'] == 'succeeded' and r['rank_attempts'] == 1 for r in self.rows().values()))

    def test_same_external_id_different_providers(self):
        notices = [notice('a'), replace(notice('a'), provider='other')]
        ranker = LLMRanker('https://example.test', '', 'test', opener=Mock(side_effect=[
            response({'choices': [{'message': {'content': json.dumps([decision('a')])}}]}) for _ in notices]))
        result = run_pipeline(self.store, Source(notices), ranker, PREFS, clock=lambda: NOW, writer=Writer())
        self.assertEqual((result['ranked_count'], ranker.opener.call_count), (2, 2))

    def test_trace_run_one_separate_databases(self):
        observers = []
        for _ in range(2):
            store = Store(':memory:')
            self.addCleanup(store.close)
            observer = LiveObserver(self.directory.name)
            observer.begin(store.start_run(NOW), NOW)
            self.addCleanup(observer.stream.close)
            observer.emit('test')
            observers.append(observer)
        self.assertNotEqual(observers[0].path, observers[1].path)
        self.assertTrue(all(len(o.path.read_text().splitlines()) == 1 for o in observers))

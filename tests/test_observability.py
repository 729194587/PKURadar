import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from pku_radar.__main__ import main
from pku_radar.observability import LiveObserver
from pku_radar.ranking import LLMRanker, RANKING_PROMPT
from pku_radar.source import PKUKnowSource
from test_live import response


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        previous = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, previous)
        network = patch('socket.socket', side_effect=AssertionError('No network'))
        network.start()
        self.addCleanup(network.stop)

    def live(self, outputs, *, pages=None):
        key = 'dummy-test-credential'
        ranker = LLMRanker('https://example.test/v1', key, 'test-model',
                           opener=Mock(side_effect=[response(output) for output in outputs]))
        pages = pages or [response({'items': [{'id': str(i), 'title': 'AI'} for i in range(len(outputs))]}),
                          response({'items': []}), response({'items': []})]
        source = PKUKnowSource(opener=Mock(side_effect=pages), sleep=Mock())
        original_fetch = PKUKnowSource.fetch
        def fetch(instance):
            source.on_page = instance.on_page
            return original_fetch(source)
        with patch.dict(os.environ, {'LLM_API_KEY': key, 'LLM_MODEL': 'test-model',
                                     'LLM_BASE_URL': 'https://user:password@example.test/v1?key=secret'}), \
             patch.object(LLMRanker, 'from_env', return_value=ranker), \
             patch.object(PKUKnowSource, 'fetch', fetch), \
             redirect_stderr(io.StringIO()) as stderr, redirect_stdout(io.StringIO()) as stdout:
            status = main(['run', '--live'])
        paths = list(Path('data/traces').glob('*.jsonl'))
        events = [json.loads(line) for path in paths for line in path.read_text(encoding='utf-8').splitlines()]
        return status, events, stderr.getvalue(), stdout.getvalue()

    def output(self, content=None, usage=None):
        result = {'choices': [{'message': {'content': content if content is not None else
                  '{"recommend": true, "priority": "high", "reason": "AI match"}'}}]}
        if usage is not None:
            result['usage'] = usage
        return result

    def test_live_success_usage_and_progress(self):
        usage = dict(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        status, events, stderr, stdout = self.live([self.output(usage=usage), self.output(usage=usage)])
        self.assertEqual(status, 0)
        self.assertEqual(events[0]['event'], 'run_start')
        self.assertEqual(events[0]['llm_base_url'], 'https://example.test/v1')
        self.assertEqual(events[0]['ranking_prompt_sha256'], hashlib.sha256(RANKING_PROMPT.encode()).hexdigest())
        self.assertIn('primary_interests', events[0]['preferences'])
        ranks = [e for e in events if e['event'] == 'ranking_finished']
        self.assertEqual(len(ranks), 2)
        self.assertEqual(ranks[0]['usage'], dict(usage, prompt_cache_hit_tokens=None,
                         prompt_cache_miss_tokens=None, completion_tokens_details={'reasoning_tokens': None}))
        for field in ('prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'reasoning_tokens'):
            self.assertIsNone(events[-1][field])
        self.assertEqual(events[-1]['total_tokens'], 30)
        self.assertEqual(events[-1]['ranking_succeeded'], 2)
        self.assertIn('[source] page 1/3: 2 items, 0 bad', stderr)
        self.assertIn('[rank 1/2] HIGH', stderr)
        self.assertIn('LLM calls: 2', stderr)
        self.assertIn('Trace:', stderr)
        self.assertNotIn('[rank', stdout)
        self.assertIn('[PKU RADAR LIVE]', stdout)
        self.assertNotIn('dummy-test-credential', json.dumps(events))

    def test_missing_usage(self):
        status, events, _, _ = self.live([self.output()])
        self.assertEqual(status, 0)
        self.assertIsNone(next(e for e in events if e['event'] == 'ranking_finished')['usage'])
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                      'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'reasoning_tokens'):
            self.assertIsNone(events[-1][field])

    def test_cache_and_reasoning_usage_sums_reported_values_including_failures(self):
        first = dict(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                     prompt_cache_hit_tokens=8, prompt_cache_miss_tokens=2,
                     completion_tokens_details={'reasoning_tokens': 3})
        second = dict(prompt_tokens=6, completion_tokens=2, total_tokens=8,
                      prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=6,
                      completion_tokens_details={'reasoning_tokens': 0})
        status, events, _, _ = self.live([
            self.output(usage=first), self.output('invalid JSON', second), self.output()])
        self.assertEqual(status, 1)
        ranks = [e for e in events if e['event'] in ('ranking_finished', 'ranking_failed')]
        self.assertEqual(ranks[0]['usage'], first)
        self.assertEqual(ranks[1]['usage'], second)
        self.assertIsNone(ranks[2]['usage'])
        for field, expected in dict(prompt_tokens=16, completion_tokens=7, total_tokens=23,
                                    prompt_cache_hit_tokens=8, prompt_cache_miss_tokens=8,
                                    reasoning_tokens=3).items():
            with self.subTest(field=field):
                self.assertEqual(events[-1][field], expected)

    def test_partial_usage_does_not_estimate_missing_or_invalid_counts(self):
        status, events, _, _ = self.live([
            self.output(usage={'prompt_cache_hit_tokens': 0, 'completion_tokens_details': {}}),
            self.output(usage={'prompt_cache_hit_tokens': True, 'prompt_cache_miss_tokens': -1,
                               'completion_tokens_details': {'reasoning_tokens': '4'}}),
            self.output(usage={'completion_tokens_details': None})])
        self.assertEqual(status, 0)
        ranks = [e for e in events if e['event'] == 'ranking_finished']
        self.assertEqual(ranks[0]['usage']['prompt_cache_hit_tokens'], 0)
        self.assertIsNone(ranks[1]['usage']['prompt_cache_hit_tokens'])
        for rank in ranks:
            self.assertIsNone(rank['usage']['prompt_cache_miss_tokens'])
            self.assertIsNone(rank['usage']['completion_tokens_details']['reasoning_tokens'])
        self.assertEqual(events[-1]['prompt_cache_hit_tokens'], 0)
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                      'prompt_cache_miss_tokens', 'reasoning_tokens'):
            self.assertIsNone(events[-1][field])

    def test_invalid_outputs_redacted_bounded_and_usage_includes_failures(self):
        usage = dict(prompt_tokens=7, completion_tokens=3, total_tokens=10)
        status, events, stderr, _ = self.live([
            self.output('dummy-test-credential ' + 'x' * 3000, usage),
            self.output('{"recommend": true, "priority": "urgent", "reason": "x"}'),
            self.output()])
        self.assertEqual(status, 1)
        failed = [e for e in events if e['event'] == 'ranking_failed']
        self.assertEqual(len(failed), 2)
        self.assertEqual(failed[0]['error_type'], 'JSONDecodeError')
        self.assertLessEqual(len(failed[0]['raw_output_preview']), 2000)
        self.assertIn('raw_output_preview', failed[1])
        self.assertNotIn('dummy-test-credential', json.dumps(events) + stderr)
        self.assertNotIn('raw_output_preview', next(e for e in events if e['event'] == 'ranking_finished'))
        self.assertEqual(events[-1]['total_tokens'], 10)
        self.assertEqual(events[-1]['status'], 'partial')

    def test_source_failure_and_bad_items(self):
        status, events, _, _ = self.live([], pages=[response({'items': [{'id': None}]}),
            OSError('unavailable'), response({'items': []})])
        self.assertEqual(status, 1)
        pages = [e for e in events if e['event'] == 'source_page_finished']
        self.assertEqual(len(pages), 3)
        self.assertEqual((pages[0]['item_count'], pages[0]['bad_item_count'], pages[0]['success']), (1, 1, True))
        self.assertFalse(pages[1]['success'])
        self.assertEqual(pages[1]['error_type'], 'OSError')

    def test_offline_no_trace(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(main(['run']), 0)
        self.assertFalse(Path('data/traces').exists())
        self.assertNotIn('[rank', stderr.getvalue())

    def test_trace_open_failure(self):
        Path('data').mkdir()
        Path('data/traces').write_text('blocked', encoding='utf-8')
        status, _, stderr, _ = self.live([self.output()])
        self.assertEqual(status, 0)
        self.assertIn('Warning: trace file unavailable', stderr)

    def test_trace_write_and_flush_failure(self):
        for method in ('write', 'flush'):
            with self.subTest(method=method):
                stream = Mock()
                getattr(stream, method).side_effect = OSError('disk failed')
                original = LiveObserver.begin
                def begin(observer, *args):
                    original(observer, *args)
                    observer.stream.close()
                    observer.stream = stream
                with patch.object(LiveObserver, 'begin', begin):
                    status, _, stderr, _ = self.live([self.output()])
                self.assertEqual(status, 0)
                self.assertIn('Warning: trace event could not be written', stderr)

    def test_events_flushed_before_finish(self):
        from test_lifecycle import NOW
        observer = LiveObserver()
        observer.begin(9, NOW)
        self.addCleanup(observer.stream.close)
        observer.start({})
        observer.emit('source_page_finished', page=1)
        events = [json.loads(line) for line in observer.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([e['event'] for e in events], ['run_start', 'source_page_finished'])

    def test_provider_error_has_no_body_or_stale_metadata(self):
        from test_lifecycle import NOW, PREFS, notice
        ranker = LLMRanker('https://example.test/v1', '', 'model', opener=Mock(side_effect=[
            response(self.output('invalid', dict(prompt_tokens=3))),
            RuntimeError('sensitive provider body')]))
        with self.assertRaises(ValueError):
            ranker.rank(notice(), PREFS, NOW)
        with self.assertRaisesRegex(RuntimeError, r'^LLM provider request failed \(RuntimeError\)$'):
            ranker.rank(notice(), PREFS, NOW)
        self.assertEqual(ranker.last_observation, {'usage': None})

    def test_digest_failure_still_finishes_trace(self):
        from pku_radar.digest import TerminalWriter
        with patch.object(TerminalWriter, 'write', side_effect=OSError('output failed')):
            status, events, _, _ = self.live([self.output()])
        self.assertEqual(status, 1)
        self.assertEqual(events[-1]['event'], 'run_finished')
        self.assertEqual(events[-1]['status'], 'failed')
        self.assertEqual(events[-1]['ranking_succeeded'], 1)

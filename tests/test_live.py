import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from pku_radar.__main__ import main
from pku_radar.digest import DigestBuilder
from pku_radar.pipeline import run_pipeline
from pku_radar.ranking import FakeRanker, LLMRanker
from pku_radar.source import PKUKnowSource, SourceResult
from pku_radar.storage import Store
from test_lifecycle import NOW, PREFS, Source, Writer, notice


def response(payload, status=200):
    stream = io.BytesIO(json.dumps(payload).encode())
    stream.status = status
    return stream


def model_response(result):
    return response({"choices": [{"message": {"content": result}}]})


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("socket.socket", side_effect=AssertionError("Tests must be offline"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def fetch(self, pages):
        opener, sleep = Mock(side_effect=pages), Mock()
        result = PKUKnowSource(opener=opener, sleep=sleep).fetch()
        return result, opener, sleep

    def test_three_pages_parameters_and_mapping(self):
        raw = {"id": "one", "title": "AI", "source_id": "source", "source_name": "学校",
               "category": "招聘", "intent_group": "information", "ai_summary": "机会",
               "ai_is_event": False, "ai_event_time": None, "ai_event_location": "北京",
               "url": "https://example.test/notice", "published_at": "2025-01-01", "extra": {"x": 1}}
        result, opener, sleep = self.fetch([response({"items": [raw]}) for _ in range(3)])
        self.assertTrue(result.complete)
        self.assertEqual(result.successful_pages, 3)
        self.assertEqual(len(result.notices), 3)
        self.assertEqual(result.notices[0].summary, "机会")
        self.assertFalse(result.notices[0].upstream_is_event)
        self.assertEqual(result.notices[0].event_location, "北京")
        self.assertEqual(json.loads(result.notices[0].raw_json), raw)
        for page, call in enumerate(opener.call_args_list, 1):
            request = call.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("User-agent"), "PKURadar/0.2")
            self.assertEqual(call.kwargs["timeout"], 30)
            url = urlsplit(request.full_url)
            self.assertEqual((url.scheme, url.netloc, url.path), ("https", "pkuknow.cn", "/api/notices"))
            self.assertEqual(parse_qs(url.query, keep_blank_values=True),
                             {key: [value] for key, value in dict(q="", category="全部通知",
                              source="all", group="wechat,official", intent="all", view="list", page=str(page)).items()})
        self.assertEqual([call.args for call in sleep.call_args_list], [(3,), (3,)])

    def test_partial_page_and_all_failed(self):
        result, _, _ = self.fetch([response({"items": [{"id": "1"}]}),
                                   TimeoutError("timeout"), response({"items": [{"id": "3"}]})])
        self.assertFalse(result.complete)
        self.assertEqual([n.external_id for n in result.notices], ["1", "3"])
        self.assertEqual(result.successful_pages, 2)
        self.assertIn("page 2", result.errors[0])
        failed, opener, _ = self.fetch([OSError("unavailable") for _ in range(3)])
        self.assertEqual((failed.successful_pages, failed.notices, opener.call_count), (0, [], 3))
        self.assertEqual(len(failed.errors), 3)

    def test_response_validation_and_item_isolation(self):
        invalid_json = io.BytesIO(b"not json")
        invalid_json.status = 200
        for bad in [invalid_json, response([]), response({}), response({"items": {}}),
                    response({"items": []}, status=503)]:
            with self.subTest(bad=bad):
                result, _, _ = self.fetch([bad, response({"items": []}), response({"items": []})])
                self.assertFalse(result.complete)
                self.assertEqual(result.successful_pages, 2)
                self.assertIn("page 1", result.errors[0])
        result, _, _ = self.fetch([response({"items": [{"id": "good"}, None, {},
                    {"id": "bad", "title": []}, {"id": None}, {"id": "last"}]}),
                    response({"items": []}), response({"items": []})])
        self.assertEqual([n.external_id for n in result.notices], ["good", "last"])
        self.assertEqual(len(result.errors), 4)
        self.assertFalse(result.complete)

    def test_partial_status_footer_and_empty_page_distinction(self):
        for fetched, expected in [
            (SourceResult([notice()], ["Source page 2: timeout"], False, 2), "partial"),
            (SourceResult([], ["Source page 1/2/3: timeout"], False, 0), "failed"),
            (SourceResult([], ["Source page 2: timeout"], False, 2), "partial"),
            (SourceResult([], [], True, 3), "success"),
        ]:
            with self.subTest(expected=expected):
                store, writer = Store(":memory:"), Writer()
                try:
                    result = run_pipeline(store, Source(fetched), FakeRanker(), PREFS,
                                          clock=lambda: NOW, writer=writer, builder=DigestBuilder(live=True))
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(result["surfaced_count"], len(fetched.notices))
                    self.assertIn("[PKU RADAR LIVE]", writer.outputs[0])
                    self.assertEqual("Source fetch was incomplete" in writer.outputs[0], not fetched.complete)
                    if fetched.errors:
                        self.assertIn(fetched.errors[0], result["error"])
                finally:
                    store.close()

    def test_ranker_success_and_request(self):
        for recommend, priority in [(True, "high"), (False, None)]:
            expected = dict(recommend=recommend, priority=priority, reason="与你关注的方向相关。")
            opener = Mock(return_value=model_response(json.dumps(expected)))
            ranker = LLMRanker("https://example.test/v1/", "", "configured-model", opener=opener)
            self.assertEqual(ranker.rank(notice(), PREFS, NOW), expected)
            request = opener.call_args.args[0]
            self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
            self.assertEqual(request.get_method(), "POST")
            body = json.loads(request.data)
            self.assertEqual(body["model"], "configured-model")
            payload = json.loads(body["messages"][1]["content"])
            self.assertEqual(payload["preferences"], PREFS)
            self.assertEqual(payload["current_datetime"], NOW.isoformat())
            self.assertEqual(set(payload["notice"]), {"title", "source_name", "category", "intent_group",
                "summary", "upstream_is_event", "event_time_text", "event_location", "url"})

    def test_ranker_failures_retry_across_runs(self):
        for bad in [RuntimeError("provider exception"), model_response("not JSON"),
                    model_response('{"recommend": true, "priority": "urgent", "reason": "x"}'),
                    model_response('```json\n{}\n```')]:
            with self.subTest(bad=bad):
                opener = Mock(side_effect=[bad, model_response(
                    '{"recommend": true, "priority": "high", "reason": "相关机会"}')])
                ranker = LLMRanker("https://example.test/v1", "", "model", opener=opener)
                store = Store(":memory:")
                try:
                    first = run_pipeline(store, Source([notice()]), ranker, PREFS,
                                         clock=lambda: NOW, writer=Writer())
                    self.assertEqual(first["status"], "failed")
                    self.assertEqual(first["surfaced_count"], 0)
                    second = run_pipeline(store, Source([]), ranker, PREFS,
                                          clock=lambda: NOW, writer=Writer())
                    self.assertEqual((second["status"], second["surfaced_count"]), ("success", 1))
                    self.assertEqual(store.db.execute("SELECT rank_attempts FROM items").fetchone()[0], 2)
                finally:
                    store.close()

    def test_cli_modes_database_and_missing_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            previous = Path.cwd()
            os.chdir(directory)
            try:
                with redirect_stderr(io.StringIO()) as stderr, patch.object(PKUKnowSource, "fetch") as fetch:
                    self.assertEqual(main(["run", "--live"]), 1)
                    fetch.assert_not_called()
                    self.assertIn("LLM_BASE_URL", stderr.getvalue())
                self.assertTrue(Path("data/pku_radar_live.db").exists())
                with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["run", "--fake-day", "1"]), 0)
                    self.assertIn("[OFFLINE FIXTURE MODE]", stdout.getvalue())
                self.assertTrue(Path("data/pku_radar_offline.db").exists())
                with patch.object(LLMRanker, "from_env", return_value=FakeRanker()), \
                     patch.object(PKUKnowSource, "fetch", return_value=SourceResult([notice()], [], True, 3)), \
                     redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["run", "--live"]), 0)
                    self.assertIn("[PKU RADAR LIVE]", stdout.getvalue())
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()

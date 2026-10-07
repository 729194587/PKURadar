import hashlib
import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pku_radar.__main__ import main
from pku_radar.digest import DigestBuilder, sort_key
from pku_radar.models import Notice
from pku_radar.pipeline import run_pipeline
from pku_radar.ranking import FakeRanker, load_preferences
from pku_radar.source import FakeSource, ROOT
from pku_radar.storage import Store


NOW = datetime(2026, 10, 7, 8, tzinfo=timezone.utc)
PREFS = {"primary_interests": ["AI"], "secondary_interests": ["动漫"], "low_interest": ["广告"]}


def notice(identity="one", title="AI lecture"):
    return Notice.from_raw({"id": identity, "title": title, "published_at": None})


class Source:
    def __init__(self, items):
        self.items = items

    def fetch(self):
        return self.items


class Writer:
    def __init__(self, fail=False):
        self.outputs = []
        self.fail = fail

    def write(self, text):
        if self.fail:
            raise OSError("writer unavailable")
        self.outputs.append(text)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("network prohibited"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "offline.db")
        self.addCleanup(self.store.close)
        self.writer = Writer()

    def run_items(self, items=(), **kwargs):
        options = dict(writer=self.writer, clock=lambda: NOW)
        options.update(kwargs)
        ranker = options.pop("ranker", FakeRanker())
        preferences = options.pop("preferences", PREFS)
        return run_pipeline(self.store, Source(items), ranker, preferences, **options)

    def item(self, identity="one"):
        return self.store.db.execute("SELECT * FROM items WHERE external_id=?", (identity,)).fetchone()

    def test_fixture_mapping_and_day_two(self):
        path = ROOT / "tests/fixtures/pkuknow_notices.json"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "4a3722f751e3d37b31a35b4246e55c77251f1bfb1c9acaa63d4411ff6292b797")
        day1, day2 = FakeSource(1).fetch(), FakeSource(2).fetch()
        raw = json.loads(path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(day1[0].summary, raw["ai_summary"])
        self.assertEqual(day1[0].upstream_is_event, raw["ai_is_event"])
        self.assertEqual(json.loads(day1[0].raw_json), raw)
        prefs = load_preferences(ROOT / "config/preferences.yaml")
        first = self.run_items(day1, preferences=prefs)
        shown = {r[0] for r in self.store.db.execute("SELECT external_id FROM items WHERE surfaced_at IS NOT NULL")}
        ranker = FakeRanker()
        second = self.run_items(day2, ranker=ranker, preferences=prefs)
        self.assertEqual((first["status"], first["new_count"]), ("success", 30))
        self.assertGreater(first["surfaced_count"], 0)
        self.assertEqual((second["new_count"], second["ranked_count"]), (3, 3))
        self.assertTrue(all(key[1] not in shown for key, _ in ranker.calls))
        self.assertIsNotNone(self.item("offline:day2:late")["surfaced_at"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM items").fetchone()[0], 33)

    def test_day_two_synthetic_items_have_clean_semantics(self):
        day1, day2 = FakeSource(1).fetch(), FakeSource(2).fetch()
        self.assertEqual(day2[2:-1], day1[:24])
        preferences = load_preferences(ROOT / "config/preferences.yaml")
        expected = [
            ("offline:day2:ai", "AI Agent 技术讲座", "high", "2026-10-08T10:00:00+08:00"),
            ("offline:day2:club", "动漫社团活动", "medium", "2026-10-08T09:00:00+08:00"),
            ("offline:day2:late", "开源工程实践（迟到通知）", "high", "2025-01-01T09:00:00+08:00"),
        ]
        for item, (identity, title, priority, published_at) in zip(
                [day2[0], day2[1], day2[-1]], expected):
            with self.subTest(identity=identity):
                self.assertEqual((item.provider, item.external_id, item.title, item.published_at),
                                 ("pkuknow", identity, title, published_at))
                self.assertEqual(item.summary, title)
                for field in ("source_id", "source_name", "category", "intent_group"):
                    self.assertIsNone(getattr(item, field))
                    self.assertIsNone(json.loads(item.raw_json)[field])
                result = FakeRanker().rank(item, preferences, NOW)
                self.assertEqual((result["recommend"], result["priority"]), (True, priority))

    def test_upsert_preserves_lifecycle_and_provider_identity(self):
        self.run_items([notice()])
        before = dict(self.item())
        changed = Notice.from_raw({"id": "one", "title": "changed", "ai_summary": "new summary"})
        self.assertEqual(self.store.ingest([changed, changed], NOW + timedelta(days=1)), 0)
        after = dict(self.item())
        for key in ("first_seen_at", "rank_status", "recommend", "priority", "rank_attempts",
                    "ranked_at", "recommendation_reason", "surfaced_at"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["summary"], "new summary")
        self.assertNotEqual(before["raw_json"], after["raw_json"])
        self.assertNotEqual(before["last_seen_at"], after["last_seen_at"])
        self.assertEqual(self.store.ingest([replace(changed, provider="other")], NOW), 1)

    def test_retry_next_run_and_fixed_clock(self):
        ranker = FakeRanker(failures={("pkuknow", "one"): 1})
        first = self.run_items([notice()], ranker=ranker)
        self.assertEqual((first["status"], self.item()["rank_attempts"]), ("failed", 1))
        self.assertIn("1 items failed ranking", self.writer.outputs[-1])
        second = self.run_items(ranker=ranker)
        self.assertEqual(second["status"], "success")
        self.assertEqual(self.item()["rank_attempts"], 2)
        self.assertEqual(self.item()["surfaced_at"], NOW.isoformat())
        self.assertEqual(ranker.calls[-1][1], NOW)
        self.assertEqual(second["finished_at"], NOW.isoformat())

    def test_max_attempts_footer_and_explicit_rerank_outside_window(self):
        ranker = FakeRanker(failures={("pkuknow", "one"): -1})
        self.run_items([notice()], ranker=ranker)
        self.run_items(ranker=ranker)
        third = self.run_items(ranker=ranker)
        self.assertEqual(self.item()["rank_attempts"], 3)
        self.assertIn("1 items reached max_rank_attempts", self.writer.outputs[-1])
        self.assertIn("reached max_rank_attempts", third["error"])
        fourth = self.run_items(ranker=ranker)
        self.assertEqual(fourth["status"], "success")
        self.assertEqual(len(ranker.calls), 3)
        self.assertNotIn("failed ranking", self.writer.outputs[-1])
        self.assertNotIn("reached max_rank_attempts", self.writer.outputs[-1])
        rerun = self.run_items(rerank=True)
        self.assertEqual(rerun["ranked_count"], 1)
        self.assertEqual(self.item()["rank_attempts"], 1)
        self.assertIsNotNone(self.item()["surfaced_at"])

    def test_database_wide_rerank_excludes_surfaced(self):
        self.run_items([notice("shown"), notice("hidden", "unmatched")])
        ranker = FakeRanker()
        result = self.run_items(rerank=True, ranker=ranker,
                                preferences={"primary_interests": ["unmatched"]})
        self.assertEqual([key for key, _ in ranker.calls], [("pkuknow", "hidden")])
        self.assertEqual(result["surfaced_count"], 1)
        self.assertNotIn("AI lecture", self.writer.outputs[-1])

    def test_success_on_last_attempt_still_reports_limit(self):
        ranker = FakeRanker(failures={("pkuknow", "one"): 2})
        self.run_items([notice()], ranker=ranker)
        self.run_items(ranker=ranker)
        result = self.run_items(ranker=ranker)
        self.assertEqual(result["status"], "success")
        self.assertNotIn("failed ranking", self.writer.outputs[-1])
        self.assertIn("1 items reached max_rank_attempts", self.writer.outputs[-1])

    def test_failed_rerank_can_surface_preserved_recommendation(self):
        self.run_items([notice()], writer=Writer(fail=True))
        before = dict(self.item())
        result = self.run_items(rerank=True, ranker=FakeRanker(failures={("pkuknow", "one"): -1}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["surfaced_count"], 1)
        self.assertEqual(self.item()["recommendation_reason"], before["recommendation_reason"])
        self.assertEqual(self.item()["ranked_at"], before["ranked_at"])
        self.assertEqual(self.item()["rank_status"], "succeeded")
        self.assertIsNotNone(self.item()["surfaced_at"])

    def test_rerank_failure_preserves_success(self):
        self.run_items([notice(title="unmatched")])
        before = dict(self.item())
        result = self.run_items(rerank=True, ranker=FakeRanker(failures={("pkuknow", "one"): -1}))
        for key in ("rank_status", "recommend", "priority", "recommendation_reason", "ranked_at"):
            self.assertEqual(self.item()[key], before[key])
        self.assertEqual(result["status"], "failed")
        self.assertIn("scripted ranking failure", self.item()["last_rank_error"])
        self.assertIn("1 items failed ranking", self.writer.outputs[-1])

    def test_writer_failure_then_recovery_without_rerank(self):
        result = self.run_items([notice()], writer=Writer(fail=True))
        self.assertEqual((result["status"], result["surfaced_count"]), ("failed", 0))
        self.assertIsNone(self.item()["surfaced_at"])
        result = self.run_items()
        self.assertEqual((result["ranked_count"], result["surfaced_count"]), (0, 1))
        self.assertIsNotNone(self.item()["surfaced_at"])

    def test_zero_recommendations_still_writes_and_can_fail(self):
        first = self.run_items([notice(title="unmatched")])
        self.assertEqual(first["status"], "success")
        self.assertIn("No relevant new notices today.", self.writer.outputs[-1])
        second = self.run_items(writer=Writer(fail=True))
        self.assertEqual(second["status"], "failed")
        self.assertIsNotNone(second["finished_at"])

    def test_partial_and_invalid_outputs(self):
        invalid = [{}, {"recommend": 1}, {"recommend": True, "priority": None, "reason": "x"},
                   {"recommend": True, "priority": "urgent", "reason": "x"},
                   {"recommend": False, "priority": "high", "reason": "x"},
                   {"recommend": False, "reason": " "}, {"recommend": False, "reason": 7}]
        for index, output in enumerate(invalid):
            with self.subTest(output=output):
                identity = f"invalid{index}"
                result = self.run_items([notice(identity), notice(f"good{index}")],
                    ranker=FakeRanker(invalid_outputs={("pkuknow", identity): output}), max_rank_attempts=1)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(self.item(identity)["rank_status"], "failed")

    def test_fake_ranker_matching(self):
        for title, expected in [("ai", (True, "high")), ("动漫", (True, "medium")),
                                ("广告", (False, None)), ("other", (False, None)),
                                ("AI 动漫 广告", (True, "high"))]:
            result = FakeRanker().rank(notice(title=title), PREFS, NOW)
            self.assertEqual((result["recommend"], result["priority"]), expected)

    def test_digest_order_and_fallback(self):
        self.run_items([notice("b"), notice("a"), Notice.from_raw(
            {"id": "dated", "title": "AI", "published_at": "2020-01-01T00:00:00+08:00"})], writer=Writer(fail=True))
        rows = self.store.recommendations()
        self.assertEqual([r["external_id"] for r in sorted(rows, key=sort_key)], ["dated", "a", "b"])
        digest = DigestBuilder().build(rows, dict(fetched=3, new=3, ranked=3, recommended=3), NOW)
        self.assertIn("[OFFLINE FIXTURE MODE]", digest)

    def test_source_builder_and_cli_unexpected_failures(self):
        with patch.object(Source, "fetch", side_effect=RuntimeError("source failed")):
            result = self.run_items()
        self.assertEqual(result["status"], "failed")
        self.assertIn("source failed", result["error"])
        with patch.object(DigestBuilder, "build", side_effect=RuntimeError("build failed")):
            result = self.run_items([notice()])
        self.assertIsNone(self.item()["surfaced_at"])
        self.assertEqual(result["status"], "failed")
        path = Path(self.temp.name) / "cli.db"
        with patch.object(FakeSource, "fetch", side_effect=RuntimeError("unexpected")), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", "--db", str(path)]), 1)
        other = Store(path)
        try:
            run = other.run(1)
            self.assertEqual(run["status"], "failed")
            self.assertIn("unexpected", run["error"])
            self.assertIsNotNone(run["finished_at"])
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()

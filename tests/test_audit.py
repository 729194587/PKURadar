import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pku_radar.__main__ import main
from pku_radar.models import Notice, Recommendation
from pku_radar.storage import Store
from test_lifecycle import NOW


class AuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "audit.db"
        self.reason = "完整理由，不能截断。" * 100 + "\n第二行理由。"
        store = Store(self.path)
        try:
            for identity, priority, date in [
                ("low", "low", "2026-10-07"), ("medium", "medium", None),
                ("high-old", "high", "2026-10-06T00:00:00Z"),
                ("high-new", "high", "2026-10-07T00:00:00+08:00"),
                ("no-b", None, "invalid"), ("no-a", None, None),
                ("no-new", None, "2026-10-07"),
                ("pending", None, None), ("failed", None, None)]:
                store.ingest([Notice.from_raw(dict(id=identity, title=identity, published_at=date,
                    source_name="学校", category="讲座", ai_event_time="明天", ai_event_location="北京",
                    url="https://example.test/notice"))], NOW)
                row = store.db.execute("SELECT * FROM items WHERE external_id=?", (identity,)).fetchone()
                if identity == "failed":
                    store.record_rank(row, NOW, error="offline failure")
                elif identity != "pending":
                    store.record_rank(row, NOW, Recommendation(priority is not None, priority, self.reason))
            with store.db:
                store.db.execute("UPDATE items SET surfaced_at=? WHERE external_id='high-new'", (NOW.isoformat(),))
            store.start_run(NOW)
        finally:
            store.close()
        for target in ("socket.socket", "pku_radar.__main__.FakeRanker", "pku_radar.__main__.LLMRanker",
                       "pku_radar.__main__.run_pipeline", "pku_radar.__main__.Store"):
            guard = patch(target, side_effect=AssertionError("Audit must only read stored data"))
            guard.start()
            self.addCleanup(guard.stop)

    def invoke(self, *args):
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            status = main(["audit", "--db", str(self.path), *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_groups_counts_dates_full_fields_and_read_only(self):
        before = self.path.read_bytes()
        status, report, error = self.invoke()
        self.assertEqual((status, error), (0, ""))
        for line in ("Total ranked: 7", "Recommended: 4", "  High: 2", "  Medium: 1", "  Low: 1",
                     "Not recommended: 3", "Pending ranking: 1", "Failed ranking: 1"):
            self.assertIn(line, report.splitlines())
        markers = [line for line in report.splitlines() if line.startswith("[")]
        self.assertEqual(markers, ["[HIGH] high-new", "[HIGH] high-old", "[MEDIUM] medium", "[LOW] low",
                                   "[NO] no-new", "[NO] no-a", "[NO] no-b"])
        self.assertLess(report.index("## Recommended"), report.index("## Not Recommended"))
        for value in ("External ID: high-new", "Source: 学校", "Category: 讲座", "Recommend: true",
                      "Recommend: false", "Priority: high", "Event time: 明天", "Event location: 北京",
                      "URL: https://example.test/notice", "Reason: " + self.reason):
            self.assertIn(value, report)
        # Byte equality covers every ranking/surfaced field, raw data, runs and schema.
        self.assertEqual(self.path.read_bytes(), before)

    def test_output_contains_full_report_terminal_only_summary_and_path(self):
        before = self.path.read_bytes()
        destination = self.path.with_suffix(".md")
        _, expected, _ = self.invoke()
        status, terminal, error = self.invoke("--output", str(destination))
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(destination.read_text(encoding="utf-8"), expected.rstrip("\n") + "\n")
        self.assertIn("Total ranked: 7", terminal)
        self.assertIn(str(destination), terminal)
        self.assertNotIn("[HIGH]", terminal)
        self.assertNotIn(self.reason, terminal)
        self.assertEqual(self.path.read_bytes(), before)

    def test_refuses_database_output_and_does_not_create_missing_database(self):
        before = self.path.read_bytes()
        status, _, error = self.invoke("--output", str(self.path))
        self.assertEqual(status, 1)
        self.assertIn("must not overwrite", error)
        self.assertEqual(self.path.read_bytes(), before)
        self.path = self.path.with_name("missing.db")
        self.assertEqual(self.invoke()[0], 1)
        self.assertFalse(self.path.exists())

    def test_default_live_path_and_empty_database(self):
        previous = Path.cwd()
        try:
            os.chdir(self.path.parent)
            store = Store("data/pku_radar_live.db")
            store.close()
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["audit"]), 0)
            self.assertIn("Total ranked: 0", stdout.getvalue())
            self.assertIn("Not recommended: 0", stdout.getvalue())
        finally:
            os.chdir(previous)

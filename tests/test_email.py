import io
import json
import os
import smtplib
import tempfile
import traceback
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from pku_radar.__main__ import main
from pku_radar.email_writer import EmailDeliveryError, EmailWriter
from pku_radar.observability import LiveObserver
from pku_radar.pipeline import run_pipeline
from pku_radar.ranking import FakeRanker, LLMRanker
from pku_radar.source import PKUKnowSource
from pku_radar.storage import Store
from test_lifecycle import NOW, PREFS, Source, Writer, notice


ENV = dict(SMTP_HOST="smtp.example.test", SMTP_PORT="587", SMTP_USER="sender@example.test",
           SMTP_PASSWORD="private-password", RADAR_EMAIL_TO="reader@example.test")


class EmailTests(unittest.TestCase):
    def setUp(self):
        for patcher in (patch.dict(os.environ, ENV, clear=True),
                        patch("socket.socket", side_effect=AssertionError("network prohibited"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.smtp_patch = patch("pku_radar.email_writer.smtplib.SMTP")
        self.smtp = self.smtp_patch.start()
        self.addCleanup(self.smtp_patch.stop)
        self.connection = self.smtp.return_value.__enter__.return_value
        self.connection.send_message.return_value = {}
        self.writer = EmailWriter()
        self.writer.configure()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def run_items(self, items=(), **kwargs):
        return run_pipeline(self.store, Source(items), FakeRanker(), PREFS,
                            clock=lambda: NOW, writer=Writer(), email_writer=self.writer, **kwargs)

    def test_message_subject_body_utf8_and_sender_fallback(self):
        digest = "北京大学\n人工智能讲座值得关注。"
        self.writer.write(digest, now=NOW + timedelta(hours=9), recommendation_count=2)
        message = self.connection.send_message.call_args.args[0]
        decoded = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        self.assertEqual(str(decoded["Subject"]), "PKU Radar · 2026-10-08 · 2 条值得关注")
        self.assertEqual(decoded.get_content().rstrip("\n"), digest)
        self.assertEqual(decoded["From"], ENV["SMTP_USER"])
        self.assertEqual(decoded["To"], ENV["RADAR_EMAIL_TO"])
        self.assertEqual(decoded.get_content_charset(), "utf-8")
        self.assertEqual(decoded.get_content_type(), "text/plain")
        self.assertFalse(decoded.is_multipart())
        self.connection.starttls.assert_called_once()
        self.connection.login.assert_called_once_with(ENV["SMTP_USER"], ENV["SMTP_PASSWORD"])

    def test_explicit_sender_and_implicit_tls(self):
        with patch.dict(os.environ, {"SMTP_PORT": "465", "RADAR_EMAIL_FROM": "radar@example.test"}), \
             patch("pku_radar.email_writer.smtplib.SMTP_SSL") as smtp_ssl:
            smtp_ssl.return_value.__enter__.return_value.send_message.return_value = {}
            self.writer.configure()
            self.writer.write("正文", now=NOW, recommendation_count=1)
            smtp_ssl.assert_called_once()
            self.assertTrue(smtp_ssl.call_args.kwargs["context"].check_hostname)
            connection = smtp_ssl.return_value.__enter__.return_value
            connection.starttls.assert_not_called()
            self.assertEqual(connection.send_message.call_args.args[0]["From"], "radar@example.test")
        self.smtp.assert_not_called()

    def test_missing_configuration(self):
        for key in ENV:
            with self.subTest(key=key), patch.dict(os.environ, {key: ""}):
                with self.assertRaisesRegex(ValueError, key):
                    self.writer.configure()
        self.smtp.assert_not_called()

    def test_invalid_configuration_is_sanitized(self):
        for value in ("private-password", "0", "65536", "1.5"):
            with patch.dict(os.environ, {"SMTP_PORT": value}):
                with self.assertRaisesRegex(ValueError, "SMTP_PORT must") as caught:
                    self.writer.configure()
                self.assertNotIn(ENV["SMTP_PASSWORD"], str(caught.exception))
        with patch.dict(os.environ, {"RADAR_EMAIL_TO": "x\nBcc: hidden@example.test"}):
            with self.assertRaisesRegex(ValueError, "RADAR_EMAIL_TO contains a newline"):
                self.writer.configure()

    def test_zero_recommendations_skips_smtp(self):
        result = self.run_items([notice(title="unmatched")])
        self.assertEqual((result["status"], result["surfaced_count"]), ("success", 0))
        self.writer.write("empty", now=NOW, recommendation_count=0)
        self.smtp.assert_not_called()

    def test_success_surfaces_after_send(self):
        def send(message):
            self.assertIsNone(self.store.db.execute("SELECT surfaced_at FROM items").fetchone()[0])
            self.assertIn("AI lecture", message.get_content())
            return {}
        self.connection.send_message.side_effect = send
        result = self.run_items([notice()])
        self.assertEqual((result["status"], result["surfaced_count"]), ("success", 1))
        self.assertIsNotNone(self.store.db.execute("SELECT surfaced_at FROM items").fetchone()[0])

    def test_failure_and_retry_without_reranking(self):
        self.connection.send_message.side_effect = RuntimeError(ENV["SMTP_PASSWORD"])
        result = self.run_items([notice()])
        self.assertEqual((result["status"], result["surfaced_count"]), ("failed", 0))
        self.assertNotIn(ENV["SMTP_PASSWORD"], result["error"])
        self.assertIn("Email delivery failed", result["error"])
        self.assertIsNone(self.store.db.execute("SELECT surfaced_at FROM items").fetchone()[0])
        self.connection.send_message.side_effect = None
        result = self.run_items()
        self.assertEqual((result["status"], result["ranked_count"], result["surfaced_count"]), ("success", 0, 1))
        self.assertIn("1 条值得关注", str(self.connection.send_message.call_args.args[0]["Subject"]))

    def test_auth_failure_traceback_does_not_leak_credentials(self):
        self.connection.login.side_effect = smtplib.SMTPAuthenticationError(535, ENV["SMTP_PASSWORD"].encode())
        try:
            self.writer.write("正文", now=NOW, recommendation_count=1)
        except EmailDeliveryError:
            rendered = traceback.format_exc()
        else:
            self.fail("Expected delivery failure")
        self.assertNotIn(ENV["SMTP_PASSWORD"], rendered)
        self.assertIn("authentication rejected", rendered)

    def test_partial_recipient_refusal_is_failure(self):
        self.connection.send_message.return_value = {"recipient": (550, ENV["SMTP_PASSWORD"])}
        self.assertEqual(self.run_items([notice()])["surfaced_count"], 0)

    def test_delivery_events(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()):
            self.connection.send_message.side_effect = RuntimeError(ENV["SMTP_PASSWORD"])
            self.run_items([notice()], observer=LiveObserver(directory))
            self.connection.send_message.side_effect = None
            self.run_items(observer=LiveObserver(directory))
            self.run_items(observer=LiveObserver(directory))
            content = "".join(path.read_text(encoding="utf-8") for path in Path(directory).glob("*.jsonl"))
        self.assertNotIn(ENV["SMTP_PASSWORD"], content)
        events = [json.loads(line) for line in content.splitlines()]
        delivery = [event for event in events if event["event"].startswith("delivery_")]
        self.assertCountEqual([event["event"] for event in delivery], ["delivery_failed", "delivery_finished"])
        for event in delivery:
            self.assertEqual((event["channel"], event["recommendation_count"]), ("email", 1))
            self.assertGreaterEqual(event["duration_ms"], 0)

    def cli(self, directory, flags):
        with patch.object(LLMRanker, "from_env", return_value=FakeRanker()), \
             patch.object(PKUKnowSource, "fetch", return_value=[notice()]) as fetch, \
             patch("pku_radar.__main__.LiveObserver", side_effect=lambda **kw: LiveObserver(Path(directory) / "traces", **kw)), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = main(["run", "--db", str(Path(directory) / "run.db"), *flags])
        return code, out.getvalue(), err.getvalue(), fetch

    def test_cli_email_and_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            code, out, _, _ = self.cli(directory, ["--live", "--email"])
        self.assertEqual(code, 0)
        self.assertIn("[PKU RADAR LIVE]", out)
        self.connection.send_message.assert_called_once()
        self.assertEqual(self.connection.send_message.call_args.args[0].get_content().rstrip(), out.rstrip())

    def test_cli_without_email_needs_no_smtp_config(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            code, out, _, _ = self.cli(directory, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("[PKU RADAR LIVE]", out)
        self.smtp.assert_not_called()

    def test_cli_missing_email_config_persists_failed_run_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            code, _, error, fetch = self.cli(directory, ["--live", "--email"])
            store = Store(Path(directory) / "run.db")
            try:
                self.assertEqual(store.run(1)["status"], "failed")
            finally:
                store.close()
        self.assertEqual(code, 1)
        self.assertIn("SMTP_PASSWORD", error)
        fetch.assert_not_called()
        self.smtp.assert_not_called()

    def test_offline_email_is_rejected(self):
        with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            main(["run", "--email"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--email requires --live", err.getvalue())
        self.smtp.assert_not_called()

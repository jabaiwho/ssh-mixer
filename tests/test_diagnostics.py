from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ssh_mixer.diagnostics import DiagnosticStore, github_issue_url


class DiagnosticStoreTest(unittest.TestCase):
    def test_report_and_log_redact_sensitive_machine_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DiagnosticStore(Path(temp))
            store.record(
                stage="connection.preflight",
                code="ssh-failed",
                message=(
                    "listener@receiver.example 100.101.102.103 "
                    "/home/listener/.ssh/id_ed25519 Headset Browser"
                ),
                session_id="session-a",
                sensitive_values=["listener", "receiver.example", "Headset", "Browser"],
            )

            preview = store.preview_report()
            included = store.preview_report(include_logs=True)
            persisted = "".join(path.read_text() for path in Path(temp).glob("*.jsonl"))

        for secret in (
            "listener",
            "receiver.example",
            "100.101.102.103",
            "id_ed25519",
            "Headset",
            "Browser",
        ):
            self.assertNotIn(secret, preview["body"])
            self.assertNotIn(secret, included["body"])
            self.assertNotIn(secret, persisted)
        self.assertNotIn("events", preview)
        self.assertIn("events", included)
        self.assertIn("Redacted operational events", included["body"])
        self.assertEqual(preview["diagnostic"]["stage"], "connection.preflight")

    def test_retention_keeps_at_most_twenty_sessions_and_seven_days(self) -> None:
        now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DiagnosticStore(root, now=lambda: now[0])
            store.record(stage="session.start", code="test", message="old", session_id="old")
            now[0] += timedelta(days=8)
            for index in range(24):
                store.record(
                    stage="session.start",
                    code="test",
                    message=f"event {index}",
                    session_id=f"new-{index:02d}",
                )

            logs = sorted(root.glob("*.jsonl"))
            log_names = [path.name for path in logs]
            total_size = sum(path.stat().st_size for path in logs)

        self.assertLessEqual(len(log_names), 20)
        self.assertFalse(any(name.startswith("old.") for name in log_names))
        self.assertLessEqual(total_size, 512 * 1024)

    def test_user_can_choose_shorter_or_longer_bounded_retention(self) -> None:
        now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DiagnosticStore(root, now=lambda: now[0])

            minimal = store.configure_retention("minimal")
            store.enable_verbose_next_session()
            store.record(stage="session.start", code="test", message="old", session_id="old")
            now[0] += timedelta(days=2)
            for index in range(8):
                store.record(
                    stage="session.start",
                    code="test",
                    message=f"event {index}",
                    session_id=f"new-{index}",
                )
            minimal_logs = list(root.glob("*.jsonl"))
            verbose_enabled = store.consume_verbose_for_session()

            extended = store.configure_retention("extended")
            settings_mode = root.joinpath(".settings.json").stat().st_mode & 0o777

        self.assertEqual(minimal["policy"], "minimal")
        self.assertEqual(minimal["maximumAgeDays"], 1)
        self.assertEqual(minimal["maximumSessions"], 5)
        self.assertLessEqual(len(minimal_logs), 5)
        self.assertFalse(any(path.name.startswith("old.") for path in minimal_logs))
        self.assertTrue(verbose_enabled)
        self.assertEqual(extended["policy"], "extended")
        self.assertEqual(extended["maximumAgeDays"], 30)
        self.assertEqual(extended["maximumSessions"], 50)
        self.assertEqual(settings_mode, 0o600)

    def test_verbose_mode_expires_after_one_session_and_clear_removes_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DiagnosticStore(Path(temp))
            store.enable_verbose_next_session()

            self.assertTrue(store.consume_verbose_for_session())
            self.assertFalse(store.consume_verbose_for_session())

            store.record(stage="session.start", code="test", message="event", session_id="one")
            self.assertTrue(list(Path(temp).glob("*.jsonl")))
            store.clear()
            self.assertFalse(list(Path(temp).glob("*.jsonl")))

    def test_invalid_retention_choice_is_rejected_without_changing_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DiagnosticStore(Path(temp))

            with self.assertRaisesRegex(ValueError, "retention policy"):
                store.configure_retention("forever")

            self.assertEqual(store.retention_settings()["policy"], "standard")

    def test_github_url_uses_the_user_reviewed_body(self) -> None:
        body = "Reviewed diagnostic body"

        url = github_issue_url(body, title="Receiver setup failed")

        self.assertIn("github.com/jabaiwho/ssh-mixer/issues/new", url)
        self.assertIn("Reviewed+diagnostic+body", url)
        self.assertIn("Receiver+setup+failed", url)


if __name__ == "__main__":
    unittest.main()

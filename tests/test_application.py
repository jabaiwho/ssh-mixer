from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.config import (
    config_path,
    keys_dir,
    logs_dir,
    state_dir,
    trust_dir,
    xdg_runtime_dir,
)
from ssh_mixer.diagnostics import DiagnosticStore


class ApplicationTest(unittest.TestCase):
    def test_inspect_returns_generic_versioned_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ):
            app = MixerApplication(
                discover_sources=lambda: [
                    {
                        "id": "sink-input:42",
                        "type": "playback",
                        "label": "Example player",
                        "defaultSelected": True,
                    }
                ],
                read_status=lambda: {"state": "stopped", "active": False},
                discover_tailscale_peers=lambda: [],
                discover_profiles=lambda: [],
            )

            result = app.execute({"operation": "inspect"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["config"]["schemaVersion"], 2)
        self.assertEqual(result["config"]["sourceIds"], [])
        self.assertEqual(result["config"]["sourceMatchers"], [])
        self.assertEqual(result["config"]["mixProfiles"], [])
        self.assertEqual(result["config"]["remote"]["host"], "")
        self.assertEqual(result["config"]["remote"]["user"], "")
        self.assertEqual(result["config"]["remote"]["keyPath"], "")

    def test_configure_protects_application_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ):
            app = MixerApplication()

            result = app.execute(
                {
                    "operation": "configure",
                    "payload": {
                        "destination": "ssh",
                        "remote": {"host": "receiver.example", "user": "listener"},
                    },
                }
            )

            self.assertTrue(result["ok"])
            self.assertEqual(config_path().stat().st_mode & 0o777, 0o600)
            for directory in (
                state_dir(),
                logs_dir(),
                xdg_runtime_dir(),
                trust_dir(),
                keys_dir(),
            ):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700, str(directory))

    def test_diagnostic_report_requires_a_user_reviewed_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DiagnosticStore(Path(temp) / "logs")
            app = MixerApplication(diagnostic_store=store)
            failure = app.execute({"operation": "not-supported"})

            preview = app.execute({"operation": "diagnostics.preview"})
            reviewed_body = preview["report"]["body"] + "\n\nUser note: reproduced once."
            report = app.execute(
                {
                    "operation": "diagnostics.report-url",
                    "payload": {"body": reviewed_body},
                }
            )

        self.assertFalse(failure["ok"])
        self.assertEqual(failure["diagnostic"]["stage"], "application.not-supported")
        self.assertTrue(preview["ok"])
        self.assertFalse(preview["report"]["logsIncluded"])
        self.assertNotIn("events", preview["report"])
        self.assertTrue(report["ok"])
        self.assertIn("User+note", report["url"])

    def test_user_can_configure_diagnostic_retention_and_open_fix_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DiagnosticStore(Path(temp) / "logs")
            app = MixerApplication(diagnostic_store=store)

            configured = app.execute(
                {
                    "operation": "diagnostics.configure",
                    "payload": {"retentionPolicy": "minimal"},
                }
            )
            contribution = app.execute({"operation": "diagnostics.contribute-url"})

        self.assertTrue(configured["ok"])
        self.assertEqual(configured["settings"]["policy"], "minimal")
        self.assertEqual(configured["settings"]["maximumAgeDays"], 1)
        self.assertEqual(configured["settings"]["maximumSessions"], 5)
        self.assertTrue(contribution["ok"])
        self.assertEqual(
            contribution["url"],
            "https://github.com/jabaiwho/ssh-mixer/blob/main/CONTRIBUTING.md",
        )

    def test_configure_refuses_a_symlinked_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        ):
            app = MixerApplication(discover_sources=lambda: [])
            self.assertTrue(app.execute({"operation": "inspect"})["ok"])
            victim = Path(temp) / "victim"
            victim.write_text("do not replace", encoding="utf-8")
            config_path().symlink_to(victim)

            result = app.execute({"operation": "configure", "payload": {}})

            self.assertFalse(result["ok"])
            self.assertEqual(victim.read_text(encoding="utf-8"), "do not replace")


if __name__ == "__main__":
    unittest.main()

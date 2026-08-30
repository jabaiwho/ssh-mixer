from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.config import config_path
from ssh_mixer.diagnostics import DiagnosticStore
from tests.test_source_matchers import SOURCES


CONNECTION = {
    "type": "direct",
    "host": "receiver.example",
    "user": "listener",
    "port": 22,
}


class MixProfileApplicationTest(unittest.TestCase):
    def environment(self, temp: str):
        return patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        )

    def save_profile(
        self,
        app: MixerApplication,
        *,
        name: str = "Desk playback",
        source_ids: list[str] | None = None,
        quick_start: bool = True,
    ) -> dict:
        return app.execute(
            {
                "operation": "mix-profile.save",
                "payload": {
                    "name": name,
                    "connection": CONNECTION,
                    "routeMode": "both",
                    "sourceIds": source_ids or ["sink-input:101"],
                    "privacy": {
                        "lockBehavior": "stop-all",
                        "showReceiverLabel": False,
                    },
                    "stream": {
                        "bitrate": "160k",
                        "connectTimeoutSeconds": 7,
                    },
                    "quickStartEnabled": quick_start,
                },
            }
        )

    def test_profile_retains_connection_route_matchers_privacy_and_stream_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            app = MixerApplication(
                discover_sources=lambda: SOURCES,
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            saved = self.save_profile(app)
            inspected = app.execute({"operation": "inspect"})
            persisted = config_path().read_text(encoding="utf-8")

        self.assertTrue(saved["ok"])
        profile = inspected["config"]["mixProfiles"][0]
        self.assertEqual(profile["connection"]["host"], "receiver.example")
        self.assertEqual(profile["routeMode"], "both")
        self.assertEqual(profile["sourceMatchers"][0]["kind"], "playback")
        self.assertEqual(profile["privacy"]["lockBehavior"], "stop-all")
        self.assertEqual(profile["stream"]["bitrate"], "160k")
        self.assertTrue(profile["quickStartEnabled"])
        self.assertNotIn("sink-input:101", persisted)
        self.assertNotIn('"sourceIds"', persisted)

    def test_explicit_playback_only_quick_start_starts_the_uniquely_resolved_profile(self) -> None:
        starts: list[dict] = []
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            app = MixerApplication(
                discover_sources=lambda: SOURCES,
                start_session=lambda config: starts.append(config)
                or {"state": "starting", "active": True},
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            saved = self.save_profile(app)
            result = app.execute(
                {
                    "operation": "mix-profile.quick-start",
                    "payload": {
                        "profileId": saved["profile"]["id"],
                        "quickStartConfirmed": True,
                    },
                }
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])
        self.assertEqual(starts[0]["sourceIds"], ["sink-input:101"])
        self.assertEqual(starts[0]["destination"], "both")
        self.assertEqual(starts[0]["remote"]["bitrate"], "160k")

    def test_loading_a_profile_opens_the_mixer_without_starting(self) -> None:
        starts: list[dict] = []
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            app = MixerApplication(
                discover_sources=lambda: SOURCES,
                start_session=lambda config: starts.append(config) or {},
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            saved = self.save_profile(app)
            loaded = app.execute(
                {
                    "operation": "mix-profile.load",
                    "payload": {"profileId": saved["profile"]["id"]},
                }
            )

        self.assertTrue(loaded["ok"])
        self.assertTrue(loaded["openMixer"])
        self.assertEqual(loaded["config"]["sourceIds"], ["sink-input:101"])
        self.assertEqual(starts, [])

    def test_capture_profile_opens_mixer_with_recent_capture_and_never_starts(self) -> None:
        starts: list[dict] = []
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            app = MixerApplication(
                discover_sources=lambda: SOURCES,
                start_session=lambda config: starts.append(config) or {},
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            saved = self.save_profile(
                app,
                name="Meeting",
                source_ids=["sink-input:101", "source:alsa_input.usb-mic"],
            )
            result = app.execute(
                {
                    "operation": "mix-profile.quick-start",
                    "payload": {
                        "profileId": saved["profile"]["id"],
                        "quickStartConfirmed": True,
                    },
                }
            )

        self.assertTrue(saved["profile"]["requiresCaptureConfirmation"])
        self.assertFalse(saved["profile"]["quickStartEnabled"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["openMixer"])
        self.assertEqual(result["reason"], "capture-confirmation-required")
        self.assertEqual(result["recentCaptureIds"], ["source:alsa_input.usb-mic"])
        self.assertEqual(starts, [])

    def test_quick_start_arms_missing_playback_and_groups_multiple_streams(self) -> None:
        current_sources = list(SOURCES)
        starts: list[dict] = []
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            app = MixerApplication(
                discover_sources=lambda: current_sources,
                start_session=lambda config: starts.append(config) or {},
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            saved = self.save_profile(app)
            profile_id = saved["profile"]["id"]

            current_sources.clear()
            missing = app.execute(
                {
                    "operation": "mix-profile.quick-start",
                    "payload": {
                        "profileId": profile_id,
                        "quickStartConfirmed": True,
                    },
                }
            )
            current_sources.extend(SOURCES)
            current_sources.append(dict(SOURCES[0], id="sink-input:999", pulseId="999"))
            ambiguous = app.execute(
                {
                    "operation": "mix-profile.quick-start",
                    "payload": {
                        "profileId": profile_id,
                        "quickStartConfirmed": True,
                    },
                }
            )

        self.assertTrue(missing["ok"])
        self.assertTrue(missing["started"])
        self.assertTrue(ambiguous["ok"])
        self.assertTrue(ambiguous["started"])
        self.assertEqual(starts[0]["sourceIds"], [])
        self.assertEqual(
            starts[1]["sourceIds"], ["sink-input:101", "sink-input:999"]
        )

    def test_quick_start_requires_an_explicit_confirmed_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            app = MixerApplication(
                discover_sources=lambda: SOURCES,
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            saved = self.save_profile(app)
            result = app.execute(
                {
                    "operation": "mix-profile.quick-start",
                    "payload": {"profileId": saved["profile"]["id"]},
                }
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["diagnostic"]["code"], "confirmation-required")


if __name__ == "__main__":
    unittest.main()

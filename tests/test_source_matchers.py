from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.audio import (
    matcher_for_source,
    normalize_source_matcher,
    resolve_source_matchers,
)
from ssh_mixer.config import config_path


SOURCES = [
    {
        "id": "sink-input:101",
        "type": "playback",
        "pulseId": "101",
        "name": "player.node",
        "applicationName": "Example Player",
        "processBinary": "example-player",
        "mediaName": "Playback",
        "label": "Example Player",
    },
    {
        "id": "source:alsa_input.usb-mic",
        "type": "capture",
        "pulseId": "22",
        "name": "alsa_input.usb-mic",
        "label": "USB Microphone",
    },
    {
        "id": "source:alsa_output.headphones.monitor",
        "type": "monitor",
        "pulseId": "23",
        "name": "alsa_output.headphones.monitor",
        "label": "Headphones monitor",
    },
]


class SourceMatcherTest(unittest.TestCase):
    def test_matcher_contains_stable_metadata_but_no_temporary_ids(self) -> None:
        matcher = matcher_for_source(SOURCES[0])

        serialized = json.dumps(matcher, sort_keys=True)
        self.assertEqual(matcher["kind"], "playback")
        self.assertEqual(matcher["processBinary"], "example-player")
        self.assertNotIn("101", serialized)
        self.assertNotIn("pulse", serialized.lower())
        self.assertNotIn("sink-input", serialized)

    def test_untrusted_matcher_drops_temporary_id_fields_and_rejects_controls(self) -> None:
        matcher = normalize_source_matcher(
            {
                "kind": "playback",
                "applicationName": "Example Player",
                "id": "sink-input:101",
                "pulseId": "101",
            }
        )

        self.assertNotIn("id", matcher)
        self.assertNotIn("pulseId", matcher)
        with self.assertRaises(ValueError):
            normalize_source_matcher(
                {"kind": "playback", "applicationName": "Player\u202eexe"}
            )

    def test_unique_playback_and_monitor_matches_restore_but_capture_is_recent_only(self) -> None:
        result = resolve_source_matchers(
            SOURCES,
            [matcher_for_source(source) for source in SOURCES],
        )

        self.assertEqual(
            result["selectedIds"],
            ["sink-input:101", "source:alsa_output.headphones.monitor"],
        )
        self.assertEqual(result["recentCaptureIds"], ["source:alsa_input.usb-mic"])
        self.assertTrue(result["hasCaptureMatchers"])

    def test_reused_temporary_id_with_different_metadata_is_not_restored(self) -> None:
        reused = dict(
            SOURCES[0],
            applicationName="Different Application",
            processBinary="different-app",
            name="different.node",
        )
        result = resolve_source_matchers(
            [reused],
            [matcher_for_source(SOURCES[0])],
        )

        self.assertEqual(result["selectedIds"], [])
        self.assertEqual(result["missingMatchers"], [0])

    def test_multiple_playback_streams_resolve_while_device_ambiguity_fails_closed(self) -> None:
        duplicate_playback = dict(SOURCES[0], id="sink-input:999", pulseId="999")
        duplicate_monitor = dict(
            SOURCES[2], id="source:duplicate-monitor", pulseId="998"
        )
        result = resolve_source_matchers(
            SOURCES + [duplicate_playback, duplicate_monitor],
            [
                matcher_for_source(SOURCES[0]),
                matcher_for_source(SOURCES[2]),
                {"kind": "monitor", "name": "missing.monitor"},
            ],
        )

        self.assertEqual(result["selectedIds"], ["sink-input:101", "sink-input:999"])
        self.assertEqual(
            result["ambiguousMatchers"],
            [{"matcherIndex": 1, "candidateCount": 2}],
        )
        self.assertEqual(result["missingMatchers"], [2])

    def test_saved_playback_source_remains_selectable_while_inactive(self) -> None:
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
            saved = app.execute(
                {
                    "operation": "configure",
                    "payload": {
                        "sourceMatchers": [
                            {
                                "kind": "playback",
                                "processBinary": "chromium",
                                "applicationName": "Chromium",
                            }
                        ]
                    },
                }
            )
            inspected = app.execute({"operation": "inspect"})

        self.assertTrue(saved["ok"])
        self.assertEqual(
            inspected["sourceChoices"],
            [
                {
                    "id": "source-choice:6154cc8582fac0f0",
                    "type": "playback",
                    "categoryLabel": "Playback Source",
                    "label": "Chromium",
                    "detail": "Ready when audio starts",
                    "active": False,
                    "activeStreamCount": 0,
                    "selected": True,
                    "recentChoice": False,
                    "sensitiveCapture": False,
                }
            ],
        )

    def test_one_playback_choice_groups_every_active_stream_for_an_application(self) -> None:
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
            second_stream = dict(
                SOURCES[0], id="sink-input:202", pulseId="202", mediaName="Notification"
            )
            app = MixerApplication(discover_sources=lambda: [SOURCES[0], second_stream])
            inspected = app.execute({"operation": "inspect"})

        self.assertEqual(
            inspected["sourceChoices"],
            [
                {
                    "id": "source-choice:7d6c98a6b1755289",
                    "type": "playback",
                    "categoryLabel": "Playback Source",
                    "label": "Example Player",
                    "detail": "2 active streams",
                    "active": True,
                    "activeStreamCount": 2,
                    "selected": False,
                    "recentChoice": False,
                    "sensitiveCapture": False,
                }
            ],
        )

    def test_logical_choice_selection_persists_only_stable_matchers(self) -> None:
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
            app = MixerApplication(discover_sources=lambda: SOURCES)
            initial = app.execute({"operation": "inspect"})
            player = next(
                choice
                for choice in initial["sourceChoices"]
                if choice["label"] == "Example Player"
            )
            saved = app.execute(
                {
                    "operation": "configure",
                    "payload": {"sourceChoiceIds": [player["id"]]},
                }
            )
            persisted = config_path().read_text(encoding="utf-8")
            inspected = app.execute({"operation": "inspect"})

        self.assertTrue(saved["ok"])
        self.assertNotIn("sink-input:101", persisted)
        self.assertNotIn('"pulseId"', persisted)
        self.assertIn('"processBinary": "example-player"', persisted)
        selected_player = next(
            choice
            for choice in inspected["sourceChoices"]
            if choice["label"] == "Example Player"
        )
        self.assertTrue(selected_player["selected"])

    def test_logical_capture_choice_is_explicit_for_one_start_but_not_persisted(self) -> None:
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
            app = MixerApplication(discover_sources=lambda: SOURCES)
            initial = app.execute({"operation": "inspect"})
            microphone = next(
                choice
                for choice in initial["sourceChoices"]
                if choice["type"] == "capture"
            )
            configured = app.execute(
                {
                    "operation": "configure",
                    "payload": {"sourceChoiceIds": [microphone["id"]]},
                }
            )
            persisted = config_path().read_text(encoding="utf-8")

        self.assertTrue(configured["ok"])
        self.assertEqual(
            configured["config"]["sourceIds"],
            ["source:alsa_input.usb-mic"],
        )
        self.assertNotIn("source:alsa_input.usb-mic", persisted)
        self.assertIn('"kind": "capture"', persisted)

    def test_numeric_ids_are_not_persisted_when_configuration_is_saved(self) -> None:
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
            app = MixerApplication(discover_sources=lambda: SOURCES)
            saved = app.execute(
                {
                    "operation": "configure",
                    "payload": {
                        "sourceIds": ["sink-input:101", "source:alsa_input.usb-mic"]
                    },
                }
            )
            persisted = config_path().read_text(encoding="utf-8")
            inspected = app.execute({"operation": "inspect"})

        self.assertTrue(saved["ok"])
        self.assertNotIn("sourceIds", persisted)
        self.assertNotIn("sink-input:101", persisted)
        self.assertNotIn('"pulseId"', persisted)
        self.assertIn('"sourceMatchers"', persisted)
        self.assertEqual(inspected["config"]["sourceIds"], ["sink-input:101"])
        capture = next(
            source for source in inspected["sources"] if source["type"] == "capture"
        )
        self.assertTrue(capture["recentChoice"])


if __name__ == "__main__":
    unittest.main()

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

    def test_ambiguity_and_missing_metadata_leave_matcher_unselected(self) -> None:
        duplicate = dict(SOURCES[0], id="sink-input:999", pulseId="999")
        result = resolve_source_matchers(
            SOURCES + [duplicate],
            [matcher_for_source(SOURCES[0]), {"kind": "monitor", "name": "missing.monitor"}],
        )

        self.assertEqual(result["selectedIds"], [])
        self.assertEqual(result["ambiguousMatchers"][0]["candidateCount"], 2)
        self.assertEqual(result["missingMatchers"], [1])

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

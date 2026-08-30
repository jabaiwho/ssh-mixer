from __future__ import annotations

import unittest

from ssh_mixer.routing import build_playback_reconciliation, build_route_plan

SOURCES = [
    {
        "id": "sink-input:101",
        "type": "playback",
        "pulseId": "101",
        "label": "cliamp",
        "sinkName": "alsa_output.headset",
        "sinkLabel": "Headset",
    },
    {
        "id": "sink-input:102",
        "type": "playback",
        "pulseId": "102",
        "label": "Browser",
        "sinkName": "alsa_output.headset",
        "sinkLabel": "Headset",
    },
    {
        "id": "source:mic",
        "type": "capture",
        "name": "alsa_input.mic",
        "label": "Microphone",
    },
    {
        "id": "source:monitor",
        "type": "monitor",
        "name": "alsa_output.headset.monitor",
        "label": "Default output monitor",
    },
]


class RoutePlanTest(unittest.TestCase):
    def test_both_mode_mixes_playback_and_capture_without_local_mic_feedback(self) -> None:
        plan = build_route_plan(SOURCES, ["sink-input:101", "source:mic"], "both")

        self.assertTrue(plan["streamRemote"])
        self.assertEqual(plan["captureSource"], "ssh_mixer_mix.monitor")
        self.assertEqual([op["op"] for op in plan["operations"]], [
            "load-null-sink",
            "move-sink-input",
            "load-loopback",
            "load-loopback",
            "stream-remote",
        ])
        mic_loopbacks = [
            op for op in plan["operations"]
            if op.get("sourceId") == "source:mic" and op.get("op") == "load-loopback"
        ]
        self.assertEqual(mic_loopbacks[0]["sink"], "ssh_mixer_mix")
        self.assertNotIn("alsa_output.headset", [op.get("sink") for op in mic_loopbacks])
        preserve = [op for op in plan["operations"] if op.get("role") == "preserve-local-playback"]
        self.assertEqual(len(preserve), 1)
        self.assertEqual(preserve[0]["sink"], "alsa_output.headset")

    def test_ssh_mode_does_not_preserve_selected_playback_locally(self) -> None:
        plan = build_route_plan(SOURCES, ["sink-input:101"], "ssh")

        self.assertFalse(plan["preserveLocalPlayback"])
        self.assertNotIn("preserve-local-playback", [op.get("role") for op in plan["operations"]])

    def test_local_mode_is_a_noop_for_routing(self) -> None:
        plan = build_route_plan(SOURCES, ["sink-input:101", "source:mic"], "local")

        self.assertFalse(plan["streamRemote"])
        self.assertEqual(plan["operations"], [])
        self.assertIn("does not monitor microphones", " ".join(plan["warnings"]))

    def test_reconciliation_routes_only_new_streams_for_armed_playback_sources(self) -> None:
        chromium = {
            "id": "sink-input:201",
            "type": "playback",
            "pulseId": "201",
            "processBinary": "chromium",
            "applicationName": "Chromium",
            "name": "Chromium",
            "label": "Chromium",
            "sinkName": "alsa_output.headset",
            "sinkLabel": "Headset",
        }
        spotify = {
            "id": "sink-input:202",
            "type": "playback",
            "pulseId": "202",
            "processBinary": "spotify",
            "applicationName": "Spotify",
            "name": "Spotify",
            "label": "Spotify",
            "sinkName": "alsa_output.headset",
            "sinkLabel": "Headset",
        }

        operations = build_playback_reconciliation(
            [chromium, spotify],
            [
                {
                    "kind": "playback",
                    "processBinary": "chromium",
                    "applicationName": "Chromium",
                }
            ],
            destination="both",
            routed_sink_input_ids=set(),
            local_copy_sinks=set(),
        )

        self.assertEqual(
            operations,
            [
                {
                    "op": "load-loopback",
                    "role": "preserve-local-playback",
                    "source": "ssh_mixer_mix.monitor",
                    "sink": "alsa_output.headset",
                    "label": "Local copy for Headset",
                },
                {
                    "op": "move-sink-input",
                    "sourceId": "sink-input:201",
                    "sinkInputId": "201",
                    "fromSink": "alsa_output.headset",
                    "toSink": "ssh_mixer_mix",
                    "label": "Chromium",
                },
            ],
        )

    def test_monitor_sources_warn_that_local_playback_is_passive(self) -> None:
        plan = build_route_plan(SOURCES, ["source:monitor"], "ssh")

        self.assertIn("passive taps", " ".join(plan["warnings"]))
        self.assertEqual(plan["operations"][1]["source"], "alsa_output.headset.monitor")


if __name__ == "__main__":
    unittest.main()

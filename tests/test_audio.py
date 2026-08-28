from __future__ import annotations

import unittest

from ssh_mixer.audio import discover_sources, resolve_source_ids

SINKS = """
Sink #10
    State: RUNNING
    Name: alsa_output.headset
    Description: Corsair VOID PRO Wireless Gaming Headset
    Properties:
        device.profile.description = "Corsair VOID PRO Wireless Gaming Headset"
"""

SOURCES = """
Source #10
    State: RUNNING
    Name: alsa_output.headset.monitor
    Description: Monitor of Corsair VOID PRO Wireless Gaming Headset
    Monitor of Sink: alsa_output.headset
    Properties:
        device.profile.description = "Corsair VOID PRO Wireless Gaming Headset"
Source #11
    State: SUSPENDED
    Name: alsa_input.headset.mono-fallback
    Description: Corsair VOID PRO Wireless Gaming Headset Mono
    Monitor of Sink: n/a
    Properties:
        device.profile.description = "Corsair VOID PRO Wireless Gaming Headset"
"""

SINK_INPUTS = """
Sink Input #101
    Sink: 10
    Properties:
        application.name = "PipeWire ALSA [cliamp]"
        node.name = "alsa_playback.cliamp"
        media.name = "ALSA Playback"
Sink Input #102
    Sink: 10
    Properties:
        application.name = "wayvibes.real"
        node.name = "wayvibes.real"
        media.name = "Playback"
"""


def fake_runner(command: list[str]) -> str:
    key = tuple(command)
    if key == ("pactl", "list", "sinks"):
        return SINKS
    if key == ("pactl", "list", "sources"):
        return SOURCES
    if key == ("pactl", "list", "sink-inputs"):
        return SINK_INPUTS
    if key == ("pactl", "get-default-sink"):
        return "alsa_output.headset\n"
    if key == ("pactl", "get-default-source"):
        return "alsa_input.headset.mono-fallback\n"
    raise AssertionError(command)


class DiscoverSourcesTest(unittest.TestCase):
    def test_discovers_playback_capture_and_monitor_sources(self) -> None:
        sources = discover_sources(fake_runner)
        by_id = {source["id"]: source for source in sources}

        self.assertEqual(by_id["sink-input:101"]["label"], "cliamp")
        self.assertEqual(by_id["sink-input:101"]["categoryLabel"], "Playback Source")
        self.assertFalse(by_id["sink-input:101"]["defaultSelected"])
        self.assertFalse(by_id["sink-input:102"]["defaultSelected"])
        self.assertEqual(by_id["source:alsa_input.headset.mono-fallback"]["type"], "capture")
        self.assertEqual(by_id["source:alsa_input.headset.mono-fallback"]["categoryLabel"], "Capture Source")
        self.assertEqual(by_id["source:alsa_output.headset.monitor"]["type"], "monitor")
        self.assertEqual(by_id["source:alsa_output.headset.monitor"]["categoryLabel"], "Output Monitor")
        self.assertTrue(by_id["source:alsa_output.headset.monitor"]["isDefaultMonitor"])

    def test_resolves_compatibility_aliases_without_selecting_other_apps(self) -> None:
        sources = discover_sources(fake_runner)

        self.assertEqual(resolve_source_ids(sources, ["cliamp"]), ["sink-input:101"])
        self.assertEqual(resolve_source_ids(sources, ["system"]), ["source:alsa_output.headset.monitor"])
        self.assertEqual(resolve_source_ids(sources, ["wayvibes.real"]), [])


if __name__ == "__main__":
    unittest.main()

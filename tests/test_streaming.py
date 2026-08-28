from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from ssh_mixer.streaming import (
    StreamEpochPolicy,
    StreamSilenceState,
    build_encoder_command,
)

ROOT = Path(__file__).resolve().parents[1]


class StreamPipelineTest(unittest.TestCase):
    def test_stream_epoch_refreshes_on_silence_after_fifteen_minutes(self) -> None:
        policy = StreamEpochPolicy(started_at=100.0)

        self.assertIsNone(policy.refresh_reason(now=999.9, silence_active=True))
        self.assertEqual(
            policy.refresh_reason(now=1_000.0, silence_active=True),
            "silence",
        )

    def test_stream_epoch_forces_refresh_at_thirty_minutes_without_silence(self) -> None:
        policy = StreamEpochPolicy(started_at=100.0)

        self.assertIsNone(policy.refresh_reason(now=1_899.9, silence_active=False))
        self.assertEqual(
            policy.refresh_reason(now=1_900.0, silence_active=False),
            "deadline",
        )

    def test_stream_silence_state_follows_encoder_events(self) -> None:
        silence = StreamSilenceState()

        silence.feed("[silencedetect] silence_start: 12.5\n")
        self.assertTrue(silence.active)

        silence.feed("[silencedetect] silence_end: 14.0 | silence_duration: 1.5\n")
        self.assertFalse(silence.active)

    def test_encoder_reports_one_second_of_quiet_without_changing_audio(self) -> None:
        command = build_encoder_command("ssh_mixer_mix.monitor", "128k")

        self.assertEqual(
            command[command.index("-af") + 1],
            "silencedetect=noise=-50dB:d=1",
        )
        self.assertIn("-nostats", command)

    def test_opus_ogg_pipeline_uses_the_reviewed_low_latency_cadence(self) -> None:
        command = build_encoder_command("ssh_mixer_mix.monitor", "128k")

        self.assertEqual(command[command.index("-frame_duration") + 1], "10")
        self.assertEqual(command[command.index("-page_duration") + 1], "10000")
        self.assertEqual(command[command.index("-c:a") + 1], "libopus")
        self.assertEqual(command[command.index("-f", command.index("-c:a")) + 1], "ogg")
        self.assertEqual(command[-1], "pipe:1")

    def test_real_encoder_emits_no_more_than_one_frame_per_ogg_page(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_stream_latency.py")],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        measured = json.loads(completed.stdout)
        self.assertEqual(measured["frameDurationMs"], 10)
        self.assertLessEqual(measured["maximumPageDurationSamples"], 480)


if __name__ == "__main__":
    unittest.main()

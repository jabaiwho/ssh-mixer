from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from ssh_mixer.streaming import build_encoder_command

ROOT = Path(__file__).resolve().parents[1]


class StreamPipelineTest(unittest.TestCase):
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

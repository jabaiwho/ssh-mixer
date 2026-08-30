from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "ssh-mixer"

AVAILABLE_SOURCES = [
    {
        "id": "sink-input:101",
        "type": "playback",
        "pulseId": "101",
        "label": "cliamp",
        "sinkName": "alsa_output.headset",
        "sinkLabel": "Headset",
    }
]


class CliPlanTest(unittest.TestCase):
    def test_snapshot_uses_the_versioned_application_seam(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = str(Path(temp) / "config")
            env["XDG_DATA_HOME"] = str(Path(temp) / "data")
            env["XDG_STATE_HOME"] = str(Path(temp) / "state")
            env["XDG_RUNTIME_DIR"] = str(Path(temp) / "runtime")
            completed = subprocess.run(
                [str(BIN), "snapshot"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                check=True,
            )

        parsed = json.loads(completed.stdout)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["schemaVersion"], 1)
        self.assertEqual(parsed["config"]["schemaVersion"], 4)
        self.assertEqual(parsed["config"]["remote"]["host"], "")

    def test_cli_failures_include_a_structured_diagnostic_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = str(Path(temp) / "config")
            env["XDG_DATA_HOME"] = str(Path(temp) / "data")
            env["XDG_STATE_HOME"] = str(Path(temp) / "state")
            env["XDG_RUNTIME_DIR"] = str(Path(temp) / "runtime")
            completed = subprocess.run(
                [str(BIN), "plan", "--json", "{"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                check=False,
            )

        parsed = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 1)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["diagnostic"]["stage"], "cli.plan")
        self.assertEqual(parsed["diagnostic"]["code"], "JSONDecodeError")

    def test_diagnostic_retention_and_contribution_are_available_through_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = str(Path(temp) / "config")
            env["XDG_DATA_HOME"] = str(Path(temp) / "data")
            env["XDG_STATE_HOME"] = str(Path(temp) / "state")
            env["XDG_RUNTIME_DIR"] = str(Path(temp) / "runtime")
            configured = subprocess.run(
                [
                    str(BIN),
                    "diagnostics-retention",
                    "--json",
                    '{"retentionPolicy":"extended"}',
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                check=True,
            )
            contribution = subprocess.run(
                [str(BIN), "diagnostics-contribute-url"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                check=True,
            )

        self.assertEqual(json.loads(configured.stdout)["settings"]["policy"], "extended")
        self.assertEqual(
            json.loads(contribution.stdout)["url"],
            "https://github.com/jabaiwho/ssh-mixer/blob/main/CONTRIBUTING.md",
        )

    def test_plan_command_is_json_public_seam(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = str(Path(temp) / "config")
            env["XDG_STATE_HOME"] = str(Path(temp) / "state")
            payload = {
                "availableSources": AVAILABLE_SOURCES,
                "sourceIds": ["sink-input:101"],
                "destination": "ssh",
            }
            completed = subprocess.run(
                [str(BIN), "plan", "--stdin"],
                input=json.dumps(payload),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                check=True,
            )

        parsed = json.loads(completed.stdout)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["plan"]["operations"][1]["op"], "move-sink-input")
        self.assertEqual(parsed["plan"]["destination"], "ssh")


if __name__ == "__main__":
    unittest.main()

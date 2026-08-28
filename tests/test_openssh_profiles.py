from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.openssh_profiles import (
    ProfileError,
    discover_profiles,
    inspect_profile,
    profile_connection,
)
from ssh_mixer.session import ssh_base_command


class OpenSshProfileTest(unittest.TestCase):
    def test_discovers_concrete_profiles_and_includes_without_executing_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            included = root / "included.conf"
            included.write_text("Host studio-mac\n  HostName studio.internal\n", encoding="utf-8")
            config = root / "config"
            config.write_text(
                "\n".join(
                    (
                        f"Include {included.name}",
                        "Host media-box media-alias",
                        "  HostName receiver.example",
                        "Host *.example !blocked exact?pattern",
                        "Host *",
                        "  ForwardAgent no",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            profiles = discover_profiles(config)

        self.assertEqual(profiles, ["media-alias", "media-box", "studio-mac"])

    def test_match_exec_is_rejected_before_effective_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config"
            config.write_text(
                "Host media-box\n  HostName receiver.example\nMatch exec \"false\"\n",
                encoding="utf-8",
            )
            called = False

            def runner(_command: list[str]) -> subprocess.CompletedProcess[str]:
                nonlocal called
                called = True
                return subprocess.CompletedProcess([], 0, "", "")

            with self.assertRaises(ProfileError):
                inspect_profile("media-box", config_path=config, runner=runner)

        self.assertFalse(called)

    def test_inspects_effective_configuration_and_summarizes_proxy_without_key_paths(self) -> None:
        output = "\n".join(
            (
                "hostname receiver.example",
                "user listener",
                "port 2222",
                "proxyjump bastion",
                "proxycommand /usr/bin/provider-cli connect receiver.example",
                "identityfile /home/listener/.ssh/id_provider",
                "identityfile /hardware/token",
                "localcommand echo unsafe",
            )
        )
        commands: list[list[str]] = []

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, output, "")

        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config"
            config.write_text("Host media-box\n", encoding="utf-8")
            inspected = inspect_profile("media-box", config_path=config, runner=runner)

        self.assertEqual(commands[0][-2:], ["--", "media-box"])
        self.assertEqual(commands[0][1:3], ["-F", str(config)])
        self.assertEqual(inspected["host"], "receiver.example")
        self.assertEqual(inspected["user"], "listener")
        self.assertEqual(inspected["port"], 2222)
        self.assertEqual(inspected["proxyJump"], "bastion")
        self.assertTrue(inspected["proxyCommandConfigured"])
        self.assertEqual(inspected["proxyExecutable"], "provider-cli")
        self.assertEqual(inspected["identityCount"], 2)
        self.assertNotIn("identityfile", str(inspected).lower())
        self.assertNotIn("id_provider", str(inspected))
        self.assertIn("PermitLocalCommand=no", commands[0])
        self.assertIn("ClearAllForwardings=yes", commands[0])

    def test_application_requires_confirmation_for_user_managed_permissions(self) -> None:
        inspected = {
            "profile": "media-box",
            "host": "receiver.example",
            "user": "listener",
            "port": 22,
            "proxyCommandConfigured": False,
            "proxyExecutable": "",
            "proxyCommandHash": "",
            "proxyJump": "bastion",
            "identityCount": 1,
        }
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
                inspect_profile=lambda _profile: inspected,
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            rejected = app.execute(
                {"operation": "profile.save", "payload": {"profile": "media-box"}}
            )
            saved = app.execute(
                {
                    "operation": "profile.save",
                    "payload": {
                        "profile": "media-box",
                        "proxyConfirmed": False,
                        "userManagedConfirmed": True,
                    },
                }
            )
            bypass = app.execute(
                {
                    "operation": "connection.save",
                    "payload": {"connection": saved.get("connection")},
                }
            )

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["diagnostic"]["code"], "confirmation-required")
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["connection"]["securityLevel"], "user-managed")
        self.assertFalse(bypass["ok"])
        self.assertEqual(bypass["diagnostic"]["code"], "profile-confirmation-required")

    def test_runtime_profile_preserves_proxy_routing_but_overrides_unsafe_behavior(self) -> None:
        inspected = {
            "profile": "cloud-box",
            "host": "instance.internal",
            "user": "listener",
            "port": 22,
            "proxyCommandConfigured": True,
            "proxyExecutable": "provider-cli",
            "proxyCommandHash": "a" * 64,
            "proxyJump": "none",
            "identityCount": 1,
        }
        connection = profile_connection(inspected, proxy_confirmed=True)

        with patch("ssh_mixer.session.inspect_profile", return_value=inspected):
            command = ssh_base_command(
                {
                    "connection": connection,
                    "connectTimeoutSeconds": 5,
                    "keyPath": "",
                }
            )

        self.assertEqual(command[-2:], ["--", "cloud-box"])
        self.assertNotIn("/dev/null", command)
        self.assertNotIn("-i", command)
        self.assertEqual(command[1:3], ["-F", str(Path.home() / ".ssh" / "config")])
        self.assertIn("ClearAllForwardings=yes", command)
        self.assertIn("ForwardAgent=no", command)
        self.assertIn("PermitLocalCommand=no", command)
        self.assertIn("RequestTTY=no", command)

    def test_proxy_command_must_be_confirmed_and_changed_configuration_is_rejected(self) -> None:
        inspected = {
            "profile": "cloud-box",
            "host": "instance.internal",
            "user": "listener",
            "port": 22,
            "proxyCommandConfigured": True,
            "proxyExecutable": "provider-cli",
            "proxyCommandHash": "first-hash",
            "proxyJump": "none",
            "identityCount": 1,
            "effectiveConfigHash": "b" * 64,
        }

        with self.assertRaises(ProfileError):
            profile_connection(inspected, proxy_confirmed=False)
        connection = profile_connection(inspected, proxy_confirmed=True)
        self.assertEqual(connection["securityLevel"], "user-managed")
        self.assertEqual(connection["proxyCommandHash"], "first-hash")

        changed = dict(inspected, proxyCommandHash="changed-hash")
        with self.assertRaises(ProfileError):
            profile_connection(
                changed,
                proxy_confirmed=True,
                expected_proxy_hash=connection["proxyCommandHash"],
            )
        changed_effective = dict(inspected, effectiveConfigHash="c" * 64)
        with self.assertRaises(ProfileError):
            profile_connection(
                changed_effective,
                proxy_confirmed=True,
                expected_effective_hash=connection["effectiveConfigHash"],
            )


if __name__ == "__main__":
    unittest.main()

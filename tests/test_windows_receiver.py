from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.identity import ManagedIdentityStore
from ssh_mixer.windows_bootstrap import (
    WINDOWS_STAGING_RE,
    WINDOWS_TRANSACTION_RE,
    WindowsBootstrap,
)
from ssh_mixer.windows_setup import (
    SetupError,
    WindowsSetupTracer,
    authorized_key_entry,
    build_windows_plan,
    parse_receiver_operation,
    verify_restrictions,
)

ROOT = Path(__file__).resolve().parents[1]
SETUP_PATH = ROOT / "receiver" / "windows" / "setup-v1.ps1"
RECEIVER_PATH = ROOT / "receiver" / "windows" / "ssh-mixer-receiver-v1.ps1"


class WindowsSetupTest(unittest.TestCase):
    def test_staging_and_transaction_paths_have_distinct_validated_prefixes(self) -> None:
        staging = "C:\\Users\\listener\\AppData\\Local\\Temp\\ssh-mixer-setup-0123456789abcdef0123456789abcdef"
        transaction = "C:\\Users\\listener\\AppData\\Local\\Temp\\ssh-mixer-windows-setup-0123456789abcdef0123456789abcdef"
        self.assertIsNotNone(WINDOWS_STAGING_RE.fullmatch(staging))
        self.assertIsNotNone(WINDOWS_TRANSACTION_RE.fullmatch(transaction))
        self.assertIsNone(WINDOWS_STAGING_RE.fullmatch(transaction))
        self.assertIsNone(WINDOWS_TRANSACTION_RE.fullmatch(staging))

    def test_detects_supported_openssh_and_plans_approved_platform_changes(self) -> None:
        plan = build_windows_plan(
            {
                "platform": "windows",
                "user": "listener",
                "profile": "C:\\Users\\listener",
                "openSshVersion": "9.5.0.0",
                "sshdInstalled": True,
                "sshdRunning": True,
                "firewallRule": False,
                "ffplay": False,
                "winget": True,
                "administratorCapable": False,
                "elevated": False,
            },
            administrator_confirmed=False,
        )

        self.assertEqual(plan["schemaVersion"], 1)
        self.assertEqual(plan["platform"], "windows")
        self.assertEqual(plan["packageSource"], "winget")
        self.assertEqual(plan["packages"], ["Gyan.FFmpeg"])
        self.assertIn("--source", plan["packageCommand"])
        self.assertTrue(any(change["kind"] == "firewall-rule" for change in plan["changes"]))
        self.assertTrue(any(change["kind"] == "key-acl" for change in plan["changes"]))
        self.assertFalse(plan["changesApplied"])

    def test_standard_user_can_use_the_already_verified_inbound_ssh_path(self) -> None:
        plan = build_windows_plan(
            {
                "platform": "windows",
                "user": "listener",
                "profile": "C:\\Users\\listener",
                "openSshVersion": "9.5.0.0",
                "sshdInstalled": True,
                "sshdRunning": False,
                "serviceInspectable": False,
                "firewallRule": False,
                "firewallInspectable": False,
                "inboundSshVerified": True,
                "ffplay": True,
                "winget": False,
                "administratorCapable": False,
                "elevated": False,
            },
            administrator_confirmed=False,
        )

        self.assertEqual(plan["sshdEvidence"], "verified-inbound-connection")
        self.assertEqual(plan["firewallEvidence"], "verified-inbound-connection")
        self.assertFalse(any(change["kind"] == "firewall-rule" for change in plan["changes"]))

    def test_administrator_capability_requires_confirmation_and_is_disclosed(self) -> None:
        probe = {
            "platform": "windows",
            "user": "Administrator",
            "profile": "C:\\Users\\Administrator",
            "openSshVersion": "9.5.0.0",
            "sshdInstalled": True,
            "sshdRunning": True,
            "firewallRule": True,
            "ffplay": True,
            "winget": True,
            "administratorCapable": True,
            "elevated": False,
        }
        unconfirmed = build_windows_plan(probe, administrator_confirmed=False)
        self.assertTrue(unconfirmed["administratorConfirmationRequired"])
        self.assertFalse(unconfirmed["administratorConfirmed"])
        confirmed = build_windows_plan(probe, administrator_confirmed=True)
        self.assertTrue(confirmed["administratorConfirmed"])
        elevated_bootstrap = build_windows_plan(
            {**probe, "elevated": True}, administrator_confirmed=True
        )
        self.assertTrue(elevated_bootstrap["bootstrapElevated"])
        self.assertFalse(elevated_bootstrap["requiredRuntimeElevated"])

    def test_unsupported_windows_openssh_fails_closed(self) -> None:
        with self.assertRaises(SetupError):
            build_windows_plan(
                {
                    "platform": "windows",
                    "user": "listener",
                    "profile": "C:\\Users\\listener",
                    "openSshVersion": "7.7.0.0",
                    "sshdInstalled": True,
                    "sshdRunning": True,
                    "firewallRule": True,
                    "ffplay": True,
                    "winget": True,
                    "administratorCapable": False,
                    "elevated": False,
                },
                administrator_confirmed=False,
            )

    def test_windows_authorized_key_is_forced_and_restricted(self) -> None:
        entry = authorized_key_entry("ssh-ed25519 AAAATEST ssh-mixer")
        for restriction in (
            "command=",
            "no-agent-forwarding",
            "no-port-forwarding",
            "no-X11-forwarding",
            "no-pty",
            "no-user-rc",
        ):
            self.assertIn(restriction, entry)
        self.assertTrue(verify_restrictions(entry, "AAAATEST"))
        self.assertFalse(verify_restrictions(entry.replace("no-port-forwarding,", ""), "AAAATEST"))

    def test_transparent_setup_has_acl_firewall_trusted_package_and_rollback(self) -> None:
        setup = SETUP_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Invoke-Expression", setup)
        self.assertNotRegex(setup, r"(?i)Invoke-WebRequest|\bcurl(?:\.exe)?\b")
        self.assertIn("Add-WindowsCapability", setup)
        self.assertIn("New-NetFirewallRule", setup)
        self.assertIn("Set-Acl", setup)
        self.assertIn("keysAclChanged", setup)
        self.assertIn("standard Receiver authorized_keys ACL requires separate native preparation", setup)
        self.assertIn("[Security.AccessControl.FileSecurity]::new()", setup)
        self.assertIn("@(@($existing) + @($entry))", setup)
        self.assertIn(r"TrimEnd('\') + '\'", setup)
        self.assertNotIn(r"TrimEnd('\\') + '\\'", setup)
        self.assertIn("--source", setup)
        self.assertIn("winget", setup)
        self.assertIn("Rollback-Incomplete", setup)

    def test_protocol_parser_rejects_malformed_and_unknown_operations(self) -> None:
        self.assertEqual(
            parse_receiver_operation("ssh-mixer-receiver v1 capabilities")["operation"],
            "capabilities",
        )
        for command in (
            "cmd.exe",
            "ssh-mixer-receiver v2 capabilities",
            "ssh-mixer-receiver v1 shell",
            "ssh-mixer-receiver v1 play extra",
            "ssh-mixer-receiver v1 quiet-test --dbfs nope",
        ):
            with self.assertRaises(SetupError):
                parse_receiver_operation(command)

    def test_receiver_playback_uses_external_clock_for_continuous_correction(self) -> None:
        receiver = RECEIVER_PATH.read_text(encoding="utf-8")
        self.assertIn("'-flags', 'low_delay', '-sync', 'ext'", receiver)
        self.assertIn("'-f', 'ogg', '-'", receiver)

    def test_receiver_copies_binary_stdin_to_a_direct_headless_ffplay_process(self) -> None:
        receiver = RECEIVER_PATH.read_text(encoding="utf-8")
        self.assertIn("function Invoke-FFplay", receiver)
        self.assertIn("$startInfo.UseShellExecute = $false", receiver)
        self.assertIn("$startInfo.CreateNoWindow = $true", receiver)
        self.assertIn("$startInfo.RedirectStandardInput = $true", receiver)
        self.assertIn("[Console]::OpenStandardInput().CopyTo($process.StandardInput.BaseStream)", receiver)
        self.assertIn("Invoke-FFplay -Arguments @(", receiver)
        self.assertNotIn("& $ffplay", receiver)

    def test_setup_and_receiver_reject_unusable_ffplay_application_aliases(self) -> None:
        setup = SETUP_PATH.read_text(encoding="utf-8")
        receiver = RECEIVER_PATH.read_text(encoding="utf-8")
        for artifact in (setup, receiver):
            self.assertIn("function Test-FFplayUsable", artifact)
            self.assertIn("[Diagnostics.ProcessStartInfo]::new()", artifact)
            self.assertIn("$startInfo.UseShellExecute = $false", artifact)
            self.assertIn("$process.WaitForExit(5000)", artifact)
        self.assertIn("ffplay = (Test-FFplayUsable)", setup)
        self.assertIn("if (-not (Test-FFplayUsable))", setup)
        self.assertIn("ffplay = (Test-FFplayUsable)", receiver)
        self.assertIn("if (-not (Test-FFplayUsable))", receiver)

    def test_receiver_quiet_test_is_fixed_bounded_faded_and_non_elevated(self) -> None:
        receiver = RECEIVER_PATH.read_text(encoding="utf-8")
        self.assertIn("$QuietStartDbfs = -40", receiver)
        self.assertIn("$QuietMaximumDbfs = -24", receiver)
        self.assertIn("$QuietStepDb = 4", receiver)
        self.assertIn("afade=t=in", receiver)
        self.assertIn("afade=t=out", receiver)
        self.assertIn("Assert-NonElevated", receiver)
        self.assertNotIn("Set-AudioDevice", receiver)
        self.assertNotIn("Invoke-Expression", receiver)

    def test_bootstrap_preserves_remote_no_change_and_complete_rollback_markers(self) -> None:
        connection = {
            "type": "direct",
            "host": "windows.example",
            "user": "listener",
            "port": 22,
        }
        staging = (
            "C:\\Users\\listener\\AppData\\Local\\Temp\\"
            "ssh-mixer-setup-0123456789abcdef0123456789abcdef"
        )
        plan = build_windows_plan(
            {
                "platform": "windows",
                "user": "listener",
                "profile": "C:\\Users\\listener",
                "openSshVersion": "9.5.0.0",
                "sshdInstalled": True,
                "sshdRunning": True,
                "firewallRule": True,
                "ffplay": True,
                "winget": True,
                "administratorCapable": True,
                "elevated": True,
            },
            administrator_confirmed=True,
        )

        for marker in ("rolled-back", "no-changes-applied"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temp:
                ssh_calls = 0

                def runner(command: list[str], **kwargs: object):
                    nonlocal ssh_calls
                    if command[0] == "scp":
                        return subprocess.CompletedProcess(command, 0, "", "")
                    ssh_calls += 1
                    if ssh_calls == 1:
                        return subprocess.CompletedProcess(command, 0, staging, "")
                    if ssh_calls == 2:
                        noninteractive = "-T" in command and "-tt" not in command
                        captured = (
                            json.dumps(
                                {
                                    "schemaVersion": 1,
                                    "ok": False,
                                    "code": marker,
                                },
                                separators=(",", ":"),
                            )
                            if noninteractive
                            and kwargs.get("stderr") == subprocess.STDOUT
                            else ""
                        )
                        return subprocess.CompletedProcess(
                            command, 1 if noninteractive else 0, captured, ""
                        )
                    return subprocess.CompletedProcess(command, 0, "", "")

                public_key = Path(temp) / "id_ed25519.pub"
                public_key.write_text(
                    "ssh-ed25519 AAAATEST ssh-mixer\n", encoding="utf-8"
                )
                bootstrap = WindowsBootstrap(
                    connection,
                    known_hosts=Path(temp) / "known_hosts",
                    runner=runner,
                )
                with self.assertRaises(ValueError):
                    bootstrap.apply(plan, {"publicKeyPath": str(public_key)})
                rollback = bootstrap.rollback(plan, {})

                self.assertEqual(rollback, {"ok": True, "complete": True})

    def test_bootstrap_surfaces_bounded_structured_setup_failure(self) -> None:
        output = "\n".join(
            [
                '{"schemaVersion":1,"ok":false,"code":"rolled-back"}',
                '{"schemaVersion":1,"ok":false,"stage":"receiver.windows-setup","code":"setup-failed","message":"ffplay executable verification failed"}',
            ]
        )
        connection = {"type": "direct", "host": "windows.example", "user": "listener", "port": 22}
        staging = "C:\\Users\\listener\\AppData\\Local\\Temp\\ssh-mixer-setup-0123456789abcdef0123456789abcdef"
        calls = 0

        def runner(command: list[str], **_kwargs: object):
            nonlocal calls
            if command[0] == "scp":
                return subprocess.CompletedProcess(command, 0, "", "")
            calls += 1
            return subprocess.CompletedProcess(command, 0, staging, "") if calls == 1 else subprocess.CompletedProcess(command, 1, output, "")

        with tempfile.TemporaryDirectory() as temp:
            public_key = Path(temp) / "id_ed25519.pub"
            public_key.write_text("ssh-ed25519 AAAATEST ssh-mixer\n", encoding="utf-8")
            bootstrap = WindowsBootstrap(connection, known_hosts=Path(temp) / "known_hosts", runner=runner)
            plan = build_windows_plan(
                {
                    "platform": "windows", "user": "listener", "profile": "C:\\Users\\listener",
                    "openSshVersion": "9.5.0.0", "sshdInstalled": True, "sshdRunning": True,
                    "firewallRule": True, "ffplay": True, "winget": True,
                    "administratorCapable": False, "elevated": False,
                },
                administrator_confirmed=False,
            )
            with self.assertRaisesRegex(ValueError, "ffplay executable verification failed"):
                bootstrap.apply(plan, {"publicKeyPath": str(public_key)})

    def test_tracer_reports_structured_incomplete_rollback(self) -> None:
        calls: list[str] = []
        tracer = WindowsSetupTracer(
            apply=lambda _plan, _identity: calls.append("apply") or {"ok": True},
            verify=lambda _plan, _identity: calls.append("verify")
            or {"ok": False, "error": "ACL verification failed"},
            rollback=lambda _plan, _identity: calls.append("rollback")
            or {"ok": False, "complete": False},
        )
        plan = tracer.trace_plan(
            {
                "platform": "windows",
                "user": "listener",
                "profile": "C:\\Users\\listener",
                "openSshVersion": "9.5.0.0",
                "sshdInstalled": True,
                "sshdRunning": True,
                "firewallRule": True,
                "ffplay": True,
                "winget": True,
                "administratorCapable": False,
                "elevated": False,
            },
            administrator_confirmed=False,
        )
        result = tracer.execute(plan, {}, approved_plan_hash=plan["planHash"])
        self.assertEqual(calls, ["apply", "verify", "rollback"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["rollbackIncomplete"])


@unittest.skipIf(os.name == "nt", "Omarchy application runtime is Linux-only")
class WindowsApplicationTest(unittest.TestCase):
    def test_application_requires_approval_then_persists_verified_windows_identity(self) -> None:
        from ssh_mixer.application import MixerApplication
        from ssh_mixer.diagnostics import DiagnosticStore

        class Bootstrap:
            def probe(self):
                return {
                    "platform": "windows",
                    "user": "listener",
                    "profile": "C:\\Users\\listener",
                    "openSshVersion": "9.5.0.0",
                    "sshdInstalled": True,
                    "sshdRunning": True,
                    "firewallRule": True,
                    "ffplay": True,
                    "winget": True,
                    "administratorCapable": False,
                    "elevated": False,
                }

            def apply(self, _plan, _identity):
                return {"ok": True}

            def verify(self, _plan, _identity):
                return {"ok": True}

            def rollback(self, _plan, _identity):
                return {"ok": True, "complete": True}

        def keygen(command: list[str], **_kwargs: object):
            key_path = Path(command[command.index("-f") + 1])
            key_path.write_text("private", encoding="utf-8")
            key_path.with_suffix(".pub").write_text(
                "ssh-ed25519 AAAATEST ssh-mixer", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        connection = {
            "type": "direct",
            "host": "windows.example",
            "user": "listener",
            "port": 22,
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
                windows_bootstrap_factory=lambda _connection, _address: Bootstrap(),
                identity_store=ManagedIdentityStore(Path(temp) / "keys", runner=keygen),
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            planned = app.execute(
                {
                    "operation": "receiver.windows-plan",
                    "payload": {
                        "connection": connection,
                        "administratorConfirmed": False,
                    },
                }
            )
            rejected = app.execute(
                {"operation": "receiver.windows-setup", "payload": {"connection": connection}}
            )
            completed = app.execute(
                {
                    "operation": "receiver.windows-setup",
                    "payload": {
                        "connection": connection,
                        "changesApproved": True,
                        "administratorConfirmed": False,
                        "approvedPlanHash": planned["plan"]["planHash"],
                        "encryptedIdentity": False,
                    },
                }
            )

        self.assertTrue(planned["ok"])
        self.assertFalse(rejected["ok"])
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["connection"]["receiverPlatform"], "windows")
        self.assertEqual(completed["connection"]["securityLevel"], "receiver-only")
        self.assertEqual(
            completed["config"]["connection"]["receiverPlatform"], "windows"
        )

    def test_privileged_setup_requires_an_already_elevated_bootstrap(self) -> None:
        from ssh_mixer.application import MixerApplication
        from ssh_mixer.diagnostics import DiagnosticStore

        class Bootstrap:
            def probe(self):
                return {
                    "platform": "windows",
                    "user": "listener",
                    "profile": "C:\\Users\\listener",
                    "openSshVersion": "9.5.0.0",
                    "sshdInstalled": True,
                    "sshdRunning": True,
                    "firewallRule": True,
                    "ffplay": True,
                    "winget": True,
                    "administratorCapable": True,
                    "elevated": False,
                }

            def apply(self, _plan, _identity):
                return {"ok": True}

            def verify(self, _plan, _identity):
                return {"ok": True}

            def rollback(self, _plan, _identity):
                return {"ok": True, "complete": True}

        generated: list[bool] = []

        def keygen(command: list[str], **_kwargs: object):
            generated.append(True)
            key_path = Path(command[command.index("-f") + 1])
            key_path.write_text("private", encoding="utf-8")
            key_path.with_suffix(".pub").write_text(
                "ssh-ed25519 AAAATEST ssh-mixer", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        connection = {
            "type": "direct",
            "host": "windows.example",
            "user": "listener",
            "port": 22,
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
                windows_bootstrap_factory=lambda _connection, _address: Bootstrap(),
                identity_store=ManagedIdentityStore(Path(temp) / "keys", runner=keygen),
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            planned = app.execute(
                {
                    "operation": "receiver.windows-plan",
                    "payload": {
                        "connection": connection,
                        "administratorConfirmed": True,
                    },
                }
            )
            blocked = app.execute(
                {
                    "operation": "receiver.windows-setup",
                    "payload": {
                        "connection": connection,
                        "changesApproved": True,
                        "administratorConfirmed": True,
                        "approvedPlanHash": planned["plan"]["planHash"],
                        "encryptedIdentity": False,
                    },
                }
            )

        self.assertFalse(blocked["ok"])
        self.assertEqual(
            blocked["diagnostic"]["code"], "elevated-bootstrap-required"
        )
        self.assertEqual(generated, [])


if __name__ == "__main__":
    unittest.main()

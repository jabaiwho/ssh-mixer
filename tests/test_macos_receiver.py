from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.identity import ManagedIdentityStore
from ssh_mixer.macos_setup import (
    MacOsSetupTracer,
    SetupError,
    authorized_key_entry,
    build_macos_plan,
    parse_receiver_operation,
    verify_restrictions,
)

ROOT = Path(__file__).resolve().parents[1]
SETUP_PATH = ROOT / "receiver" / "macos" / "setup-v1.sh"
RECEIVER_PATH = ROOT / "receiver" / "macos" / "ssh-mixer-receiver-v1"


class MacOsSetupTest(unittest.TestCase):
    def test_arm_plan_uses_only_the_architecture_specific_homebrew_location(self) -> None:
        plan = build_macos_plan(
            {
                "platform": "macos",
                "architecture": "arm64",
                "version": "14.6.1",
                "user": "listener",
                "home": "/Users/listener",
                "openSshVersion": "9.6",
                "remoteLogin": True,
                "administratorCapable": False,
                "homebrewPath": "/opt/homebrew/bin/brew",
                "ffplay": False,
                "elevated": False,
            }
        )

        self.assertTrue(plan["experimental"])
        self.assertEqual(plan["homebrewPath"], "/opt/homebrew/bin/brew")
        self.assertEqual(
            plan["packageCommand"], ["/opt/homebrew/bin/brew", "install", "ffmpeg"]
        )
        self.assertEqual(plan["packages"], ["ffmpeg"])
        self.assertFalse(plan["changesApplied"])

    def test_intel_plan_uses_usr_local_and_unknown_architecture_fails_closed(self) -> None:
        intel = build_macos_plan(
            {
                "platform": "macos",
                "architecture": "x86_64",
                "version": "13.7",
                "user": "listener",
                "home": "/Users/listener",
                "openSshVersion": "9.0",
                "remoteLogin": True,
                "administratorCapable": True,
                "homebrewPath": "/usr/local/bin/brew",
                "ffplay": True,
                "elevated": False,
            }
        )
        self.assertEqual(intel["homebrewPath"], "/usr/local/bin/brew")
        self.assertTrue(intel["administratorCapable"])
        with self.assertRaises(SetupError):
            build_macos_plan(
                {
                    "platform": "macos",
                    "architecture": "riscv64",
                    "version": "15.0",
                    "user": "listener",
                    "home": "/Users/listener",
                    "openSshVersion": "9.8",
                    "remoteLogin": True,
                    "administratorCapable": False,
                    "homebrewPath": "",
                    "ffplay": False,
                    "elevated": False,
                }
            )

    def test_missing_homebrew_is_explained_but_never_bootstrapped_by_a_pipeline(self) -> None:
        with self.assertRaisesRegex(SetupError, "Homebrew"):
            build_macos_plan(
                {
                    "platform": "macos",
                    "architecture": "arm64",
                    "version": "14.6",
                    "user": "listener",
                    "home": "/Users/listener",
                    "openSshVersion": "9.6",
                    "remoteLogin": True,
                    "administratorCapable": False,
                    "homebrewPath": "",
                    "ffplay": False,
                    "elevated": False,
                }
            )
        setup = SETUP_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(setup, r"\b(curl|wget)\b")

    def test_remote_login_and_privilege_changes_are_disclosed(self) -> None:
        plan = build_macos_plan(
            {
                "platform": "macos",
                "architecture": "arm64",
                "version": "14.6",
                "user": "listener",
                "home": "/Users/listener",
                "openSshVersion": "9.6",
                "remoteLogin": False,
                "administratorCapable": True,
                "homebrewPath": "/opt/homebrew/bin/brew",
                "ffplay": True,
                "elevated": False,
            }
        )
        remote_login = next(
            change for change in plan["changes"] if change["kind"] == "remote-login"
        )
        self.assertTrue(remote_login["requiresPrivilege"])
        self.assertIn("macOS security approval", remote_login["summary"])

    def test_authorized_key_is_forced_and_restricted(self) -> None:
        entry = authorized_key_entry(
            "/Users/listener/.local/lib/ssh-mixer/ssh-mixer-receiver-v1",
            "ssh-ed25519 AAAATEST ssh-mixer",
        )
        for restriction in (
            "command=",
            "restrict",
            "no-agent-forwarding",
            "no-port-forwarding",
            "no-X11-forwarding",
            "no-pty",
            "no-user-rc",
        ):
            self.assertIn(restriction, entry)
        self.assertTrue(verify_restrictions(entry, "AAAATEST"))
        self.assertFalse(verify_restrictions(entry.replace("no-pty,", ""), "AAAATEST"))

    def test_companion_setup_is_transparent_verified_and_rolls_back(self) -> None:
        setup = SETUP_PATH.read_text(encoding="utf-8")
        self.assertIn("systemsetup -setremotelogin on", setup)
        self.assertIn("brew install ffmpeg", setup)
        self.assertIn("shasum -a 256", setup)
        self.assertIn("rollback", setup)
        self.assertIn("ROLLBACK_INCOMPLETE", setup)
        self.assertIn('[ "$MODE" = remove ]', setup)
        self.assertNotIn("spctl --master-disable", setup)
        self.assertNotIn("xattr -dr", setup)

    def test_protocol_rejects_unknown_operations_and_quiet_test_is_bounded(self) -> None:
        self.assertEqual(
            parse_receiver_operation("ssh-mixer-receiver v1 capabilities")["operation"],
            "capabilities",
        )
        quiet = parse_receiver_operation("ssh-mixer-receiver v1 quiet-test --dbfs -32")
        self.assertEqual(quiet["dbfs"], -32)
        self.assertEqual(
            parse_receiver_operation("ssh-mixer-receiver v1 quiet-test --dbfs -1")["dbfs"],
            -1,
        )
        self.assertEqual(
            parse_receiver_operation("ssh-mixer-receiver v1 quiet-test --dbfs 0")["dbfs"],
            0,
        )
        for command in (
            "zsh -c whoami",
            "ssh-mixer-receiver v2 capabilities",
            "ssh-mixer-receiver v1 shell",
            "ssh-mixer-receiver v1 quiet-test --dbfs -41",
            "ssh-mixer-receiver v1 quiet-test --dbfs 1",
            "ssh-mixer-receiver v1 quiet-test --dbfs -32; whoami",
        ):
            with self.assertRaises(SetupError):
                parse_receiver_operation(command)

        receiver = RECEIVER_PATH.read_text(encoding="utf-8")
        self.assertIn("QUIET_START_DBFS=-40", receiver)
        self.assertIn("QUIET_DEFAULT_DBFS=-32", receiver)
        self.assertIn("QUIET_MAXIMUM_DBFS=0", receiver)
        self.assertIn("QUIET_STEP_DB=1", receiver)
        self.assertIn("afade=t=in", receiver)
        self.assertIn("afade=t=out", receiver)
        self.assertIn('"experimental":true', receiver)
        self.assertNotIn("osascript", receiver)

    def test_receiver_playback_uses_external_clock_for_continuous_correction(self) -> None:
        receiver = RECEIVER_PATH.read_text(encoding="utf-8")
        self.assertIn("-flags low_delay -sync ext -f ogg", receiver)

    def test_unknown_forced_command_returns_structured_failure(self) -> None:
        completed = subprocess.run(
            [str(RECEIVER_PATH), "--forced"],
            env={**os.environ, "SSH_ORIGINAL_COMMAND": "zsh -c whoami"},
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        error = json.loads(completed.stderr)
        self.assertEqual(error["code"], "protocol-rejected")
        self.assertTrue(error["experimental"])

    def test_tracer_reports_structured_incomplete_rollback(self) -> None:
        tracer = MacOsSetupTracer(
            apply=lambda _plan, _identity: {"ok": True},
            verify=lambda _plan, _identity: {"ok": False, "error": "restriction failed"},
            rollback=lambda _plan, _identity: {"ok": False, "complete": False},
        )
        plan = tracer.trace_plan(
            {
                "platform": "macos",
                "architecture": "arm64",
                "version": "14.6",
                "user": "listener",
                "home": "/Users/listener",
                "openSshVersion": "9.6",
                "remoteLogin": True,
                "administratorCapable": False,
                "homebrewPath": "/opt/homebrew/bin/brew",
                "ffplay": True,
                "elevated": False,
            }
        )
        result = tracer.execute(plan, {}, approved_plan_hash=plan["planHash"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["experimental"])
        self.assertTrue(result["rollbackIncomplete"])


class MacOsApplicationTest(unittest.TestCase):
    def test_adapter_failure_uses_the_shared_structured_diagnostic_interface(self) -> None:
        class UnsupportedBootstrap:
            def probe(self):
                return {"platform": "unknown"}

            def apply(self, _plan, _identity):
                raise AssertionError("apply must not run")

            def verify(self, _plan, _identity):
                raise AssertionError("verify must not run")

            def rollback(self, _plan, _identity):
                raise AssertionError("rollback must not run")

        connection = {
            "type": "direct",
            "host": "mac.example",
            "user": "listener",
            "port": 22,
        }
        with tempfile.TemporaryDirectory() as temp:
            app = MixerApplication(
                macos_bootstrap_factory=lambda _connection, _address: UnsupportedBootstrap(),
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            failed = app.execute(
                {"operation": "receiver.macos-plan", "payload": {"connection": connection}}
            )
            preview = app.execute({"operation": "diagnostics.preview"})

        self.assertFalse(failed["ok"])
        self.assertEqual(
            failed["diagnostic"]["stage"], "application.receiver.macos-plan"
        )
        self.assertTrue(preview["ok"])
        self.assertIn("application.receiver.macos-plan", preview["report"]["body"])

    def test_application_approval_persists_experimental_verified_adapter(self) -> None:
        class Bootstrap:
            def probe(self):
                return {
                    "platform": "macos",
                    "architecture": "arm64",
                    "version": "14.6",
                    "user": "listener",
                    "home": "/Users/listener",
                    "openSshVersion": "9.6",
                    "remoteLogin": True,
                    "administratorCapable": False,
                    "homebrewPath": "/opt/homebrew/bin/brew",
                    "ffplay": True,
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
            "host": "mac.example",
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
                macos_bootstrap_factory=lambda _connection, _address: Bootstrap(),
                identity_store=ManagedIdentityStore(Path(temp) / "keys", runner=keygen),
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            planned = app.execute(
                {"operation": "receiver.macos-plan", "payload": {"connection": connection}}
            )
            rejected = app.execute(
                {"operation": "receiver.macos-setup", "payload": {"connection": connection}}
            )
            completed = app.execute(
                {
                    "operation": "receiver.macos-setup",
                    "payload": {
                        "connection": connection,
                        "changesApproved": True,
                        "experimentalConfirmed": True,
                        "approvedPlanHash": planned["plan"]["planHash"],
                        "encryptedIdentity": False,
                    },
                }
            )

        self.assertTrue(planned["ok"])
        self.assertTrue(planned["plan"]["experimental"])
        self.assertFalse(rejected["ok"])
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["connection"]["receiverPlatform"], "macos")
        self.assertTrue(completed["connection"]["experimental"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.bootstrap import LinuxBootstrap
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.identity import IdentityError, ManagedIdentityStore
from ssh_mixer.linux_setup import (
    LinuxSetupTracer,
    SetupError,
    authorized_key_entry,
    build_linux_plan,
    verify_restrictions,
)

ROOT = Path(__file__).resolve().parents[1]
RECEIVER_PATH = ROOT / "receiver" / "linux" / "ssh-mixer-receiver-v1.py"
SETUP_PATH = ROOT / "receiver" / "linux" / "setup-v1.sh"


def load_receiver():
    spec = importlib.util.spec_from_file_location("receiver_v1", RECEIVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManagedIdentityTest(unittest.TestCase):
    def test_generates_a_dedicated_unencrypted_identity_in_protected_storage(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            key_path = Path(command[command.index("-f") + 1])
            key_path.write_text("private", encoding="utf-8")
            key_path.with_suffix(".pub").write_text(
                "ssh-ed25519 AAAATEST ssh-mixer", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            store = ManagedIdentityStore(Path(temp), runner=runner)
            identity = store.generate("receiver-123", encrypted=False)

            self.assertEqual(stat.S_IMODE(Path(temp).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(Path(identity["privateKeyPath"]).stat().st_mode), 0o600)
            self.assertNotIn("privateKey", identity)
            self.assertIn("-N", commands[0])
            self.assertEqual(commands[0][commands[0].index("-N") + 1], "")
            self.assertEqual(identity["securityLevel"], "receiver-only")

    def test_encrypted_identity_uses_native_ssh_keygen_prompt_and_agent(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            if command[0] == "ssh-keygen":
                key_path = Path(command[command.index("-f") + 1])
                key_path.write_text("private", encoding="utf-8")
                key_path.with_suffix(".pub").write_text(
                    "ssh-ed25519 AAAATEST ssh-mixer", encoding="utf-8"
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"SSH_AUTH_SOCK": "/run/user/1000/agent"}
        ):
            identity = ManagedIdentityStore(Path(temp), runner=runner).generate(
                "receiver-456", encrypted=True
            )

        self.assertNotIn("-N", commands[0])
        self.assertEqual(commands[1][0], "ssh-add")
        self.assertTrue(identity["agentBacked"])

    def test_loads_only_a_complete_protected_managed_identity(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            key_path = Path(command[command.index("-f") + 1])
            key_path.write_text("private", encoding="utf-8")
            key_path.with_suffix(".pub").write_text(
                "ssh-ed25519 AAAATEST ssh-mixer", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            store = ManagedIdentityStore(Path(temp), runner=runner)
            generated = store.generate("receiver-a", encrypted=False)

            loaded = store.load("receiver-a")
            Path(generated["privateKeyPath"]).chmod(0o644)
            with self.assertRaisesRegex(IdentityError, "unsafe"):
                store.load("receiver-a")

        self.assertEqual(loaded["receiverId"], "receiver-a")
        self.assertEqual(loaded["publicKey"], generated["publicKey"])
        self.assertEqual(loaded["securityLevel"], "receiver-only")

    def test_rejects_root_and_unsafe_receiver_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = ManagedIdentityStore(Path(temp), runner=lambda *_args, **_kwargs: None)
            with self.assertRaises(IdentityError):
                store.generate("../shared", encrypted=False)


class LinuxSetupTest(unittest.TestCase):
    def test_bootstrap_uses_native_openssh_auth_without_receiving_password_input(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object):
            calls.append((command, kwargs))
            output = (
                "platform=linux\nuser=listener\nhome=/home/listener\n"
                "command.python3=true\ncommand.ffplay=true\n"
                "command.apt-get=false\ncommand.dnf=false\n"
                "command.pacman=false\ncommand.zypper=false\n"
            )
            return subprocess.CompletedProcess(command, 0, output, "")

        bootstrap = LinuxBootstrap(
            {
                "type": "direct",
                "host": "receiver.example",
                "user": "listener",
                "port": 22,
            },
            known_hosts=Path("/private/known_hosts"),
            runner=runner,
        )
        probe = bootstrap.probe()

        self.assertEqual(probe["user"], "listener")
        self.assertNotIn("BatchMode=yes", calls[0][0])
        self.assertNotIn("PasswordAuthentication=no", calls[0][0])
        self.assertNotIn("stdin", calls[0][1])
        self.assertNotIn("input", calls[0][1])

    def test_legacy_import_uses_only_the_reviewed_private_bootstrap_identity(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object):
            calls.append(command)
            output = (
                "platform=linux\nuser=listener\nhome=/home/listener\n"
                "command.python3=true\ncommand.ffplay=true\n"
                "command.apt-get=false\ncommand.dnf=false\n"
                "command.pacman=false\ncommand.zypper=false\n"
            )
            return subprocess.CompletedProcess(command, 0, output, "")

        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "legacy-key"
            key.write_text("private", encoding="utf-8")
            key.chmod(0o600)
            bootstrap = LinuxBootstrap(
                {
                    "type": "direct",
                    "host": "receiver.example",
                    "user": "listener",
                    "port": 22,
                },
                known_hosts=Path(temp) / "known_hosts",
                bootstrap_key_path=str(key),
                runner=runner,
            )
            bootstrap.probe()

        self.assertIn("-i", calls[0])
        self.assertIn(str(key), calls[0])
        self.assertIn("IdentitiesOnly=yes", calls[0])

    def test_managed_verification_excludes_the_native_bootstrap_identity(self) -> None:
        calls: list[list[str]] = []
        key_body = "ZmFrZQ=="
        entry = authorized_key_entry(
            "/home/listener/.local/lib/ssh-mixer/ssh-mixer-receiver-v1.py",
            f"ssh-ed25519 {key_body} managed",
        )

        def runner(command: list[str], **_kwargs: object):
            calls.append(command)
            joined = " ".join(command)
            if "awk '/ ssh-mixer-managed-v1$/" in joined:
                return subprocess.CompletedProcess(command, 0, entry + "\n", "")
            if "-R" in command:
                return subprocess.CompletedProcess(command, 255, "", "forwarding rejected")
            if command[-1] == "ssh-mixer-receiver v1 capabilities":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "platform": "linux",
                            "protocol": "v1",
                            "protocolVersion": 1,
                            "helperVersion": "1.1.0",
                        }
                    ),
                    "",
                )
            return subprocess.CompletedProcess(
                command, 1, "", '{"code":"protocol-rejected"}'
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap_key = root / "bootstrap"
            managed_key = root / "managed"
            for path in (bootstrap_key, managed_key):
                path.write_text("private", encoding="utf-8")
                path.chmod(0o600)
            bootstrap = LinuxBootstrap(
                {
                    "type": "direct",
                    "host": "receiver.example",
                    "user": "listener",
                    "port": 22,
                },
                known_hosts=root / "known_hosts",
                bootstrap_key_path=str(bootstrap_key),
                runner=runner,
            )
            result = bootstrap.verify(
                {"expectedReceiverVersion": "1.1.0"},
                {
                    "privateKeyPath": str(managed_key),
                    "publicKey": f"ssh-ed25519 {key_body} managed",
                },
            )

        self.assertTrue(result["restrictionsVerified"])
        managed_calls = [call for call in calls if str(managed_key) in call]
        self.assertGreaterEqual(len(managed_calls), 3)
        self.assertTrue(all(str(bootstrap_key) not in call for call in managed_calls))
        forwarding_call = next(call for call in managed_calls if "-R" in call)
        self.assertNotIn("ClearAllForwardings=yes", forwarding_call)
        self.assertIn("ExitOnForwardFailure=yes", forwarding_call)

    def test_native_update_retains_exact_backups_until_commit_or_rollback(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object):
            calls.append(command)
            joined = " ".join(command)
            if "mktemp -d" in joined:
                output = "/tmp/ssh-mixer-setup.ABC123\n"
            elif "sha256sum --" in joined:
                output = f"{setup_digest}  setup\n{receiver_digest}  receiver\n"
            elif "setup-v1.sh apply" in joined:
                output = '{"schemaVersion":1,"ok":true,"companionVersion":"1.1.0"}\n'
            else:
                output = ""
            return subprocess.CompletedProcess(command, 0, output, "")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            setup = root / "setup-v1.sh"
            receiver = root / "receiver.py"
            public = root / "managed.pub"
            setup.write_text("setup", encoding="utf-8")
            receiver.write_text("receiver", encoding="utf-8")
            public.write_text("ssh-ed25519 ZmFrZQ== managed", encoding="utf-8")
            setup_digest = hashlib.sha256(setup.read_bytes()).hexdigest()
            receiver_digest = hashlib.sha256(receiver.read_bytes()).hexdigest()
            plan = build_linux_plan(
                {
                    "platform": "linux",
                    "user": "listener",
                    "home": "/home/listener",
                    "commands": {"python3": True, "ffplay": True},
                }
            )
            plan.update(
                {
                    "setupSha256": setup_digest,
                    "receiverSha256": receiver_digest,
                    "previousReceiverVersion": "1.0.0",
                }
            )
            bootstrap = LinuxBootstrap(
                {"type": "direct", "host": "receiver.example", "user": "listener", "port": 22},
                known_hosts=root / "known_hosts",
                runner=runner,
            )

            result = bootstrap.apply(
                plan,
                {"publicKeyPath": str(public)},
                setup_path=setup,
                receiver_path=receiver,
                retain_transaction=True,
            )
            rolled_back = bootstrap.rollback(plan, {})

        self.assertTrue(result["ok"])
        self.assertTrue(rolled_back["complete"])
        self.assertEqual(rolled_back["restoredVersion"], "1.0.0")
        commands = "\n".join(" ".join(call) for call in calls)
        self.assertIn("receiver.backup", commands)
        self.assertIn("authorized_keys.backup", commands)
        self.assertIn("install -m 755", commands)
        update_apply = next(call for call in calls if "setup-v1.sh apply" in " ".join(call))
        self.assertIn("-T", update_apply)
        self.assertNotIn("-tt", update_apply)

    def test_removal_uses_the_managed_identity_and_requires_verified_receiver_result(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object):
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "ok": True,
                        "keyRevoked": True,
                        "helperRemoved": False,
                    }
                ),
                "",
            )

        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "id_ed25519"
            key.write_text("private", encoding="utf-8")
            key.chmod(0o600)
            bootstrap_key = Path(temp) / "bootstrap_ed25519"
            bootstrap_key.write_text("bootstrap", encoding="utf-8")
            bootstrap_key.chmod(0o600)
            bootstrap = LinuxBootstrap(
                {
                    "type": "direct",
                    "host": "receiver.example",
                    "user": "listener",
                    "port": 22,
                },
                known_hosts=Path(temp) / "known_hosts",
                bootstrap_key_path=str(bootstrap_key),
                runner=runner,
            )
            result = bootstrap.remove(
                {
                    "privateKeyPath": str(key),
                    "publicKeyBody": "bWFuYWdlZA==",
                }
            )

        self.assertTrue(result["verified"])
        self.assertFalse(result["helperRemoved"])
        self.assertIn(str(key), calls[0])
        self.assertNotIn(str(bootstrap_key), calls[0])
        self.assertEqual(calls[0][-1], "ssh-mixer-receiver v1 remove")

    def test_plan_explains_trusted_package_changes_and_rejects_root(self) -> None:
        plan = build_linux_plan(
            {
                "platform": "linux",
                "user": "listener",
                "home": "/home/listener",
                "commands": {"python3": True, "ffplay": False, "apt-get": True},
            }
        )
        self.assertEqual(plan["schemaVersion"], 1)
        self.assertEqual(plan["packageManager"], "apt-get")
        self.assertEqual(plan["packages"], ["ffmpeg"])
        self.assertTrue(any(change["requiresPrivilege"] for change in plan["changes"]))
        self.assertFalse(plan["changesApplied"])
        dependency_plan = build_linux_plan(
            {
                "platform": "linux",
                "user": "listener",
                "home": "/home/listener",
                "commands": {"python3": False, "ffplay": False, "dnf": True},
            }
        )
        self.assertEqual(dependency_plan["packages"], ["python3", "ffmpeg"])
        self.assertEqual(
            dependency_plan["packageCommand"],
            ["sudo", "dnf", "install", "-y", "--", "python3", "ffmpeg"],
        )
        with self.assertRaises(SetupError):
            build_linux_plan(
                {
                    "platform": "linux",
                    "user": "root",
                    "home": "/root",
                    "commands": {"python3": True, "ffplay": True},
                }
            )

    def test_authorized_key_is_forced_and_all_ssh_features_are_disabled(self) -> None:
        entry = authorized_key_entry(
            "/home/listener/.local/lib/ssh-mixer/ssh-mixer-receiver-v1.py",
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

    def test_tracer_requires_unchanged_approval_and_reports_incomplete_rollback(self) -> None:
        calls: list[str] = []
        tracer = LinuxSetupTracer(
            apply=lambda _plan, _identity: calls.append("apply") or {"ok": True},
            verify=lambda _plan, _identity: calls.append("verify")
            or {"ok": False, "error": "restrictions missing"},
            rollback=lambda _plan, _identity: calls.append("rollback")
            or {"ok": False, "complete": False},
        )
        plan = tracer.trace_plan(
            {
                "platform": "linux",
                "user": "listener",
                "home": "/home/listener",
                "commands": {"python3": True, "ffplay": True},
            }
        )
        with self.assertRaises(SetupError):
            tracer.execute(plan, {}, approved_plan_hash="changed")
        self.assertEqual(calls, [])

        result = tracer.execute(plan, {}, approved_plan_hash=plan["planHash"])

        self.assertEqual(calls, ["apply", "verify", "rollback"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["rollbackIncomplete"])
        self.assertEqual(result["rollback"], "incomplete")

    def test_transparent_setup_avoids_download_and_pipe_and_has_rollback_reporting(self) -> None:
        script = SETUP_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(script, r"\b(curl|wget)\b")
        self.assertIn("rollback", script)
        self.assertIn("ROLLBACK_INCOMPLETE", script)
        self.assertIn("apt-get", script)
        self.assertIn("dnf", script)
        self.assertIn("pacman", script)


class LinuxApplicationTracerTest(unittest.TestCase):
    def test_application_plans_approves_generates_verifies_and_persists_identity(self) -> None:
        class Bootstrap:
            def probe(self):
                return {
                    "platform": "linux",
                    "user": "listener",
                    "home": "/home/listener",
                    "commands": {"python3": True, "ffplay": True},
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
            "host": "receiver.example",
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
                bootstrap_factory=lambda _connection, _address: Bootstrap(),
                identity_store=ManagedIdentityStore(Path(temp) / "keys", runner=keygen),
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            planned = app.execute(
                {"operation": "receiver.linux-plan", "payload": {"connection": connection}}
            )
            rejected = app.execute(
                {"operation": "receiver.linux-setup", "payload": {"connection": connection}}
            )
            completed = app.execute(
                {
                    "operation": "receiver.linux-setup",
                    "payload": {
                        "connection": connection,
                        "changesApproved": True,
                        "approvedPlanHash": planned["plan"]["planHash"],
                        "encryptedIdentity": False,
                    },
                }
            )

        self.assertTrue(planned["ok"])
        self.assertFalse(rejected["ok"])
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["connection"]["securityLevel"], "receiver-only")
        self.assertTrue(completed["setup"]["verified"])


class ReceiverProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receiver = load_receiver()

    def test_only_versioned_approved_operations_are_accepted(self) -> None:
        request = self.receiver.parse_operation("ssh-mixer-receiver v1 capabilities")
        self.assertEqual(request.operation, "capabilities")
        for command in (
            "bash",
            "ssh-mixer-receiver v2 capabilities",
            "ssh-mixer-receiver v1 shell",
            "ssh-mixer-receiver v1 play extra",
            "ssh-mixer-receiver v1 quiet-test --dbfs nope",
        ):
            with self.assertRaises(self.receiver.ProtocolError):
                self.receiver.parse_operation(command)

    def test_playback_uses_external_clock_for_continuous_correction(self) -> None:
        with patch.object(self.receiver.shutil, "which", return_value="/usr/bin/ffplay"), patch.object(
            self.receiver.os, "execv", side_effect=RuntimeError("exec captured")
        ) as execute:
            with self.assertRaisesRegex(RuntimeError, "exec captured"):
                self.receiver.play()

        command = execute.call_args.args[1]
        self.assertEqual(command[command.index("-sync") + 1], "ext")
        self.assertEqual(command[command.index("-f") + 1], "ogg")

    def test_receiver_test_is_short_faded_bounded_and_selectable_by_one_db(self) -> None:
        settings = self.receiver.quiet_test_settings(-32, previous_dbfs=None)
        self.assertEqual(settings["dbfs"], -32)
        self.assertLessEqual(settings["durationSeconds"], 1.0)
        self.assertGreater(settings["fadeInSeconds"], 0)
        self.assertGreater(settings["fadeOutSeconds"], 0)
        self.assertFalse(settings["loop"])
        self.assertFalse(settings["changesSystemVolume"])
        self.receiver.quiet_test_settings(-17, previous_dbfs=-32)
        self.receiver.quiet_test_settings(0, previous_dbfs=-17)
        with self.assertRaises(self.receiver.ProtocolError):
            self.receiver.quiet_test_settings(-41, previous_dbfs=None)
        with self.assertRaises(self.receiver.ProtocolError):
            self.receiver.quiet_test_settings(1, previous_dbfs=0)

    def test_unknown_forced_command_fails_without_running_it(self) -> None:
        completed = subprocess.run(
            [str(RECEIVER_PATH), "--forced"],
            env={**os.environ, "SSH_ORIGINAL_COMMAND": "touch /tmp/not-allowed"},
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        error = json.loads(completed.stderr)
        self.assertEqual(error["code"], "protocol-rejected")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.config import config_path, keys_dir, load_config, logs_dir, save_config
from ssh_mixer.connections import TrustStore, connection_id
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.identity import ManagedIdentityStore
from ssh_mixer.session import SessionError, require_no_pending_removal


class RemovalApplicationTest(unittest.TestCase):
    @contextmanager
    def environment(self, root: str):
        with patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(root) / "config"),
                "XDG_DATA_HOME": str(Path(root) / "data"),
                "XDG_STATE_HOME": str(Path(root) / "state"),
                "XDG_RUNTIME_DIR": str(Path(root) / "runtime"),
            },
            clear=False,
        ):
            yield

    def configured_receiver(self, root: str) -> tuple[dict[str, object], str]:
        receiver_id = "a" * 64
        directory = keys_dir() / receiver_id
        directory.mkdir(parents=True, mode=0o700)
        private_path = directory / "id_ed25519"
        public_path = directory / "id_ed25519.pub"
        key_material = base64.b64encode(b"managed-removal-key").decode("ascii")
        private_path.write_text("private-key", encoding="utf-8")
        private_path.chmod(0o600)
        public_path.write_text(f"ssh-ed25519 {key_material} ssh-mixer:test\n", encoding="utf-8")
        public_path.chmod(0o644)
        connection: dict[str, object] = {
            "type": "direct",
            "host": "receiver.example",
            "user": "listener",
            "port": 22,
            "securityLevel": "receiver-only",
            "managedIdentityId": receiver_id,
            "receiverPlatform": "linux",
            "receiverProtocol": "v1",
        }
        save_config(
            {
                "connection": connection,
                "remote": {"keyPath": str(private_path)},
                "mixProfiles": [
                    {
                        "schemaVersion": 1,
                        "id": "living-room",
                        "name": "Living room",
                        "connection": connection,
                        "routeMode": "ssh",
                        "sourceMatchers": [],
                        "privacy": {"lockBehavior": "stop-all", "showReceiverLabel": False},
                        "stream": {"bitrate": "128k", "connectTimeoutSeconds": 5},
                    }
                ],
            }
        )
        return connection, key_material

    def trusted_store(self, root: str, connection: dict[str, object]) -> TrustStore:
        trust = TrustStore(Path(root) / "trust")
        host_key = base64.b64encode(b"receiver-host-key").decode("ascii")
        trust.approve(connection, [f"receiver.example ssh-ed25519 {host_key}"])
        return trust

    def app(
        self,
        root: str,
        connection: dict[str, object],
        *,
        remote_remove,
        plugin_remove=lambda _command: {"ok": True, "removed": True},
        active: bool = False,
        identity_store: ManagedIdentityStore | None = None,
    ) -> MixerApplication:
        return MixerApplication(
            read_status=lambda: {"state": "running" if active else "stopped", "active": active},
            remote_remove=remote_remove,
            plugin_remove=plugin_remove,
            identity_store=identity_store or ManagedIdentityStore(keys_dir()),
            trust_store=self.trusted_store(root, connection),
            diagnostic_store=DiagnosticStore(logs_dir()),
        )

    def test_reachable_receiver_is_revoked_before_local_state_is_verified_deleted(self) -> None:
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            connection, key_material = self.configured_receiver(temp)
            app = self.app(
                temp,
                connection,
                remote_remove=lambda request: calls.append(request) or {
                    "ok": True,
                    "verified": True,
                    "keyRevoked": True,
                    "helperRemoved": True,
                },
            )
            app.execute(
                {
                    "operation": "diagnostics.verbose-next",
                    "payload": {},
                }
            )
            plan_result = app.execute({"operation": "removal.plan", "payload": {}})
            plan = plan_result["plan"]
            rejected = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {"plan": plan, "approvedPlanHash": "wrong"},
                }
            )
            calls_before_approval = list(calls)
            applied = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {"plan": plan, "approvedPlanHash": plan["planHash"]},
                }
            )
            configured = load_config()
            key_root_exists = (keys_dir() / ("a" * 64)).exists()
            trust_files = list((Path(temp) / "trust").glob("*.json"))
            diagnostic_files = list(logs_dir().glob("*"))

        self.assertFalse(rejected["ok"])
        self.assertEqual(calls_before_approval, [])
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["revocation"], "verified")
        self.assertTrue(applied["helperRemoved"])
        self.assertEqual(calls[0]["publicKeyBody"], key_material)
        self.assertIsNone(configured["connection"])
        self.assertEqual(configured["mixProfiles"], [])
        self.assertFalse(key_root_exists)
        self.assertEqual(trust_files, [])
        self.assertEqual(diagnostic_files, [])
        self.assertTrue(all(applied["localCleanup"].values()))

    def test_offline_receiver_stays_pending_without_deleting_retry_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            connection, key_material = self.configured_receiver(temp)
            app = self.app(
                temp,
                connection,
                remote_remove=lambda _request: {
                    "ok": False,
                    "verified": False,
                    "code": "receiver-offline",
                },
            )
            plan = app.execute({"operation": "removal.plan", "payload": {}})["plan"]
            result = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {"plan": plan, "approvedPlanHash": plan["planHash"]},
                }
            )
            pending = app.execute({"operation": "removal.inspect", "payload": {}})
            pending_path = Path(temp) / "state" / "ssh-mixer" / "pending-removals.json"
            pending_mode = pending_path.stat().st_mode & 0o777
            still_configured = load_config()["connection"]
            key_exists = (keys_dir() / ("a" * 64) / "id_ed25519").is_file()
            with self.assertRaises(SessionError):
                require_no_pending_removal()

        self.assertFalse(result["ok"])
        self.assertNotIn(key_material, json.dumps(result))
        self.assertNotIn("id_ed25519", json.dumps(result))
        self.assertEqual(result["revocation"], "pending")
        self.assertEqual(result["diagnostic"]["code"], "receiver-offline")
        self.assertEqual(pending["pendingCount"], 1)
        self.assertEqual(pending_mode, 0o600)
        self.assertIsNotNone(still_configured)
        self.assertTrue(key_exists)

    def test_partial_local_cleanup_retries_without_repeating_verified_revocation(self) -> None:
        remote_calls: list[dict[str, object]] = []

        class FailingIdentityStore(ManagedIdentityStore):
            def revoke_local(self, receiver_id: str) -> bool:
                raise OSError("simulated local deletion failure")

        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            connection, _key_material = self.configured_receiver(temp)

            def remote_remove(request):
                remote_calls.append(request)
                return {
                    "ok": True,
                    "verified": True,
                    "keyRevoked": True,
                    "helperRemoved": True,
                }

            failing_app = self.app(
                temp,
                connection,
                remote_remove=remote_remove,
                identity_store=FailingIdentityStore(keys_dir()),
            )
            first_plan = failing_app.execute(
                {"operation": "removal.plan", "payload": {}}
            )["plan"]
            partial = failing_app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {
                        "plan": first_plan,
                        "approvedPlanHash": first_plan["planHash"],
                    },
                }
            )
            retry_app = self.app(temp, connection, remote_remove=remote_remove)
            retry_plan = retry_app.execute(
                {"operation": "removal.plan", "payload": {}}
            )["plan"]
            completed = retry_app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {
                        "plan": retry_plan,
                        "approvedPlanHash": retry_plan["planHash"],
                    },
                }
            )

        self.assertFalse(partial["ok"])
        self.assertEqual(partial["diagnostic"]["code"], "local-cleanup-incomplete")
        self.assertTrue(partial["receivers"][0]["remoteCleanupVerified"])
        self.assertTrue(completed["ok"])
        self.assertEqual(len(remote_calls), 1)

    def test_active_session_defers_without_stopping_or_removing_anything(self) -> None:
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            connection, _key_material = self.configured_receiver(temp)
            app = self.app(
                temp,
                connection,
                active=True,
                remote_remove=lambda request: calls.append(request) or {"ok": True},
            )
            plan = app.execute({"operation": "removal.plan", "payload": {}})["plan"]
            result = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {"plan": plan, "approvedPlanHash": plan["planHash"]},
                }
            )
            key_exists = (keys_dir() / ("a" * 64) / "id_ed25519").is_file()

        self.assertFalse(result["ok"])
        self.assertTrue(result["deferred"])
        self.assertEqual(calls, [])
        self.assertTrue(key_exists)

    def test_abandonment_requires_exact_informed_confirmation_and_is_not_revocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            connection, _key_material = self.configured_receiver(temp)
            app = self.app(
                temp,
                connection,
                remote_remove=lambda _request: {
                    "ok": False,
                    "verified": False,
                    "code": "receiver-offline",
                },
            )
            plan = app.execute({"operation": "removal.plan", "payload": {}})["plan"]
            too_early = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {
                        "plan": plan,
                        "approvedPlanHash": plan["planHash"],
                        "abandonPending": True,
                        "abandonmentConfirmation": "ABANDON WITHOUT VERIFIED REVOCATION",
                    },
                }
            )
            app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {"plan": plan, "approvedPlanHash": plan["planHash"]},
                }
            )
            pending_plan = app.execute(
                {"operation": "removal.plan", "payload": {}}
            )["plan"]
            blocked = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {
                        "plan": pending_plan,
                        "approvedPlanHash": pending_plan["planHash"],
                        "abandonPending": True,
                        "abandonmentConfirmation": "yes",
                    },
                }
            )
            abandoned = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {
                        "plan": pending_plan,
                        "approvedPlanHash": pending_plan["planHash"],
                        "abandonPending": True,
                        "abandonmentConfirmation": "ABANDON WITHOUT VERIFIED REVOCATION",
                    },
                }
            )

        self.assertFalse(too_early["ok"])
        self.assertEqual(too_early["diagnostic"]["code"], "cleanup-attempt-required")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["diagnostic"]["code"], "confirmation-required")
        self.assertTrue(abandoned["ok"])
        self.assertEqual(abandoned["revocation"], "abandoned-not-revoked")
        self.assertFalse(abandoned["remoteCleanupVerified"])

    def test_full_uninstall_does_not_invoke_omarchy_while_cleanup_is_pending(self) -> None:
        plugin_calls: list[list[str]] = []
        state_absent_before_plugin: list[bool] = []
        online = False

        def remote_remove(_request):
            return (
                {"ok": True, "verified": True, "keyRevoked": True, "helperRemoved": True}
                if online
                else {"ok": False, "verified": False, "code": "receiver-offline"}
            )

        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            connection, _key_material = self.configured_receiver(temp)

            def plugin_remove(command: list[str]):
                plugin_calls.append(command)
                state_absent_before_plugin.append(
                    all(
                        not path.exists()
                        for path in (
                            Path(temp) / "config" / "ssh-mixer",
                            Path(temp) / "data" / "ssh-mixer",
                            Path(temp) / "state" / "ssh-mixer",
                            Path(temp) / "runtime" / "ssh-mixer",
                        )
                    )
                )
                return {"ok": True, "removed": True}

            app = self.app(
                temp,
                connection,
                remote_remove=remote_remove,
                plugin_remove=plugin_remove,
            )
            plan = app.execute({"operation": "uninstall.plan", "payload": {}})["plan"]
            planned_receivers = list(plan["receivers"])
            pending = app.execute(
                {
                    "operation": "uninstall.apply",
                    "payload": {"plan": plan, "approvedPlanHash": plan["planHash"]},
                }
            )
            online = True
            retry_plan = app.execute({"operation": "uninstall.plan", "payload": {}})["plan"]
            removed = app.execute(
                {
                    "operation": "uninstall.apply",
                    "payload": {
                        "plan": retry_plan,
                        "approvedPlanHash": retry_plan["planHash"],
                    },
                }
            )

        self.assertEqual(len(planned_receivers), 1)
        self.assertEqual(planned_receivers[0]["label"], "listener@receiver.example")
        self.assertFalse(pending["ok"])
        self.assertEqual(plugin_calls, [["omarchy-plugin-remove", "jabaiwho.ssh-mixer", "--yes"]])
        self.assertEqual(state_absent_before_plugin, [True])
        self.assertTrue(removed["ok"])
        self.assertTrue(removed["pluginRemoved"])
        self.assertTrue(removed["localSensitiveStateRemoved"])

    def test_plugin_removal_failure_does_not_recreate_deleted_sensitive_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            save_config({})
            app = MixerApplication(
                read_status=lambda: {"state": "stopped", "active": False},
                remote_remove=lambda _request: {"ok": True},
                plugin_remove=lambda _command: {"ok": False, "removed": False},
                diagnostic_store=DiagnosticStore(logs_dir()),
            )
            plan = app.execute({"operation": "uninstall.plan", "payload": {}})["plan"]
            result = app.execute(
                {
                    "operation": "uninstall.apply",
                    "payload": {"plan": plan, "approvedPlanHash": plan["planHash"]},
                }
            )
            sensitive_roots_absent = all(
                not path.exists()
                for path in (
                    Path(temp) / "config" / "ssh-mixer",
                    Path(temp) / "data" / "ssh-mixer",
                    Path(temp) / "state" / "ssh-mixer",
                    Path(temp) / "runtime" / "ssh-mixer",
                )
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["diagnostic"]["code"], "plugin-removal-failed")
        self.assertTrue(result["localSensitiveStateRemoved"])
        self.assertTrue(sensitive_roots_absent)

    def test_user_managed_identity_is_never_deleted_or_reported_as_revoked(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            external_key = Path(temp) / "user-owned-key"
            external_key.write_text("user-owned", encoding="utf-8")
            external_key.chmod(0o600)
            connection = {
                "type": "direct",
                "host": "receiver.example",
                "user": "listener",
                "port": 22,
                "securityLevel": "user-managed",
            }
            save_config({"connection": connection, "remote": {"keyPath": str(external_key)}})
            app = self.app(temp, connection, remote_remove=lambda _request: {"ok": True})
            plan = app.execute({"operation": "removal.plan", "payload": {}})["plan"]
            app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {"plan": plan, "approvedPlanHash": plan["planHash"]},
                }
            )
            pending_plan = app.execute(
                {"operation": "removal.plan", "payload": {}}
            )["plan"]
            result = app.execute(
                {
                    "operation": "removal.apply",
                    "payload": {
                        "plan": pending_plan,
                        "approvedPlanHash": pending_plan["planHash"],
                        "abandonPending": True,
                        "abandonmentConfirmation": "ABANDON WITHOUT VERIFIED REVOCATION",
                    },
                }
            )
            external_key_exists = external_key.is_file()

        self.assertTrue(result["ok"])
        self.assertEqual(result["revocation"], "abandoned-not-revoked")
        self.assertTrue(external_key_exists)


class CompanionRemovalArtifactTest(unittest.TestCase):
    def test_linux_companion_removes_only_the_selected_key_and_unshared_state(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = root / "receiver" / "linux" / "setup-v1.sh"
        key_body = base64.b64encode(b"remove-this-key").decode("ascii")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            authorized_keys = home / ".ssh" / "authorized_keys"
            receiver = home / ".local" / "lib" / "ssh-mixer" / "ssh-mixer-receiver-v1.py"
            state = home / ".local" / "state" / "ssh-mixer" / "quiet-test-v1.json"
            authorized_keys.parent.mkdir(parents=True)
            receiver.parent.mkdir(parents=True)
            state.parent.mkdir(parents=True)
            authorized_keys.write_text(
                "ssh-ed25519 unrelated user-owned\n"
                + (
                    f'command="helper --forced --key {key_body}" ssh-ed25519 '
                    f"{key_body} ssh-mixer-managed-v1\n"
                ),
                encoding="utf-8",
            )
            receiver.write_text("helper", encoding="utf-8")
            state.write_text("{}", encoding="utf-8")
            completed = subprocess.run(
                [str(script), "remove", key_body],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "XDG_STATE_HOME": str(home / ".local" / "state"),
                },
            )
            result = json.loads(completed.stdout)
            retained = authorized_keys.read_text(encoding="utf-8")
            receiver_exists = receiver.exists()
            state_exists = state.exists()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(result["keyRevoked"])
        self.assertTrue(result["helperRemoved"])
        self.assertEqual(retained, "ssh-ed25519 unrelated user-owned\n")
        self.assertFalse(receiver_exists)
        self.assertFalse(state_exists)

    def test_linux_receiver_self_removal_is_fixed_key_specific_and_verified(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "receiver"
            / "linux"
            / "ssh-mixer-receiver-v1.py"
        )
        key_body = base64.b64encode(b"self-remove-key").decode("ascii")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            helper = home / ".local" / "lib" / "ssh-mixer" / source.name
            authorized_keys = home / ".ssh" / "authorized_keys"
            helper.parent.mkdir(parents=True)
            authorized_keys.parent.mkdir(parents=True)
            shutil.copy2(source, helper)
            helper.chmod(0o700)
            authorized_keys.write_text(
                (
                    f'command="{helper} --forced --key {key_body}",restrict '
                    f"ssh-ed25519 {key_body} ssh-mixer-managed-v1\n"
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [str(helper), "--forced", "--key", key_body],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "XDG_STATE_HOME": str(home / ".local" / "state"),
                    "SSH_ORIGINAL_COMMAND": "ssh-mixer-receiver v1 remove",
                },
            )
            result = json.loads(completed.stdout)
            retained = authorized_keys.read_text(encoding="utf-8")
            helper_exists = helper.exists()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(result["keyRevoked"])
        self.assertTrue(result["helperRemoved"])
        self.assertEqual(retained, "")
        self.assertFalse(helper_exists)

    def test_shared_linux_helper_remains_for_another_managed_identity(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "receiver"
            / "linux"
            / "ssh-mixer-receiver-v1.py"
        )
        key_body = base64.b64encode(b"first-managed-key").decode("ascii")
        other_body = base64.b64encode(b"second-managed-key").decode("ascii")
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            helper = home / ".local" / "lib" / "ssh-mixer" / source.name
            authorized_keys = home / ".ssh" / "authorized_keys"
            helper.parent.mkdir(parents=True)
            authorized_keys.parent.mkdir(parents=True)
            shutil.copy2(source, helper)
            helper.chmod(0o700)
            authorized_keys.write_text(
                (
                    f'command="{helper} --forced --key {key_body}" ssh-ed25519 '
                    f"{key_body} ssh-mixer-managed-v1\n"
                    f'command="{helper} --forced --key {other_body}" ssh-ed25519 '
                    f"{other_body} ssh-mixer-managed-v1\n"
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [str(helper), "--forced", "--key", key_body],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "XDG_STATE_HOME": str(home / ".local" / "state"),
                    "SSH_ORIGINAL_COMMAND": "ssh-mixer-receiver v1 remove",
                },
            )
            result = json.loads(completed.stdout)
            retained = authorized_keys.read_text(encoding="utf-8")
            helper_exists = helper.is_file()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(result["keyRevoked"])
        self.assertFalse(result["helperRemoved"])
        self.assertNotIn(key_body, retained)
        self.assertIn(other_body, retained)
        self.assertTrue(helper_exists)

    def test_all_platform_companions_support_key_specific_verified_removal(self) -> None:
        root = Path(__file__).resolve().parents[1] / "receiver"
        linux = (root / "linux" / "setup-v1.sh").read_text(encoding="utf-8")
        windows = (root / "windows" / "setup-v1.ps1").read_text(encoding="utf-8")
        macos = (root / "macos" / "setup-v1.sh").read_text(encoding="utf-8")

        self.assertIn('MODE" == "remove"', linux)
        self.assertIn("'Remove'", windows)
        self.assertIn('[ "$MODE" = remove ]', macos)
        for artifact in (linux, windows, macos):
            self.assertIn("keyRevoked", artifact)
            self.assertIn("helperRemoved", artifact)

    def test_receiver_protocols_expose_fixed_self_removal_without_shell_dispatch(self) -> None:
        root = Path(__file__).resolve().parents[1] / "receiver"
        linux = (root / "linux" / "ssh-mixer-receiver-v1.py").read_text(encoding="utf-8")
        windows = (root / "windows" / "ssh-mixer-receiver-v1.ps1").read_text(encoding="utf-8")
        macos = (root / "macos" / "ssh-mixer-receiver-v1").read_text(encoding="utf-8")

        self.assertIn('operation == "remove"', linux)
        self.assertIn("'remove'", windows)
        self.assertIn('operation=remove', macos)
        self.assertNotIn("eval ", linux + windows + macos)


if __name__ == "__main__":
    unittest.main()

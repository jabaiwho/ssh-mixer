from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ssh_mixer.application import MixerApplication
from ssh_mixer.config import config_path, load_config, secure_write_text
from ssh_mixer.connections import TrustStore
from ssh_mixer.diagnostics import DiagnosticStore
from ssh_mixer.migration import MigrationService
from ssh_mixer.session import SessionError, require_migration_complete


LEGACY = {
    "sourceIds": ["sink-input:407", "source:alsa_input.secret-mic"],
    "destination": "both",
    "remote": {
        "host": "legacy-receiver.example",
        "user": "legacy-user",
        "keyPath": "~/.ssh/legacy-key",
        "port": 2222,
        "bitrate": "160k",
        "receiverCommand": "ffplay -f ogg -",
        "connectTimeoutSeconds": 7,
    },
}


class MigrationServiceTest(unittest.TestCase):
    def test_application_exposes_detection_plan_and_exact_approved_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            self.seed()
            app = MixerApplication(
                read_status=lambda: {"state": "stopped", "active": False},
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            inspected = app.execute({"operation": "migration.inspect"})
            planned = app.execute(
                {
                    "operation": "migration.plan",
                    "payload": {"choice": "start-fresh"},
                }
            )
            rejected = app.execute(
                {
                    "operation": "migration.apply",
                    "payload": {
                        "plan": planned["plan"],
                        "approvedPlanHash": "different",
                    },
                }
            )
            applied = app.execute(
                {
                    "operation": "migration.apply",
                    "payload": {
                        "plan": planned["plan"],
                        "approvedPlanHash": planned["plan"]["planHash"],
                    },
                }
            )

        self.assertTrue(inspected["detected"])
        self.assertNotIn("legacy-receiver.example", json.dumps(inspected))
        self.assertTrue(planned["ok"])
        self.assertFalse(rejected["ok"])
        self.assertTrue(applied["ok"])
        self.assertIsNone(applied["migration"]["config"]["connection"])

    def environment(self, temp: str):
        return patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_DATA_HOME": str(Path(temp) / "data"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(temp) / "runtime"),
            },
            clear=False,
        )

    def seed(self) -> str:
        raw = json.dumps(LEGACY, indent=2) + "\n"
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text(raw, encoding="utf-8")
        return raw

    def test_unrelated_configuration_write_cannot_silently_retire_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            original = self.seed()
            app = MixerApplication(
                read_status=lambda: {"state": "stopped", "active": False},
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            blocked = app.execute(
                {
                    "operation": "configure",
                    "payload": {"privacy": {"lockBehavior": "continue-playback"}},
                }
            )
            unchanged = config_path().read_text(encoding="utf-8")

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["diagnostic"]["code"], "migration-required")
        self.assertEqual(unchanged, original)

    def test_application_requires_host_fingerprint_approval_before_retention(self) -> None:
        encoded = base64.b64encode(b"migration-host-key").decode("ascii")
        host_key = f"receiver.example ssh-ed25519 {encoded}"
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            original = self.seed()
            app = MixerApplication(
                read_status=lambda: {"state": "stopped", "active": False},
                scan_host_keys=lambda *_args, **_kwargs: [host_key],
                trust_store=TrustStore(Path(temp) / "trust"),
                diagnostic_store=DiagnosticStore(Path(temp) / "logs"),
            )
            planned = app.execute(
                {
                    "operation": "migration.plan",
                    "payload": {"choice": "keep-user-managed"},
                }
            )
            blocked = app.execute(
                {
                    "operation": "migration.apply",
                    "payload": {
                        "plan": planned["plan"],
                        "approvedPlanHash": planned["plan"]["planHash"],
                    },
                }
            )
            unchanged = config_path().read_text(encoding="utf-8")

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["diagnostic"]["code"], "trust-required")
        self.assertEqual(unchanged, original)

    def test_detects_legacy_reasons_without_returning_or_logging_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            self.seed()
            inspected = MigrationService().inspect()
            serialized = json.dumps(inspected, sort_keys=True)

        self.assertTrue(inspected["detected"])
        self.assertEqual(
            set(inspected["reasons"]),
            {
                "schema-before-v2",
                "temporary-source-ids",
                "arbitrary-receiver-command",
                "implicit-legacy-connection",
            },
        )
        for private in (
            "legacy-receiver.example",
            "legacy-user",
            "legacy-key",
            "sink-input:407",
            "secret-mic",
            "ffplay",
        ):
            self.assertNotIn(private, serialized)

    def test_all_three_explicit_choices_are_planned_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            original = self.seed()
            service = MigrationService()
            plans = {
                choice: service.plan(choice, platform="linux")
                for choice in ("import-secure", "keep-user-managed", "start-fresh")
            }
            unchanged = config_path().read_text(encoding="utf-8")

        self.assertEqual(unchanged, original)
        self.assertEqual(set(plans), {"import-secure", "keep-user-managed", "start-fresh"})
        self.assertTrue(all(plan["requiresApproval"] for plan in plans.values()))
        self.assertTrue(all(plan["planHash"] for plan in plans.values()))

    def test_new_session_start_is_blocked_until_legacy_choice_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            self.seed()
            with self.assertRaises(SessionError):
                require_migration_complete()

    def test_active_session_defers_without_backup_or_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            self.seed()
            service = MigrationService(
                status_reader=lambda: {"state": "streaming", "active": True}
            )
            plan = service.plan("start-fresh")
            result = service.execute(plan, approved_plan_hash=plan["planHash"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "session-active")
        self.assertTrue(result["deferred"])
        self.assertFalse(service.backup_path.exists())

    def test_import_secures_and_verifies_identity_before_retiring_legacy(self) -> None:
        callback_observations: list[dict] = []
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            self.seed()
            service = MigrationService()
            plan = service.plan("import-secure", platform="linux")

            def secure_import(platform: str, connection: dict, payload: dict) -> dict:
                callback_observations.append(
                    {
                        "platform": platform,
                        "backupExists": service.backup_path.is_file(),
                        "connection": connection,
                        "bootstrapKeyPath": payload["_legacyBootstrapKeyPath"],
                    }
                )
                managed = dict(
                    connection,
                    securityLevel="receiver-only",
                    managedIdentityId="a" * 64,
                    receiverPlatform="linux",
                )
                return {
                    "ok": True,
                    "verified": True,
                    "connection": managed,
                    "config": {
                        "schemaVersion": 2,
                        "connection": managed,
                        "destination": "both",
                        "sourceMatchers": [],
                        "remote": {
                            "host": connection["host"],
                            "user": connection["user"],
                            "port": connection["port"],
                            "keyPath": str(Path(temp) / "managed-key"),
                            "bitrate": "160k",
                        },
                    },
                }

            result = service.execute(
                plan,
                approved_plan_hash=plan["planHash"],
                secure_import=secure_import,
            )
            persisted = config_path().read_text(encoding="utf-8")
            loaded = load_config()

        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertTrue(callback_observations[0]["backupExists"])
        self.assertEqual(callback_observations[0]["platform"], "linux")
        self.assertEqual(callback_observations[0]["bootstrapKeyPath"], "~/.ssh/legacy-key")
        self.assertEqual(loaded["connection"]["securityLevel"], "receiver-only")
        self.assertNotIn("sourceIds", persisted)
        self.assertNotIn("ffplay", persisted)
        self.assertFalse(service.backup_path.exists())

    def test_keep_user_managed_removes_unsafe_legacy_state_but_retains_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            self.seed()
            service = MigrationService()
            plan = service.plan("keep-user-managed")
            result = service.execute(plan, approved_plan_hash=plan["planHash"])
            loaded = load_config()
            persisted = config_path().read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertEqual(loaded["connection"]["securityLevel"], "user-managed")
        self.assertEqual(loaded["remote"]["host"], "legacy-receiver.example")
        self.assertEqual(loaded["remote"]["keyPath"], "~/.ssh/legacy-key")
        self.assertNotIn("sourceIds", persisted)
        self.assertNotIn("ffplay", persisted)

    def test_start_fresh_can_transactionally_replace_malformed_legacy_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            config_path().parent.mkdir(parents=True, exist_ok=True)
            config_path().write_text('{"remote":', encoding="utf-8")
            service = MigrationService()
            plan = service.plan("start-fresh")
            result = service.execute(plan, approved_plan_hash=plan["planHash"])
            loaded = load_config()

        self.assertTrue(result["ok"])
        self.assertIsNone(loaded["connection"])

    def test_start_fresh_removes_every_legacy_receiver_and_source_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            self.seed()
            service = MigrationService()
            plan = service.plan("start-fresh")
            result = service.execute(plan, approved_plan_hash=plan["planHash"])
            loaded = load_config()

        self.assertTrue(result["ok"])
        self.assertIsNone(loaded["connection"])
        self.assertEqual(loaded["sourceMatchers"], [])
        self.assertEqual(loaded["remote"]["host"], "")

    def test_failed_migration_restores_exact_prior_config_and_keeps_protected_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.environment(temp):
            original = self.seed()
            service = MigrationService()
            plan = service.plan("import-secure", platform="windows")

            def failed_import(_platform: str, _connection: dict, _payload: dict) -> dict:
                secure_write_text(config_path(), '{"partiallyChanged":true}\n')
                return {"ok": False, "stage": "receiver.verify", "error": "verification failed"}

            result = service.execute(
                plan,
                approved_plan_hash=plan["planHash"],
                secure_import=failed_import,
            )
            restored = config_path().read_text(encoding="utf-8")
            backup_mode = service.backup_path.stat().st_mode & 0o777

        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "receiver.verify")
        self.assertEqual(result["rollback"], "complete")
        self.assertNotIn("legacy-receiver.example", json.dumps(result))
        self.assertNotIn("legacy-user", json.dumps(result))
        self.assertEqual(restored, original)
        self.assertEqual(backup_mode, 0o600)


if __name__ == "__main__":
    unittest.main()

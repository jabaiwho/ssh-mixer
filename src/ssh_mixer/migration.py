from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_CONFIG,
    DEFAULT_RECEIVER_COMMAND,
    config_dir,
    config_path,
    ensure_dirs,
    load_config,
    normalize_config,
    save_config,
    secure_write_text,
)
from .connections import normalize_connection
from .session import normalize_status

MIGRATION_SCHEMA_VERSION = 1
MIGRATION_CHOICES = {"import-secure", "keep-user-managed", "start-fresh"}
MIGRATION_PLATFORMS = {"linux", "windows", "macos"}
SecureImport = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
StatusReader = Callable[[], dict[str, Any]]


class MigrationError(ValueError):
    """Raised when legacy migration cannot proceed safely."""


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _plan_hash(plan: dict[str, Any]) -> str:
    unsigned = dict(plan)
    unsigned.pop("planHash", None)
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_raw() -> tuple[str, dict[str, Any] | None]:
    path = config_path()
    if path.is_symlink():
        raise MigrationError("legacy configuration path is unsafe")
    if not path.exists():
        return "", None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return "", None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    return raw, parsed if isinstance(parsed, dict) else None


def _legacy_reasons(raw: dict[str, Any] | None, *, exists: bool) -> list[str]:
    if not exists:
        return []
    if raw is None:
        return ["invalid-legacy-config"]
    reasons: list[str] = []
    try:
        schema = int(raw.get("schemaVersion", 0))
    except (TypeError, ValueError):
        schema = 0
    if schema < CONFIG_SCHEMA_VERSION:
        reasons.append("schema-before-v2")
    if "sourceIds" in raw:
        reasons.append("temporary-source-ids")
    remote = raw.get("remote", {})
    if isinstance(remote, dict):
        command = str(remote.get("receiverCommand", DEFAULT_RECEIVER_COMMAND))
        if command != DEFAULT_RECEIVER_COMMAND:
            reasons.append("arbitrary-receiver-command")
        if raw.get("connection") is None and remote.get("host") and remote.get("user"):
            reasons.append("implicit-legacy-connection")
    return reasons


def _legacy_connection(raw: dict[str, Any]) -> dict[str, Any]:
    remote = raw.get("remote", {})
    if not isinstance(remote, dict):
        raise MigrationError("legacy receiver details are incomplete")
    return normalize_connection(
        {
            "type": "direct",
            "host": remote.get("host", ""),
            "user": remote.get("user", ""),
            "port": remote.get("port", 22),
            "securityLevel": "user-managed",
        }
    )


class MigrationService:
    """Owns exact approval, protected backup, verification, and local rollback."""

    def __init__(self, *, status_reader: StatusReader = normalize_status) -> None:
        self.status_reader = status_reader
        self.backup_path = config_dir() / "legacy-backup.json"

    def inspect(self) -> dict[str, Any]:
        path = config_path()
        raw_text, raw = _read_raw()
        reasons = _legacy_reasons(raw, exists=path.exists())
        session_active = self.status_reader().get("active") is True
        return {
            "ok": True,
            "schemaVersion": MIGRATION_SCHEMA_VERSION,
            "detected": bool(reasons),
            "reasons": reasons,
            "choices": [
                "import-secure",
                "keep-user-managed",
                "start-fresh",
            ],
            "sessionActive": session_active,
            "deferred": bool(reasons) and session_active,
            "rollbackAvailable": self.backup_path.is_file(),
            "legacyConfigDigest": _digest(raw_text) if raw_text else "",
        }

    def legacy_connection(self) -> dict[str, Any]:
        _raw_text, raw = _read_raw()
        if raw is None or not _legacy_reasons(raw, exists=config_path().exists()):
            raise MigrationError("no usable legacy configuration was detected")
        return _legacy_connection(raw)

    def plan(self, choice: str, *, platform: str = "") -> dict[str, Any]:
        normalized_choice = str(choice).strip().lower()
        normalized_platform = str(platform).strip().lower()
        if normalized_choice not in MIGRATION_CHOICES:
            raise MigrationError("migration choice is invalid")
        if normalized_choice == "import-secure":
            if normalized_platform not in MIGRATION_PLATFORMS:
                raise MigrationError("Import and secure requires a supported Receiver platform")
        else:
            normalized_platform = ""
        raw_text, raw = _read_raw()
        reasons = _legacy_reasons(raw, exists=config_path().exists())
        if not reasons:
            raise MigrationError("no legacy configuration was detected")
        if normalized_choice != "start-fresh":
            if raw is None:
                raise MigrationError("only Start fresh is available for invalid legacy data")
            _legacy_connection(raw)
        changes = {
            "import-secure": [
                "Back up the exact legacy configuration in protected storage",
                "Establish and verify a dedicated receiver-only Managed Identity",
                (
                    "Remove the arbitrary receiver command and temporary source ids "
                    "only after verification"
                ),
            ],
            "keep-user-managed": [
                "Back up the exact legacy configuration in protected storage",
                "Retain the receiver and existing identity as explicitly user-managed",
                (
                    "Replace arbitrary commands with Receiver Protocol v1 and remove "
                    "temporary source ids"
                ),
            ],
            "start-fresh": [
                "Back up the exact legacy configuration in protected storage",
                "Remove legacy receiver, identity path, command, and temporary source choices",
                "Return to an unconfigured public-install state",
            ],
        }[normalized_choice]
        plan: dict[str, Any] = {
            "schemaVersion": MIGRATION_SCHEMA_VERSION,
            "choice": normalized_choice,
            "platform": normalized_platform,
            "legacyConfigDigest": _digest(raw_text),
            "legacyReasons": reasons,
            "changes": changes,
            "requiresApproval": True,
            "waitsForInactiveSession": True,
            "backupProtection": "0600-atomic",
        }
        plan["planHash"] = _plan_hash(plan)
        return plan

    def execute(
        self,
        plan: dict[str, Any],
        *,
        approved_plan_hash: str,
        secure_import: SecureImport | None = None,
        setup_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.status_reader().get("active") is True:
            return {
                "ok": False,
                "schemaVersion": MIGRATION_SCHEMA_VERSION,
                "stage": "session-active",
                "deferred": True,
                "error": "migration waits for the active Session to stop",
                "rollback": "not-required",
            }
        if not isinstance(plan, dict):
            raise MigrationError("migration plan must be an object")
        choice = str(plan.get("choice", ""))
        platform = str(plan.get("platform", ""))
        expected = self.plan(choice, platform=platform)
        if (
            expected != plan
            or approved_plan_hash != plan.get("planHash")
            or approved_plan_hash != _plan_hash(plan)
        ):
            raise MigrationError("exact unchanged migration plan approval is required")

        original_text, original = _read_raw()
        if (
            _digest(original_text) != plan["legacyConfigDigest"]
            or (original is None and choice != "start-fresh")
        ):
            raise MigrationError("legacy configuration changed before migration")
        self._prepare_backup(original_text)
        stage = "backup"
        secure_changes_committed = False
        try:
            if choice == "start-fresh":
                stage = "fresh-config-write"
                save_config(normalize_config(DEFAULT_CONFIG))
            elif choice == "keep-user-managed":
                if original is None:
                    raise MigrationError("legacy receiver details are unavailable")
                stage = "user-managed-conversion"
                save_config(self._user_managed_config(original))
            else:
                if original is None:
                    raise MigrationError("legacy receiver details are unavailable")
                stage = "receiver.setup"
                if secure_import is None:
                    raise MigrationError("verified Managed Identity setup is unavailable")
                internal_setup_payload = dict(setup_payload or {})
                remote = original.get("remote", {})
                internal_setup_payload["_legacyBootstrapKeyPath"] = (
                    str(remote.get("keyPath", ""))
                    if isinstance(remote, dict)
                    else ""
                )
                imported = secure_import(
                    platform,
                    _legacy_connection(original),
                    internal_setup_payload,
                )
                if imported.get("ok") is not True or imported.get("verified") is not True:
                    secure_changes_committed = imported.get("rollbackIncomplete") is True
                    stage = str(imported.get("stage", "receiver.verify"))
                    raise MigrationError("Managed Identity setup or verification failed")
                secure_changes_committed = True
                connection = normalize_connection(imported.get("connection"))
                if connection.get("securityLevel") != "receiver-only":
                    stage = "receiver.verify"
                    raise MigrationError("Managed Identity restriction verification failed")
                candidate = imported.get("config")
                if not isinstance(candidate, dict):
                    stage = "local-config-write"
                    raise MigrationError("verified migration configuration is missing")
                candidate["connection"] = connection
                candidate["sourceIds"] = []
                candidate["sourceMatchers"] = []
                save_config(candidate)

            stage = "post-migration-verification"
            migrated = self._verify(choice)
            self.backup_path.unlink(missing_ok=True)
            return {
                "ok": True,
                "schemaVersion": MIGRATION_SCHEMA_VERSION,
                "choice": choice,
                "stage": "complete",
                "verified": True,
                "rollback": "not-required",
                "backupRemovedAfterVerification": True,
                "config": migrated,
            }
        except Exception as exc:
            local_rollback = self._restore_backup()
            rollback_complete = local_rollback and not secure_changes_committed
            return {
                "ok": False,
                "schemaVersion": MIGRATION_SCHEMA_VERSION,
                "choice": choice,
                "stage": stage,
                "error": f"migration failed during {stage} ({type(exc).__name__})",
                "rollback": "complete" if rollback_complete else "incomplete",
                "rollbackIncomplete": not rollback_complete,
                "localConfigurationRestored": local_rollback,
                "remoteCleanupRequired": secure_changes_committed,
                "managedIdentityCleanupRequired": secure_changes_committed,
                "backupRetained": self.backup_path.is_file(),
            }

    def _prepare_backup(self, original_text: str) -> None:
        ensure_dirs()
        if self.backup_path.is_symlink():
            raise MigrationError("legacy backup path is unsafe")
        if self.backup_path.exists():
            existing = self.backup_path.read_text(encoding="utf-8")
            if existing != original_text:
                raise MigrationError("a different failed migration backup requires review")
            self.backup_path.chmod(0o600)
            return
        secure_write_text(self.backup_path, original_text)

    def _restore_backup(self) -> bool:
        try:
            if self.backup_path.is_symlink() or not self.backup_path.is_file():
                return False
            secure_write_text(
                config_path(), self.backup_path.read_text(encoding="utf-8")
            )
            return True
        except (OSError, ValueError):
            return False

    def _user_managed_config(self, original: dict[str, Any]) -> dict[str, Any]:
        remote = original.get("remote", {})
        if not isinstance(remote, dict):
            raise MigrationError("legacy receiver details are incomplete")
        return normalize_config(
            {
                "schemaVersion": CONFIG_SCHEMA_VERSION,
                "connection": _legacy_connection(original),
                "destination": original.get("destination", "both"),
                "sourceMatchers": [],
                "privacy": {"lockBehavior": "stop-all", "showReceiverLabel": False},
                "remote": {
                    "host": remote.get("host", ""),
                    "user": remote.get("user", ""),
                    "port": remote.get("port", 22),
                    "keyPath": remote.get("keyPath", ""),
                    "bitrate": remote.get("bitrate", "128k"),
                    "connectTimeoutSeconds": remote.get("connectTimeoutSeconds", 5),
                    "receiverCommand": DEFAULT_RECEIVER_COMMAND,
                },
            }
        )

    def _verify(self, choice: str) -> dict[str, Any]:
        _raw_text, raw = _read_raw()
        if raw is None or int(raw.get("schemaVersion", 0)) != CONFIG_SCHEMA_VERSION:
            raise MigrationError("migrated configuration schema verification failed")
        if "sourceIds" in raw:
            raise MigrationError("obsolete temporary source-id state remains")
        remote = raw.get("remote", {})
        if (
            not isinstance(remote, dict)
            or remote.get("receiverCommand") != DEFAULT_RECEIVER_COMMAND
        ):
            raise MigrationError("Receiver Protocol command verification failed")
        loaded = load_config()
        if choice == "import-secure" and (
            not isinstance(loaded.get("connection"), dict)
            or loaded["connection"].get("securityLevel") != "receiver-only"
        ):
            raise MigrationError("Managed Identity verification did not persist")
        if choice == "keep-user-managed" and (
            not isinstance(loaded.get("connection"), dict)
            or loaded["connection"].get("securityLevel") != "user-managed"
        ):
            raise MigrationError("user-managed status verification did not persist")
        if choice == "start-fresh" and loaded.get("connection") is not None:
            raise MigrationError("fresh configuration still contains a Connection")
        return loaded

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import (
    config_dir,
    data_dir,
    keys_dir,
    load_config,
    logs_dir,
    save_config,
    secure_open_lock,
    secure_write_text,
    state_dir,
    xdg_runtime_dir,
)
from .connections import TrustStore, connection_id, normalize_connection
from .diagnostics import DiagnosticStore
from .identity import ManagedIdentityStore

ABANDONMENT_CONFIRMATION = "ABANDON WITHOUT VERIFIED REVOCATION"
PLUGIN_REMOVE_COMMAND = ["omarchy-plugin-remove", "jabaiwho.ssh-mixer", "--yes"]
PUBLIC_KEY_RE = re.compile(r"^ssh-ed25519 ([A-Za-z0-9+/]+={0,3})(?:\s+.*)?$")
CLEANUP_CODE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
RemoteRemover = Callable[[dict[str, Any]], dict[str, Any]]
PluginRemover = Callable[[list[str]], dict[str, Any]]
StatusReader = Callable[[], dict[str, Any]]


class RemovalError(ValueError):
    """Raised when a removal plan or protected cleanup state is unsafe."""


def _default_plugin_remove(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return {"ok": completed.returncode == 0, "removed": completed.returncode == 0}


def _plan_hash(plan: dict[str, Any]) -> str:
    public = {key: value for key, value in plan.items() if key != "planHash"}
    encoded = json.dumps(public, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _connection_label(connection: dict[str, Any]) -> str:
    if connection.get("type") == "openssh-profile":
        return f"OpenSSH profile {connection.get('profile', '')}"
    return f"{connection.get('user', '')}@{connection.get('host', '')}"


@contextmanager
def _removal_lock():
    lock = xdg_runtime_dir() / "removal.lock"
    if lock.parent.is_symlink():
        raise RemovalError("removal lock directory may not be a symlink")
    lock.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock.parent.chmod(0o700)
    with secure_open_lock(lock) as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield


class RemovalService:
    """Plans and verifies remote revocation before deleting associated local state."""

    def __init__(
        self,
        *,
        remote_remove: RemoteRemover,
        status_reader: StatusReader,
        identity_store: ManagedIdentityStore,
        trust_store: TrustStore,
        diagnostic_store: DiagnosticStore,
        plugin_remove: PluginRemover = _default_plugin_remove,
    ) -> None:
        self._remote_remove = remote_remove
        self._status_reader = status_reader
        self._identities = identity_store
        self._trust = trust_store
        self._diagnostics = diagnostic_store
        self._plugin_remove = plugin_remove

    @property
    def pending_path(self) -> Path:
        return state_dir() / "pending-removals.json"

    def inspect(self) -> dict[str, Any]:
        pending = self._load_pending()
        entries = [
            {
                "connectionId": item_id,
                "platform": str(value.get("platform", "unknown")),
                "status": str(value.get("status", "pending")),
                "code": str(value.get("code", "cleanup-pending")),
                "label": _connection_label(value.get("connection", {})),
                "remoteCleanupVerified": bool(value.get("remoteCleanupVerified", False)),
            }
            for item_id, value in sorted(pending.items())
            if isinstance(value, dict)
        ]
        return {
            "schemaVersion": 1,
            "pendingCount": len(entries),
            "pending": entries,
            "abandonmentConfirmation": ABANDONMENT_CONFIRMATION,
        }

    def plan(self, *, uninstall: bool) -> dict[str, Any]:
        inventory = self._inventory()
        if not uninstall:
            config = load_config()
            current = config.get("connection")
            if not isinstance(current, dict):
                pending = self._load_pending()
                if len(pending) != 1:
                    raise RemovalError("no configured Connection is available for removal")
                target_ids = [next(iter(pending))]
            else:
                target_ids = [connection_id(current)]
            inventory = [item for item in inventory if item["connectionId"] in target_ids]
        if not inventory and not uninstall:
            raise RemovalError("no configured Connection is available for removal")
        scope = "plugin-uninstall" if uninstall else "connection-removal"
        changes = [
            "Defer without stopping an active Session",
            "Request key-specific Receiver cleanup and require verified revocation",
            "Remove the Receiver helper only when no SSH-mixer Managed Identities remain",
            (
                "Delete associated owned identity, Trust Record, Mix Profiles, and "
                "diagnostics only after revocation"
            ),
            "Keep offline or partial cleanup explicitly pending for retry",
        ]
        if uninstall:
            changes.extend(
                [
                    "Remove all verified local SSH-mixer sensitive state",
                    "Invoke Omarchy plugin removal only after Receiver cleanup is resolved",
                ]
            )
        plan: dict[str, Any] = {
            "schemaVersion": 1,
            "scope": scope,
            "receiverCount": len(inventory),
            "receivers": [self._public_item(item) for item in inventory],
            "changes": changes,
            "activeSessionPolicy": "defer-without-stop",
            "offlinePolicy": "pending-until-retry-or-informed-abandonment",
            "abandonmentWarning": (
                "Abandonment deletes local retry credentials without proving remote key "
                "revocation; it is never reported as successful revocation."
            ),
            "pluginCommand": PLUGIN_REMOVE_COMMAND if uninstall else [],
        }
        plan["planHash"] = _plan_hash(plan)
        return plan

    def execute(
        self,
        plan: dict[str, Any],
        *,
        approved_plan_hash: str,
        abandon_pending: bool,
        abandonment_confirmation: str,
    ) -> dict[str, Any]:
        with _removal_lock():
            return self._execute_locked(
                plan,
                approved_plan_hash=approved_plan_hash,
                abandon_pending=abandon_pending,
                abandonment_confirmation=abandonment_confirmation,
            )

    def _execute_locked(
        self,
        plan: dict[str, Any],
        *,
        approved_plan_hash: str,
        abandon_pending: bool,
        abandonment_confirmation: str,
    ) -> dict[str, Any]:
        uninstall = plan.get("scope") == "plugin-uninstall"
        expected = self.plan(uninstall=uninstall)
        if approved_plan_hash != _plan_hash(plan) or plan != expected:
            raise RemovalError("removal plan changed before approval")
        if abandon_pending and abandonment_confirmation != ABANDONMENT_CONFIRMATION:
            return {
                "ok": False,
                "schemaVersion": 1,
                "code": "confirmation-required",
                "message": "exact informed abandonment confirmation is required",
                "revocation": "not-attempted",
            }
        if abandon_pending and any(
            str(item.get("status", "configured")) == "configured"
            for item in plan.get("receivers", [])
        ):
            return {
                "ok": False,
                "schemaVersion": 1,
                "code": "cleanup-attempt-required",
                "message": "Receiver cleanup must be attempted before it can be abandoned",
                "revocation": "not-attempted",
            }
        status = self._status_reader()
        if bool(status.get("active")) or status.get("state") in {"running", "starting"}:
            return {
                "ok": False,
                "schemaVersion": 1,
                "code": "active-session",
                "message": "cleanup is deferred while a Session is active",
                "deferred": True,
                "revocation": "not-attempted",
            }

        results: list[dict[str, Any]] = []
        for public_item in plan.get("receivers", []):
            item_id = str(public_item.get("connectionId", ""))
            item = self._item_by_id(item_id)
            if item is None:
                return self._failure(
                    "cleanup-state-changed",
                    "configured Receiver cleanup state changed",
                    results,
                )
            result = self._remove_one(item, abandon=abandon_pending)
            results.append(result)
            if not result.get("ok"):
                return self._failure(
                    str(result.get("code", "cleanup-pending")),
                    str(result.get("message", "Receiver cleanup remains pending")),
                    results,
                )

        abandoned = any(item.get("revocation") == "abandoned-not-revoked" for item in results)
        if uninstall:
            if self._load_pending():
                return self._failure(
                    "cleanup-pending",
                    "plugin removal is blocked while Receiver cleanup remains pending",
                    results,
                )
            local_removed = self._remove_all_local_state()
            if not local_removed:
                return self._failure(
                    "local-cleanup-incomplete",
                    "sensitive local state could not be verified absent",
                    results,
                )
            try:
                plugin = self._plugin_remove(list(PLUGIN_REMOVE_COMMAND))
            except (OSError, ValueError):
                plugin = {"ok": False, "removed": False}
            if not isinstance(plugin, dict):
                plugin = {"ok": False, "removed": False}
            if not bool(plugin.get("ok")) or not bool(plugin.get("removed")):
                return {
                    "ok": False,
                    "schemaVersion": 1,
                    "code": "plugin-removal-failed",
                    "message": "Omarchy plugin removal failed after local cleanup",
                    "receivers": results,
                    "localSensitiveStateRemoved": True,
                    "pluginRemoved": False,
                    "revocation": (
                        "abandoned-not-revoked" if abandoned else "verified"
                    ),
                }
            return {
                "ok": True,
                "schemaVersion": 1,
                "receivers": results,
                "remoteCleanupVerified": not abandoned,
                "allReceiversRevoked": not abandoned,
                "revocation": "abandoned-not-revoked" if abandoned else "verified",
                "localSensitiveStateRemoved": True,
                "pluginRemoved": True,
            }

        result = results[0]
        return result

    def _remove_one(self, item: dict[str, Any], *, abandon: bool) -> dict[str, Any]:
        item_id = str(item["connectionId"])
        pending = self._load_pending()
        prior = pending.get(item_id, {})
        pending[item_id] = {
            **item,
            "status": str(prior.get("status", "pending")),
            "code": str(prior.get("code", "cleanup-started")),
            "remoteCleanupVerified": bool(
                prior.get("remoteCleanupVerified", item.get("remoteCleanupVerified", False))
            ),
            "helperRemoved": bool(prior.get("helperRemoved", False)),
        }
        self._save_pending(pending)
        managed = item["securityLevel"] == "receiver-only"
        remote_verified = bool(pending[item_id]["remoteCleanupVerified"])

        if not abandon and managed and not remote_verified:
            try:
                request = self._remote_request(item)
                remote = self._remote_remove(request)
            except RemovalError:
                remote = {
                    "ok": False,
                    "verified": False,
                    "code": "retry-credentials-unavailable",
                }
            except (OSError, ValueError):
                remote = {"ok": False, "verified": False, "code": "receiver-offline"}
            if not isinstance(remote, dict):
                remote = {
                    "ok": False,
                    "verified": False,
                    "code": "remote-cleanup-unverified",
                }
            remote_verified = (
                bool(remote.get("ok"))
                and bool(remote.get("verified"))
                and bool(remote.get("keyRevoked"))
                and isinstance(remote.get("helperRemoved"), bool)
            )
            if not remote_verified:
                code = str(remote.get("code", "remote-cleanup-unverified"))
                if not CLEANUP_CODE_RE.fullmatch(code):
                    code = "remote-cleanup-unverified"
                pending[item_id].update({"status": "pending", "code": code})
                self._save_pending(pending)
                return {
                    "ok": False,
                    "schemaVersion": 1,
                    "connectionId": item_id,
                    "code": code,
                    "stages": [
                        {"stage": "receiver.revoke", "ok": False, "code": code}
                    ],
                    "message": "Receiver cleanup could not be verified and remains pending",
                    "revocation": "pending",
                    "remoteCleanupVerified": False,
                    "companionCleanupAvailable": True,
                }
            pending[item_id].update(
                {
                    "status": "remote-verified",
                    "code": "remote-revoked",
                    "remoteCleanupVerified": True,
                    "helperRemoved": bool(remote["helperRemoved"]),
                }
            )
            self._save_pending(pending)
        elif not abandon and not managed:
            pending[item_id].update(
                {"status": "pending", "code": "user-managed-revocation-unavailable"}
            )
            self._save_pending(pending)
            return {
                "ok": False,
                "schemaVersion": 1,
                "connectionId": item_id,
                "code": "user-managed-revocation-unavailable",
                "stages": [
                    {
                        "stage": "receiver.revoke",
                        "ok": False,
                        "code": "user-managed-revocation-unavailable",
                    }
                ],
                "message": "user-owned SSH access cannot be revoked by SSH-mixer",
                "revocation": "pending",
                "remoteCleanupVerified": False,
                "requiresAbandonment": True,
            }

        try:
            local = self._cleanup_local(item, delete_owned_identity=managed)
        except (OSError, ValueError):
            pending = self._load_pending()
            pending[item_id].update(
                {
                    "status": "pending-local-cleanup",
                    "code": "local-cleanup-incomplete",
                    "remoteCleanupVerified": remote_verified,
                }
            )
            self._save_pending(pending)
            return {
                "ok": False,
                "schemaVersion": 1,
                "connectionId": item_id,
                "code": "local-cleanup-incomplete",
                "stages": [
                    {
                        "stage": "receiver.revoke",
                        "ok": remote_verified,
                        "code": "remote-revoked" if remote_verified else "abandoned",
                    },
                    {
                        "stage": "local.cleanup",
                        "ok": False,
                        "code": "local-cleanup-incomplete",
                    },
                ],
                "message": "local cleanup is incomplete and remains pending",
                "revocation": "verified" if remote_verified else "abandoned-not-revoked",
                "remoteCleanupVerified": remote_verified,
            }

        pending = self._load_pending()
        helper_removed = bool(pending.get(item_id, {}).get("helperRemoved", False))
        pending.pop(item_id, None)
        self._save_pending(pending)
        abandoned = abandon or not managed
        return {
            "ok": True,
            "schemaVersion": 1,
            "connectionId": item_id,
            "remoteCleanupVerified": remote_verified and not abandoned,
            "revocation": "abandoned-not-revoked" if abandoned else "verified",
            "helperRemoved": False if abandoned else helper_removed,
            "localCleanup": local,
            "stages": [
                {
                    "stage": "receiver.revoke",
                    "ok": not abandoned,
                    "code": "abandoned" if abandoned else "remote-revoked",
                },
                *[
                    {"stage": f"local.{name}", "ok": verified}
                    for name, verified in local.items()
                ],
            ],
        }

    def _remote_request(self, item: dict[str, Any]) -> dict[str, Any]:
        identity_id = str(item.get("managedIdentityId", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", identity_id):
            raise RemovalError("Managed Identity cleanup id is invalid")
        directory = self._identities.root / identity_id
        private_path = directory / "id_ed25519"
        public_path = directory / "id_ed25519.pub"
        if (
            self._identities.root.is_symlink()
            or directory.is_symlink()
            or private_path.is_symlink()
            or public_path.is_symlink()
            or not private_path.is_file()
            or not public_path.is_file()
            or private_path.stat().st_mode & 0o077
        ):
            raise RemovalError("Managed Identity retry credentials are unavailable or unsafe")
        public_key = public_path.read_text(encoding="utf-8").strip()
        match = PUBLIC_KEY_RE.fullmatch(public_key)
        if not match:
            raise RemovalError("Managed Identity public key is invalid")
        return {
            "schemaVersion": 1,
            "connection": item["connection"],
            "platform": item["platform"],
            "managedIdentityId": identity_id,
            "privateKeyPath": str(private_path),
            "publicKeyBody": match.group(1),
            "operation": "ssh-mixer-receiver v1 remove",
        }

    def _cleanup_local(
        self, item: dict[str, Any], *, delete_owned_identity: bool
    ) -> dict[str, bool]:
        item_id = str(item["connectionId"])
        config = load_config()
        if isinstance(config.get("connection"), dict) and connection_id(
            config["connection"]
        ) == item_id:
            config["connection"] = None
            config["remote"]["host"] = ""
            config["remote"]["user"] = ""
            config["remote"]["keyPath"] = ""
        config["mixProfiles"] = [
            profile
            for profile in config.get("mixProfiles", [])
            if not isinstance(profile.get("connection"), dict)
            or connection_id(profile["connection"]) != item_id
        ]
        save_config(config)
        config_verified = all(
            not isinstance(connection, dict) or connection_id(connection) != item_id
            for connection in [
                load_config().get("connection"),
                *[
                    profile.get("connection")
                    for profile in load_config().get("mixProfiles", [])
                    if isinstance(profile, dict)
                ],
            ]
        )
        if not config_verified:
            raise RemovalError("Connection or Mix Profile references remain")

        trust_deleted = self._trust.revoke(item["connection"])
        if self._trust.has_record(item["connection"]):
            raise RemovalError("Trust Record deletion could not be verified")
        identity_deleted = True
        if delete_owned_identity:
            identity_id = str(item.get("managedIdentityId", ""))
            self._identities.revoke_local(identity_id)
            identity_deleted = not (self._identities.root / identity_id).exists()
            if not identity_deleted:
                raise RemovalError("Managed Identity deletion could not be verified")
        self._diagnostics.clear()
        diagnostics_deleted = not any(logs_dir().iterdir())
        if not diagnostics_deleted:
            raise RemovalError("diagnostic deletion could not be verified")
        return {
            "connectionAndProfilesRemoved": config_verified,
            "trustRecordRemoved": trust_deleted or not self._trust.has_record(item["connection"]),
            "ownedIdentityRemoved": identity_deleted,
            "diagnosticsRemoved": diagnostics_deleted,
        }

    def _inventory(self) -> list[dict[str, Any]]:
        config = load_config()
        pending = self._load_pending()
        items: dict[str, dict[str, Any]] = {
            item_id: dict(value)
            for item_id, value in pending.items()
            if isinstance(value, dict) and isinstance(value.get("connection"), dict)
        }
        candidates: list[tuple[dict[str, Any], str]] = []
        current = config.get("connection")
        if isinstance(current, dict):
            candidates.append((current, str(config.get("remote", {}).get("keyPath", ""))))
        for profile in config.get("mixProfiles", []):
            if isinstance(profile, dict) and isinstance(profile.get("connection"), dict):
                candidates.append((profile["connection"], ""))
        for connection, key_path in candidates:
            item_id = connection_id(connection)
            managed_id = str(connection.get("managedIdentityId", ""))
            items[item_id] = {
                "connectionId": item_id,
                "connection": connection,
                "label": _connection_label(connection),
                "platform": str(connection.get("receiverPlatform", "unknown")),
                "securityLevel": str(connection.get("securityLevel", "user-managed")),
                "managedIdentityId": managed_id,
                "privateKeyPath": key_path or (
                    str(keys_dir() / managed_id / "id_ed25519") if managed_id else ""
                ),
                "status": str(items.get(item_id, {}).get("status", "configured")),
                "code": str(items.get(item_id, {}).get("code", "configured")),
                "remoteCleanupVerified": bool(
                    items.get(item_id, {}).get("remoteCleanupVerified", False)
                ),
            }
        return [items[item_id] for item_id in sorted(items)]

    def _item_by_id(self, item_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self._inventory() if item["connectionId"] == item_id),
            None,
        )

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "connectionId": item["connectionId"],
            "label": item["label"],
            "platform": item["platform"],
            "experimental": item["platform"] == "macos",
            "securityLevel": item["securityLevel"],
            "status": item.get("status", "configured"),
            "remoteCleanupVerified": bool(item.get("remoteCleanupVerified", False)),
            "helperPolicy": "remove-only-when-unshared",
        }

    def _load_pending(self) -> dict[str, dict[str, Any]]:
        path = self.pending_path
        if not path.exists():
            return {}
        if path.is_symlink():
            raise RemovalError("pending cleanup state may not be a symlink")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RemovalError("pending cleanup state is unreadable") from exc
        entries = value.get("entries", {}) if isinstance(value, dict) else {}
        if not isinstance(entries, dict):
            raise RemovalError("pending cleanup state is invalid")
        validated: dict[str, dict[str, Any]] = {}
        for key, item in entries.items():
            if not isinstance(item, dict) or not isinstance(item.get("connection"), dict):
                raise RemovalError("pending cleanup entry is invalid")
            connection = normalize_connection(item["connection"])
            item_id = connection_id(connection)
            if str(key) != item_id:
                raise RemovalError("pending cleanup Connection identity changed")
            security_level = str(connection.get("securityLevel", "user-managed"))
            managed_id = str(connection.get("managedIdentityId", ""))
            if security_level == "receiver-only" and not re.fullmatch(
                r"[0-9a-f]{64}", managed_id
            ):
                raise RemovalError("pending Managed Identity metadata is invalid")
            status = str(item.get("status", "pending"))
            if status not in {"pending", "remote-verified", "pending-local-cleanup"}:
                raise RemovalError("pending cleanup status is invalid")
            remote_verified = item.get("remoteCleanupVerified") is True
            if remote_verified and status not in {
                "remote-verified",
                "pending-local-cleanup",
            }:
                raise RemovalError("pending cleanup verification state is inconsistent")
            validated[item_id] = {
                **item,
                "connectionId": item_id,
                "connection": connection,
                "label": _connection_label(connection),
                "platform": str(connection.get("receiverPlatform", "unknown")),
                "securityLevel": security_level,
                "managedIdentityId": managed_id,
                "status": status,
                "code": (
                    str(item.get("code"))
                    if CLEANUP_CODE_RE.fullmatch(str(item.get("code", "")))
                    else "cleanup-pending"
                ),
                "remoteCleanupVerified": remote_verified,
                "helperRemoved": item.get("helperRemoved") is True,
            }
        return validated

    def _save_pending(self, entries: dict[str, dict[str, Any]]) -> None:
        if not entries:
            if self.pending_path.is_symlink():
                raise RemovalError("pending cleanup state may not be a symlink")
            self.pending_path.unlink(missing_ok=True)
            return
        if self.pending_path.parent.is_symlink():
            raise RemovalError("pending cleanup directory may not be a symlink")
        self.pending_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.pending_path.parent.chmod(0o700)
        secure_write_text(
            self.pending_path,
            json.dumps({"schemaVersion": 1, "entries": entries}, sort_keys=True) + "\n",
        )

    def _remove_all_local_state(self) -> bool:
        roots = [config_dir(), data_dir(), state_dir(), xdg_runtime_dir()]
        for path in roots:
            if path.is_symlink():
                raise RemovalError("protected application directory may not be a symlink")
        for path in roots:
            if path.exists():
                shutil.rmtree(path)
        return all(not path.exists() and not path.is_symlink() for path in roots)

    def _failure(
        self, code: str, message: str, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "schemaVersion": 1,
            "code": code,
            "message": message,
            "receivers": results,
            "revocation": "pending",
            "remoteCleanupVerified": False,
            "pendingCleanup": self.inspect(),
        }

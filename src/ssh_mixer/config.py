from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

APP_ID = "ssh-mixer"
CONFIG_SCHEMA_VERSION = 4
STATE_SCHEMA_VERSION = 1
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class StorageError(ValueError):
    """Raised when protected application storage is unsafe."""

DEFAULT_RECEIVER_COMMAND = "ssh-mixer-receiver v1 play"

DEFAULT_CONFIG: dict[str, Any] = {
    "schemaVersion": CONFIG_SCHEMA_VERSION,
    "sourceIds": [],
    "sourceMatchers": [],
    "pinnedSourceMatchers": [],
    "destination": "both",
    "connection": None,
    "connections": [],
    "privacy": {
        "lockBehavior": "stop-all",
        "showReceiverLabel": False,
    },
    "mixProfiles": [],
    "remote": {
        "host": "",
        "user": "",
        "keyPath": "",
        "port": 22,
        "bitrate": "128k",
        "receiverCommand": DEFAULT_RECEIVER_COMMAND,
        "connectTimeoutSeconds": 5,
    },
}


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def xdg_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def xdg_runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / APP_ID
    return xdg_state_home() / APP_ID / "runtime"


def config_dir() -> Path:
    return xdg_config_home() / APP_ID


def data_dir() -> Path:
    return xdg_data_home() / APP_ID


def keys_dir() -> Path:
    return data_dir() / "keys"


def trust_dir() -> Path:
    return data_dir() / "trust"


def state_dir() -> Path:
    return xdg_state_home() / APP_ID


def logs_dir() -> Path:
    return state_dir() / "logs"


def config_path() -> Path:
    return config_dir() / "config.json"


def state_path() -> Path:
    return state_dir() / "session.json"


def lock_path() -> Path:
    return xdg_runtime_dir() / "session.lock"


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise StorageError(f"protected directory may not be a symlink: {path}")
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    path.chmod(PRIVATE_DIR_MODE)


def _protect_existing_file(path: Path) -> None:
    if path.is_symlink():
        raise StorageError(f"protected file may not be a symlink: {path}")
    if path.is_file():
        path.chmod(PRIVATE_FILE_MODE)


def ensure_dirs() -> None:
    for path in (
        config_dir(),
        data_dir(),
        keys_dir(),
        trust_dir(),
        state_dir(),
        logs_dir(),
        xdg_runtime_dir(),
    ):
        _secure_directory(path)
    for path in (config_path(), state_path(), lock_path()):
        _protect_existing_file(path)
    for directory in (keys_dir(), trust_dir(), logs_dir()):
        for path in directory.iterdir():
            _protect_existing_file(path)


def _protected_open_fd(path: Path, flags: int) -> int:
    if path.is_symlink():
        raise StorageError(f"protected file may not be a symlink: {path}")
    flags |= os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, PRIVATE_FILE_MODE)
    os.fchmod(fd, PRIVATE_FILE_MODE)
    return fd


def secure_open_append(path: Path, *, binary: bool = False) -> Any:
    fd = _protected_open_fd(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    if binary:
        return os.fdopen(fd, "ab", buffering=0)
    return os.fdopen(fd, "a", encoding="utf-8")


def secure_open_lock(path: Path) -> Any:
    fd = _protected_open_fd(path, os.O_RDWR | os.O_CREAT)
    return os.fdopen(fd, "r+", encoding="utf-8")


def secure_write_text(path: Path, text: str) -> None:
    """Atomically write one protected application file without following links."""

    if path.is_symlink():
        raise StorageError(f"protected file may not be a symlink: {path}")
    tmp = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    if tmp.is_symlink():
        raise StorageError(f"temporary file may not be a symlink: {tmp}")
    try:
        fd = _protected_open_fd(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
        path.chmod(PRIVATE_FILE_MODE)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> dict[str, Any]:
    ensure_dirs()
    path = config_path()
    if not path.exists():
        return deep_merge(DEFAULT_CONFIG, {})
    if path.is_symlink():
        raise StorageError(f"protected file may not be a symlink: {path}")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deep_merge(DEFAULT_CONFIG, {})
    if not isinstance(loaded, dict):
        return deep_merge(DEFAULT_CONFIG, {})
    return normalize_config(deep_merge(DEFAULT_CONFIG, loaded))


def save_config(config: dict[str, Any]) -> None:
    ensure_dirs()
    normalized = normalize_config(config)
    # Concrete PulseAudio/PipeWire ids are runtime-only. Persist only stable
    # Source Matchers so numeric-id reuse cannot select unrelated audio.
    normalized.pop("sourceIds", None)
    secure_write_text(
        config_path(),
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
    )


def expand_user_path(value: str) -> str:
    return str(Path(value).expanduser()) if value else value


def remote_config(config: dict[str, Any]) -> dict[str, Any]:
    remote = deep_merge(DEFAULT_CONFIG["remote"], config.get("remote", {}))
    env_overrides = {
        "host": os.environ.get("SSH_MIXER_HOST"),
        "user": os.environ.get("SSH_MIXER_USER"),
        "keyPath": os.environ.get("SSH_MIXER_KEY"),
        "port": os.environ.get("SSH_MIXER_PORT"),
        "bitrate": os.environ.get("SSH_MIXER_BITRATE"),
        "receiverCommand": DEFAULT_RECEIVER_COMMAND,
        "connectTimeoutSeconds": os.environ.get("SSH_MIXER_CONNECT_TIMEOUT"),
    }
    for key, value in env_overrides.items():
        if value:
            remote[key] = value

    remote["host"] = str(remote.get("host", "")).strip()
    remote["user"] = str(remote.get("user", "")).strip()
    remote["keyPath"] = str(remote.get("keyPath", "")).strip()
    try:
        port = int(remote.get("port", 22))
    except (TypeError, ValueError):
        port = 22
    remote["port"] = max(1, min(port, 65535))
    remote["bitrate"] = str(remote.get("bitrate", "128k")).strip() or "128k"
    # Receiver commands are protocol operations, never user-configurable shell text.
    remote["receiverCommand"] = DEFAULT_RECEIVER_COMMAND
    try:
        timeout = int(remote.get("connectTimeoutSeconds", 5))
    except (TypeError, ValueError):
        timeout = 5
    remote["connectTimeoutSeconds"] = max(1, min(timeout, 30))
    return remote


def normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    merged = deep_merge(DEFAULT_CONFIG, config)
    destination = str(merged.get("destination", "both")).lower()
    if destination not in {"local", "ssh", "both"}:
        destination = "both"

    raw_ids = merged.get("sourceIds", [])
    if not isinstance(raw_ids, list):
        raw_ids = []
    source_ids = [str(item) for item in raw_ids if str(item).strip()]

    from .audio import normalize_source_matcher
    from .mix_profiles import normalize_mix_profiles, normalize_privacy

    def normalized_matchers(key: str) -> list[dict[str, Any]]:
        matchers: list[dict[str, Any]] = []
        raw_matchers = merged.get(key, [])
        if not isinstance(raw_matchers, list):
            return matchers
        for raw_matcher in raw_matchers[:32]:
            try:
                matcher = normalize_source_matcher(raw_matcher)
            except ValueError:
                continue
            if matcher not in matchers:
                matchers.append(matcher)
        return matchers

    source_matchers = normalized_matchers("sourceMatchers")
    pinned_source_matchers = normalized_matchers("pinnedSourceMatchers")

    from .connections import normalize_connection, normalize_connections, upsert_connection

    connection = None
    if isinstance(merged.get("connection"), dict):
        connection = normalize_connection(merged["connection"])

    mix_profiles = normalize_mix_profiles(merged.get("mixProfiles"))
    connections = normalize_connections(merged.get("connections"))
    if connection is not None:
        connections = upsert_connection(connections, connection)
    for profile in mix_profiles:
        profile_connection = profile.get("connection")
        if isinstance(profile_connection, dict):
            connections = upsert_connection(connections, profile_connection)

    remote = remote_config(merged)
    if connection is not None:
        remote["host"] = connection["host"]
        remote["user"] = connection["user"]
        remote["port"] = connection["port"]
        remote["connection"] = connection

    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "sourceIds": source_ids,
        "sourceMatchers": source_matchers,
        "pinnedSourceMatchers": pinned_source_matchers,
        "destination": destination,
        "connection": connection,
        "connections": connections,
        "privacy": normalize_privacy(merged.get("privacy")),
        "mixProfiles": mix_profiles,
        "remote": remote,
    }


def config_from_payload(payload: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    base_config = normalize_config(base or load_config())
    incoming: dict[str, Any] = {}

    if "destination" in payload:
        incoming["destination"] = payload["destination"]
    elif "destinationMode" in payload:
        incoming["destination"] = payload["destinationMode"]

    if "sourceIds" in payload:
        incoming["sourceIds"] = payload["sourceIds"]
    elif "sources" in payload:
        incoming["sourceIds"] = payload["sources"]
    if "sourceMatchers" in payload:
        incoming["sourceMatchers"] = payload["sourceMatchers"]
    if "pinnedSourceMatchers" in payload:
        incoming["pinnedSourceMatchers"] = payload["pinnedSourceMatchers"]

    if "connection" in payload:
        incoming["connection"] = payload["connection"]
    if "connections" in payload:
        incoming["connections"] = payload["connections"]
    if "privacy" in payload:
        incoming["privacy"] = payload["privacy"]
    if "mixProfiles" in payload:
        incoming["mixProfiles"] = payload["mixProfiles"]

    remote_payload: dict[str, Any] = {}
    if isinstance(payload.get("remote"), dict):
        remote_payload.update(payload["remote"])
    for payload_key, remote_key in {
        "host": "host",
        "remoteHost": "host",
        "user": "user",
        "remoteUser": "user",
        "keyPath": "keyPath",
        "sshKey": "keyPath",
        "port": "port",
        "remotePort": "port",
        "bitrate": "bitrate",
        "receiverCommand": "receiverCommand",
        "connectTimeoutSeconds": "connectTimeoutSeconds",
    }.items():
        if payload_key in payload:
            remote_payload[remote_key] = payload[payload_key]
    if remote_payload:
        incoming["remote"] = remote_payload

    return normalize_config(deep_merge(base_config, incoming))


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return persisted configuration without secret material.

    SSH-mixer stores paths and target names only. This helper exists so callers
    have a single place to scrub anything that may be added later.
    """

    normalized = normalize_config(config)
    normalized["remote"]["keyPath"] = str(normalized["remote"].get("keyPath", ""))
    from .connections import connection_id

    active = normalized.get("connection")
    active_id = connection_id(active) if isinstance(active, dict) else ""
    normalized["connections"] = [
        {
            **connection,
            "connectionId": connection_id(connection),
            "selected": connection_id(connection) == active_id,
        }
        for connection in normalized.get("connections", [])
    ]
    if isinstance(active, dict):
        normalized["connection"] = {
            **active,
            "connectionId": active_id,
        }
    return normalized

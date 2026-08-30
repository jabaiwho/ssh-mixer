from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PRIVATE_DIR_MODE, StorageError, secure_write_text

CONNECTION_TYPES = {"tailscale", "direct", "openssh-profile"}
PROFILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
KEY_TYPE_RE = re.compile(r"^(?:ssh-|ecdsa-)[A-Za-z0-9@._+-]+$")
MAX_SAVED_CONNECTIONS = 32
MAX_RECEIVER_NAME_LENGTH = 60


class ConnectionError(ValueError):
    """Raised when a Connection cannot be represented safely."""


def _valid_host(host: str) -> bool:
    if not host or host.startswith("-") or len(host) > 253:
        return False
    if any(character.isspace() or ord(character) < 32 for character in host):
        return False
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass
    return all(DNS_LABEL_RE.fullmatch(label) for label in candidate.rstrip(".").split("."))


def _default_receiver_name(value: dict[str, Any]) -> str:
    connection_type = str(value.get("type", "")).strip().lower()
    if connection_type == "openssh-profile":
        profile = str(value.get("profile", "")).strip()
        if profile:
            return profile
    host = str(value.get("host", "")).strip().rstrip(".")
    if not host:
        return "Receiver"
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host.split(".", 1)[0]
    if isinstance(address, ipaddress.IPv4Address):
        octets = str(address).split(".")
        return "Receiver " + ".".join(octets[-2:])
    groups = address.exploded.split(":")
    return "Receiver " + ":".join(groups[:2]) + "…"


def normalize_receiver_name(value: Any, connection: dict[str, Any]) -> str:
    name = str(value or "").strip() or _default_receiver_name(connection)
    if len(name) > MAX_RECEIVER_NAME_LENGTH:
        raise ConnectionError(
            f"Receiver name must be at most {MAX_RECEIVER_NAME_LENGTH} characters"
        )
    if any(unicodedata.category(character).startswith("C") for character in name):
        raise ConnectionError("Receiver name contains control characters")
    return name


def normalize_connection(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConnectionError("connection must be an object")
    connection_type = str(value.get("type", "")).strip().lower()
    if connection_type not in CONNECTION_TYPES:
        raise ConnectionError("connection type must be tailscale, direct, or openssh-profile")
    host = str(value.get("host", "")).strip().rstrip(".")
    user = str(value.get("user", "")).strip()
    if not _valid_host(host):
        raise ConnectionError("receiver host is invalid")
    if not USER_RE.fullmatch(user):
        raise ConnectionError("receiver user is invalid")
    try:
        port = int(value.get("port", 22))
    except (TypeError, ValueError) as exc:
        raise ConnectionError("receiver port is invalid") from exc
    if not 1 <= port <= 65535:
        raise ConnectionError("receiver port must be between 1 and 65535")
    peer_id = str(value.get("peerId", "")).strip()
    if connection_type == "tailscale" and not peer_id:
        raise ConnectionError("Tailscale peer identity is required")
    normalized = {
        "schemaVersion": 1,
        "type": connection_type,
        "host": host,
        "user": user,
        "port": port,
        "peerId": peer_id if connection_type == "tailscale" else "",
    }
    if connection_type in {"tailscale", "direct"}:
        security_level = str(value.get("securityLevel", ""))
        managed_identity_id = str(value.get("managedIdentityId", ""))
        if security_level == "user-managed" and not managed_identity_id:
            normalized["securityLevel"] = "user-managed"
        elif security_level or managed_identity_id:
            receiver_platform = str(value.get("receiverPlatform", ""))
            if (
                security_level != "receiver-only"
                or not re.fullmatch(r"[0-9a-f]{64}", managed_identity_id)
                or receiver_platform not in {"linux", "windows", "macos"}
            ):
                raise ConnectionError("Managed Identity metadata is invalid")
            normalized.update(
                {
                    "securityLevel": security_level,
                    "managedIdentityId": managed_identity_id,
                    "receiverPlatform": receiver_platform,
                    "experimental": receiver_platform == "macos",
                }
            )
    if connection_type == "openssh-profile":
        profile = str(value.get("profile", "")).strip()
        proxy_configured = bool(value.get("proxyCommandConfigured", False))
        proxy_hash = str(value.get("proxyCommandHash", ""))
        if not PROFILE_RE.fullmatch(profile):
            raise ConnectionError("OpenSSH profile name is invalid")
        if proxy_configured and (
            not bool(value.get("proxyConfirmed", False))
            or not re.fullmatch(r"[0-9a-f]{64}", proxy_hash)
        ):
            raise ConnectionError("OpenSSH profile ProxyCommand is not confirmed")
        effective_hash = str(value.get("effectiveConfigHash", ""))
        if effective_hash and not re.fullmatch(r"[0-9a-f]{64}", effective_hash):
            raise ConnectionError("OpenSSH profile effective configuration hash is invalid")
        normalized.update(
            {
                "profile": profile,
                "securityLevel": "user-managed",
                "proxyCommandConfigured": proxy_configured,
                "proxyExecutable": str(value.get("proxyExecutable", "")),
                "proxyCommandHash": proxy_hash if proxy_configured else "",
                "proxyConfirmed": bool(value.get("proxyConfirmed", False)),
                "effectiveConfigHash": effective_hash,
            }
        )
    receiver_name = value.get("receiverName")
    if str(receiver_name or "").strip().rstrip(".") == host:
        receiver_name = ""
    normalized["receiverName"] = normalize_receiver_name(receiver_name, normalized)
    return normalized


def normalize_connections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    connections: list[dict[str, Any]] = []
    for raw in value[:MAX_SAVED_CONNECTIONS]:
        try:
            connection = normalize_connection(raw)
        except ConnectionError:
            continue
        connections = upsert_connection(connections, connection)
    return connections


def upsert_connection(
    connections: list[dict[str, Any]], connection: dict[str, Any]
) -> list[dict[str, Any]]:
    normalized = normalize_connection(connection)
    item_id = connection_id(normalized)
    result = [
        normalize_connection(item)
        for item in connections
        if connection_id(item) != item_id
    ]
    result.append(normalized)
    if len(result) > MAX_SAVED_CONNECTIONS:
        result = result[-MAX_SAVED_CONNECTIONS:]
    return result


def find_connection(
    connections: list[dict[str, Any]], item_id: str
) -> dict[str, Any]:
    for connection in connections:
        normalized = normalize_connection(connection)
        if connection_id(normalized) == str(item_id):
            return normalized
    raise ConnectionError("saved Connection was not found")


def rename_connection(
    connections: list[dict[str, Any]], item_id: str, receiver_name: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    current = find_connection(connections, item_id)
    renamed = normalize_connection(
        {**current, "receiverName": normalize_receiver_name(receiver_name, current)}
    )
    return upsert_connection(connections, renamed), renamed


def discover_tailscale_peers() -> list[dict[str, Any]]:
    if shutil.which("tailscale") is None:
        return []
    try:
        completed = subprocess.run(
            ["tailscale", "status", "--json"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    try:
        return parse_tailscale_status(completed.stdout)
    except ConnectionError:
        return []


def parse_tailscale_status(raw: str) -> list[dict[str, Any]]:
    try:
        status = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectionError("Tailscale returned invalid status data") from exc
    peers_value = status.get("Peer", {}) if isinstance(status, dict) else {}
    if isinstance(peers_value, dict):
        peers_iterable = list(peers_value.values())
    elif isinstance(peers_value, list):
        peers_iterable = peers_value
    else:
        peers_iterable = []
    peers: list[dict[str, Any]] = []
    for peer in peers_iterable:
        if not isinstance(peer, dict) or not bool(peer.get("Online", False)):
            continue
        peer_id = str(peer.get("ID", "")).strip()
        dns_name = str(peer.get("DNSName", "")).strip().rstrip(".")
        hostname = str(peer.get("HostName", "")).strip().rstrip(".")
        host = dns_name or hostname
        addresses = []
        for address in peer.get("TailscaleIPs", []) or []:
            try:
                addresses.append(str(ipaddress.ip_address(str(address))))
            except ValueError:
                continue
        if peer_id and _valid_host(host) and addresses:
            peers.append(
                {
                    "id": peer_id,
                    "host": host,
                    "label": hostname or host,
                    "addresses": addresses,
                    "online": True,
                }
            )
    peers.sort(key=lambda peer: (str(peer["label"]).lower(), str(peer["id"])))
    return peers


def resolve_addresses(host: str) -> list[str]:
    addresses: list[str] = []
    for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
        address = str(ipaddress.ip_address(result[4][0]))
        if address not in addresses:
            addresses.append(address)
    return addresses


def verify_tailscale_peer(
    connection: dict[str, Any],
    peers: list[dict[str, Any]],
    *,
    resolve: Callable[[str], list[str]] = resolve_addresses,
) -> dict[str, Any]:
    normalized = normalize_connection(connection)
    if normalized["type"] != "tailscale":
        raise ConnectionError("connection is not a Tailscale Connection")
    peer = next(
        (candidate for candidate in peers if candidate.get("id") == normalized["peerId"]),
        None,
    )
    if not peer or not peer.get("online"):
        raise ConnectionError("selected Tailscale peer is not online")
    if str(peer.get("host", "")).rstrip(".") != normalized["host"]:
        raise ConnectionError("selected Tailscale peer identity changed")
    advertised = {
        str(ipaddress.ip_address(str(address))) for address in peer.get("addresses", [])
    }
    try:
        resolved = [str(ipaddress.ip_address(address)) for address in resolve(normalized["host"])]
    except (OSError, ValueError) as exc:
        raise ConnectionError("could not resolve the selected Tailscale peer") from exc
    matching = [address for address in resolved if address in advertised]
    if not matching:
        raise ConnectionError("receiver address is not advertised by the selected Tailscale peer")
    return {**normalized, "address": matching[0], "tailscaleVerified": True}


def scan_host_keys(connection: dict[str, Any], *, address: str | None = None) -> list[str]:
    normalized = normalize_connection(connection)
    scan_host = address or normalized["host"]
    try:
        completed = subprocess.run(
            [
                "ssh-keyscan",
                "-T",
                "5",
                "-p",
                str(normalized["port"]),
                scan_host,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConnectionError("could not retrieve the receiver host key") from exc
    if completed.returncode != 0 and not completed.stdout.strip():
        raise ConnectionError("receiver did not provide an SSH host key")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    _parse_host_keys(lines)
    return lines


def connection_id(connection: dict[str, Any]) -> str:
    normalized = normalize_connection(connection)
    value = "\0".join(
        str(normalized.get(key, ""))
        for key in ("type", "profile", "peerId", "host", "port", "user")
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _host_token(connection: dict[str, Any]) -> str:
    host = str(connection["host"])
    port = int(connection["port"])
    return host if port == 22 else f"[{host}]:{port}"


def _parse_host_keys(lines: list[str]) -> list[dict[str, str]]:
    keys: dict[tuple[str, str], dict[str, str]] = {}
    for line in lines:
        parts = line.strip().split()
        if len(parts) < 3 or parts[0].startswith("#"):
            continue
        key_type, encoded = parts[1], parts[2]
        if not KEY_TYPE_RE.fullmatch(key_type):
            continue
        try:
            material = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError):
            continue
        if not material:
            continue
        digest = base64.b64encode(hashlib.sha256(material).digest()).decode("ascii").rstrip("=")
        keys[(key_type, encoded)] = {
            "type": key_type,
            "key": encoded,
            "fingerprint": f"SHA256:{digest}",
        }
    if not keys:
        raise ConnectionError("receiver did not provide a valid SSH host key")
    return [keys[key] for key in sorted(keys)]


class TrustStore:
    """Protected Trust Records and OpenSSH known-host material."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise StorageError("trust directory may not be a symlink")
        self.directory.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        self.directory.chmod(PRIVATE_DIR_MODE)

    @property
    def known_hosts_path(self) -> Path:
        return self.directory / "known_hosts"

    def _record_path(self, connection: dict[str, Any]) -> Path:
        return self.directory / f"{connection_id(connection)}.json"

    def _load(self, connection: dict[str, Any]) -> dict[str, Any] | None:
        path = self._record_path(connection)
        if not path.exists():
            return None
        if path.is_symlink():
            raise StorageError("Trust Record may not be a symlink")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError("Trust Record is unreadable") from exc
        return value if isinstance(value, dict) else None

    def inspect(self, connection: dict[str, Any], candidate_lines: list[str]) -> dict[str, Any]:
        normalized = normalize_connection(connection)
        candidates = _parse_host_keys(candidate_lines)
        approved = self._load(normalized)
        candidate_pairs = {(key["type"], key["key"]) for key in candidates}
        approved_keys = approved.get("keys", []) if approved else []
        approved_pairs = {
            (str(key.get("type", "")), str(key.get("key", "")))
            for key in approved_keys
            if isinstance(key, dict)
        }
        status = "unknown" if approved is None else (
            "trusted" if candidate_pairs == approved_pairs else "changed"
        )
        return {
            "schemaVersion": 1,
            "status": status,
            "connectionId": connection_id(normalized),
            "candidateFingerprints": [key["fingerprint"] for key in candidates],
            "approvedFingerprints": [
                str(key.get("fingerprint", ""))
                for key in approved_keys
                if isinstance(key, dict)
            ],
        }

    def approve(self, connection: dict[str, Any], candidate_lines: list[str]) -> dict[str, Any]:
        normalized = normalize_connection(connection)
        keys = _parse_host_keys(candidate_lines)
        record = {
            "schemaVersion": 1,
            "connectionId": connection_id(normalized),
            "hostToken": _host_token(normalized),
            "keys": keys,
        }
        secure_write_text(
            self._record_path(normalized),
            json.dumps(record, indent=2, sort_keys=True) + "\n",
        )
        self._rebuild_known_hosts()
        return self.inspect(normalized, candidate_lines)

    def has_record(self, connection: dict[str, Any]) -> bool:
        path = self._record_path(normalize_connection(connection))
        if path.is_symlink():
            raise StorageError("Trust Record may not be a symlink")
        return path.is_file()

    def revoke(self, connection: dict[str, Any]) -> bool:
        path = self._record_path(normalize_connection(connection))
        if path.is_symlink():
            raise StorageError("Trust Record may not be a symlink")
        existed = path.is_file()
        path.unlink(missing_ok=True)
        if path.exists() or path.is_symlink():
            raise StorageError("Trust Record deletion could not be verified")
        self._rebuild_known_hosts()
        return existed

    def _rebuild_known_hosts(self) -> None:
        lines: list[str] = []
        for path in sorted(self.directory.glob("*.json")):
            if path.is_symlink():
                raise StorageError("Trust Record may not be a symlink")
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StorageError("Trust Record is unreadable") from exc
            host_token = str(record.get("hostToken", ""))
            scoped_alias = str(record.get("connectionId", ""))
            aliases = [host_token] if host_token else []
            if re.fullmatch(r"[0-9a-f]{64}", scoped_alias):
                aliases.append(scoped_alias)
            for key in record.get("keys", []):
                if not isinstance(key, dict):
                    continue
                for alias in aliases:
                    lines.append(f"{alias} {key.get('type', '')} {key.get('key', '')}")
        secure_write_text(
            self.known_hosts_path,
            "\n".join(sorted(set(lines))) + ("\n" if lines else ""),
        )

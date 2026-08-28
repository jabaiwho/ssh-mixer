from __future__ import annotations

import glob
import hashlib
import os
import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROFILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
WILDCARD_CHARACTERS = set("*?![")


class ProfileError(ValueError):
    """Raised when an OpenSSH Profile cannot be inspected or approved safely."""


def _tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=True, posix=True)
    except ValueError:
        return []


def _config_files(config_path: Path) -> list[Path]:
    pending = [config_path.expanduser()]
    seen: set[Path] = set()
    files: list[Path] = []
    while pending:
        path = pending.pop(0)
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        files.append(resolved)
        try:
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            parts = _tokens(line)
            if not parts or parts[0].lower() != "include":
                continue
            for pattern in parts[1:]:
                expanded = os.path.expanduser(pattern)
                if not os.path.isabs(expanded):
                    expanded = str(resolved.parent / expanded)
                pending.extend(Path(match) for match in sorted(glob.glob(expanded)))
    return files


def discover_profiles(config_path: Path | None = None) -> list[str]:
    path = config_path or (Path.home() / ".ssh" / "config")
    profiles: set[str] = set()
    for config_file in _config_files(path):
        try:
            lines = config_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            parts = _tokens(line)
            if not parts or parts[0].lower() != "host":
                continue
            for candidate in parts[1:]:
                if (
                    PROFILE_RE.fullmatch(candidate)
                    and not any(character in WILDCARD_CHARACTERS for character in candidate)
                ):
                    profiles.add(candidate)
    return sorted(profiles, key=str.lower)


def _reject_match_exec(config_path: Path) -> None:
    for config_file in _config_files(config_path):
        try:
            lines = config_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            parts = [part.lower() for part in _tokens(line)]
            if parts and parts[0] == "match" and "exec" in parts[1:]:
                raise ProfileError(
                    "OpenSSH profiles using Match exec are not supported because inspection could execute it"
                )


def _run_ssh_g(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=8,
    )


def inspect_profile(
    profile: str,
    *,
    config_path: Path | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run_ssh_g,
) -> dict[str, Any]:
    if not PROFILE_RE.fullmatch(profile) or any(
        character in WILDCARD_CHARACTERS for character in profile
    ):
        raise ProfileError("OpenSSH profile name is invalid")
    path = config_path or (Path.home() / ".ssh" / "config")
    _reject_match_exec(path)
    command = [
        "ssh",
        "-F",
        str(path.expanduser()),
        "-G",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "RequestTTY=no",
        "--",
        profile,
    ]
    try:
        completed = runner(command)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProfileError("OpenSSH profile inspection failed") from exc
    if completed.returncode != 0:
        raise ProfileError("OpenSSH profile inspection failed")
    values: dict[str, list[str]] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition(" ")
        if separator:
            values.setdefault(key.lower(), []).append(value.strip())

    def first(key: str, default: str = "") -> str:
        return values.get(key, [default])[0]

    host = first("hostname")
    user = first("user")
    try:
        port = int(first("port", "22"))
    except ValueError as exc:
        raise ProfileError("OpenSSH profile has an invalid port") from exc
    if not host or not user or not 1 <= port <= 65535:
        raise ProfileError("OpenSSH profile is missing a valid host, user, or port")
    proxy_command = first("proxycommand", "none")
    proxy_configured = proxy_command.lower() != "none"
    proxy_hash = (
        hashlib.sha256(proxy_command.encode("utf-8")).hexdigest()
        if proxy_configured
        else ""
    )
    proxy_executable = ""
    if proxy_configured:
        try:
            command_parts = shlex.split(proxy_command, posix=True)
        except ValueError:
            command_parts = []
        if command_parts:
            proxy_executable = Path(command_parts[0]).name
    proxy_jump = first("proxyjump", "none")
    effective_values = {
        "host": host,
        "user": user,
        "port": port,
        "proxyJump": proxy_jump,
        "proxyCommand": proxy_command,
        "identityFiles": values.get("identityfile", []),
        "identityAgent": first("identityagent", ""),
    }
    effective_hash = hashlib.sha256(
        repr(effective_values).encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": 1,
        "profile": profile,
        "host": host,
        "user": user,
        "port": port,
        "proxyJump": proxy_jump,
        "proxyCommandConfigured": proxy_configured,
        "proxyExecutable": proxy_executable or "configured proxy",
        "proxyCommandHash": proxy_hash,
        "identityCount": len(values.get("identityfile", [])),
        "effectiveConfigHash": effective_hash,
        "securityLevel": "user-managed",
    }


def profile_connection(
    inspected: dict[str, Any],
    *,
    proxy_confirmed: bool,
    expected_proxy_hash: str = "",
    expected_effective_hash: str = "",
) -> dict[str, Any]:
    proxy_configured = bool(inspected.get("proxyCommandConfigured"))
    proxy_hash = str(inspected.get("proxyCommandHash", ""))
    if proxy_configured and not proxy_confirmed:
        raise ProfileError("the profile ProxyCommand requires confirmation")
    if expected_proxy_hash and expected_proxy_hash != proxy_hash:
        raise ProfileError("the profile ProxyCommand changed and must be confirmed again")
    effective_hash = str(inspected.get("effectiveConfigHash", ""))
    if expected_effective_hash and expected_effective_hash != effective_hash:
        raise ProfileError("the effective OpenSSH profile changed and must be confirmed again")
    profile = str(inspected.get("profile", ""))
    if not PROFILE_RE.fullmatch(profile):
        raise ProfileError("OpenSSH profile name is invalid")
    return {
        "schemaVersion": 1,
        "type": "openssh-profile",
        "profile": profile,
        "host": str(inspected.get("host", "")),
        "user": str(inspected.get("user", "")),
        "port": int(inspected.get("port", 22)),
        "peerId": "",
        "securityLevel": "user-managed",
        "proxyCommandConfigured": proxy_configured,
        "proxyExecutable": str(inspected.get("proxyExecutable", "")),
        "proxyCommandHash": proxy_hash,
        "proxyConfirmed": bool(proxy_confirmed),
        "effectiveConfigHash": effective_hash,
    }

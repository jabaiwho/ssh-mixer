from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .versions import COMPANION_VERSIONS, PROTOCOL_VERSION, RECEIVER_VERSIONS

PUBLIC_KEY_RE = re.compile(r"^ssh-ed25519 ([A-Za-z0-9+/]+={0,3})(?:\s+.*)?$")
SAFE_PATH_RE = re.compile(r"^/Users/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:[^0-9].*)?$")
QUIET_LEVELS = set(range(-40, 1))
REQUIRED_RESTRICTIONS = {
    "restrict",
    "no-agent-forwarding",
    "no-port-forwarding",
    "no-X11-forwarding",
    "no-pty",
    "no-user-rc",
}
BREW_PATHS = {
    "arm64": "/opt/homebrew/bin/brew",
    "x86_64": "/usr/local/bin/brew",
}


class SetupError(ValueError):
    """Raised when Experimental macOS setup cannot proceed safely."""


def receiver_artifact_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "receiver"
        / "macos"
        / "ssh-mixer-receiver-v1"
    )


def setup_artifact_path() -> Path:
    return receiver_artifact_path().with_name("setup-v1.sh")


def artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version(value: object, name: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(str(value))
    if not match:
        raise SetupError(f"{name} version is missing or malformed")
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def build_macos_plan(probe: dict[str, Any]) -> dict[str, Any]:
    if probe.get("platform") != "macos":
        raise SetupError("Receiver platform is not macOS")
    architecture = str(probe.get("architecture", ""))
    if architecture not in BREW_PATHS:
        raise SetupError("macOS Receiver architecture is unsupported")
    macos_version = _version(probe.get("version"), "macOS")
    if macos_version < (12, 0, 0):
        raise SetupError("Experimental macOS support requires macOS 12 or newer")
    openssh_version = _version(probe.get("openSshVersion"), "OpenSSH")
    if openssh_version < (8, 1, 0):
        raise SetupError("macOS OpenSSH 8.1 or newer is required")
    user = str(probe.get("user", "")).strip()
    home = str(probe.get("home", "")).strip()
    if user == "root" or probe.get("elevated") is True:
        raise SetupError("Experimental macOS Receiver runtime must be non-elevated")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", user):
        raise SetupError("macOS Receiver user is invalid")
    if home != f"/Users/{user}" or not SAFE_PATH_RE.fullmatch(home):
        raise SetupError("macOS Receiver home does not match the selected account")

    expected_brew = BREW_PATHS[architecture]
    reported_brew = str(probe.get("homebrewPath", ""))
    ffplay_available = probe.get("ffplay") is True
    if reported_brew != expected_brew:
        raise SetupError(
            "Homebrew is required at its architecture-specific official location; SSH-mixer never installs it with a download pipeline"
        )
    packages: list[str] = []
    package_command: list[str] = []
    if not ffplay_available:
        packages = ["ffmpeg"]
        package_command = [expected_brew, "install", "ffmpeg"]

    receiver_path = f"{home}/.local/lib/ssh-mixer/ssh-mixer-receiver-v1"
    changes: list[dict[str, Any]] = []
    if probe.get("remoteLogin") is not True:
        changes.append(
            {
                "kind": "remote-login",
                "summary": "Enable Remote Login with native macOS security approval",
                "requiresPrivilege": True,
                "command": ["sudo", "systemsetup", "-setremotelogin", "on"],
            }
        )
    if packages:
        changes.append(
            {
                "kind": "package-install",
                "summary": f"Install ffmpeg with trusted Homebrew at {expected_brew} without auto-updating Homebrew",
                "requiresPrivilege": False,
                "command": package_command,
            }
        )
    changes.extend(
        [
            {
                "kind": "receiver-install",
                "summary": f"Install checksummed Experimental Receiver Protocol v1 at {receiver_path}",
                "requiresPrivilege": False,
            },
            {
                "kind": "managed-key-enroll",
                "summary": "Install one forced Receiver key with shell and forwarding features disabled",
                "requiresPrivilege": False,
            },
        ]
    )
    plan = {
        "schemaVersion": 1,
        "platform": "macos",
        "companionVersion": COMPANION_VERSIONS["macos"],
        "receiverVersion": RECEIVER_VERSIONS["macos"],
        "protocolVersion": PROTOCOL_VERSION,
        "experimental": True,
        "realDeviceVerified": False,
        "architecture": architecture,
        "version": str(probe.get("version")),
        "openSshVersion": str(probe.get("openSshVersion")),
        "user": user,
        "home": home,
        "administratorCapable": probe.get("administratorCapable") is True,
        "remoteLogin": probe.get("remoteLogin") is True,
        "homebrewPath": reported_brew,
        "receiverPath": receiver_path,
        "receiverSha256": artifact_sha256(receiver_artifact_path()),
        "setupSha256": artifact_sha256(setup_artifact_path()),
        "packageSource": "homebrew" if packages else "",
        "packages": packages,
        "packageCommand": package_command,
        "changes": changes,
        "changesApplied": False,
        "authentication": "native-openssh",
        "rollback": "automatic; incomplete rollback is reported explicitly",
    }
    plan["planHash"] = plan_hash(plan)
    return plan


def authorized_key_entry(receiver_path: str, public_key: str) -> str:
    if not SAFE_PATH_RE.fullmatch(receiver_path) or not receiver_path.endswith(
        "/ssh-mixer-receiver-v1"
    ):
        raise SetupError("macOS Receiver Protocol path is invalid")
    match = PUBLIC_KEY_RE.fullmatch(public_key.strip())
    if not match:
        raise SetupError("Managed Identity public key is invalid")
    options = [
        f'command="{receiver_path} --forced --key {match.group(1)}"',
        "restrict",
        "no-agent-forwarding",
        "no-port-forwarding",
        "no-X11-forwarding",
        "no-pty",
        "no-user-rc",
    ]
    return (
        ",".join(options)
        + f" ssh-ed25519 {match.group(1)} ssh-mixer-managed-macos-v1"
    )


def verify_restrictions(entry: str, expected_key_body: str) -> bool:
    options, separator, key_data = entry.partition(" ssh-ed25519 ")
    if not separator or key_data.split(maxsplit=1)[0] != expected_key_body:
        return False
    option_set = set(options.split(","))
    if not REQUIRED_RESTRICTIONS.issubset(option_set):
        return False
    commands = [item for item in option_set if item.startswith('command="')]
    return (
        len(commands) == 1
        and commands[0].endswith(
            f'/ssh-mixer-receiver-v1 --forced --key {expected_key_body}"'
        )
    )


def parse_receiver_operation(command: str) -> dict[str, Any]:
    parts = command.split()
    if len(parts) < 3 or parts[:2] != ["ssh-mixer-receiver", "v1"]:
        raise SetupError("Receiver Protocol v1 is required")
    operation = parts[2]
    if operation in {"capabilities", "diagnostics", "play", "remove"} and len(parts) == 3:
        return {"operation": operation}
    if operation == "quiet-test" and len(parts) == 5 and parts[3] == "--dbfs":
        try:
            dbfs = int(parts[4])
        except ValueError as exc:
            raise SetupError("quiet test level must be an integer") from exc
        if dbfs not in QUIET_LEVELS:
            raise SetupError("quiet test level is outside the approved range")
        return {"operation": operation, "dbfs": dbfs}
    raise SetupError("Receiver operation is malformed or not allowed")


def plan_hash(plan: dict[str, Any]) -> str:
    value = {key: item for key, item in plan.items() if key != "planHash"}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class MacOsSetupTracer:
    def __init__(
        self,
        *,
        apply: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        verify: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        rollback: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._apply = apply
        self._verify = verify
        self._rollback = rollback

    def trace_plan(self, probe: dict[str, Any]) -> dict[str, Any]:
        return build_macos_plan(probe)

    def execute(
        self,
        plan: dict[str, Any],
        identity: dict[str, Any],
        *,
        approved_plan_hash: str,
    ) -> dict[str, Any]:
        if approved_plan_hash != plan_hash(plan):
            raise SetupError("Experimental macOS setup plan changed before approval")
        stages: list[dict[str, Any]] = []
        try:
            applied = self._apply(plan, identity)
            if not bool(applied.get("ok")):
                raise SetupError(str(applied.get("error", "macOS setup apply failed")))
            stages.append({"stage": "apply", "ok": True})
            verified = self._verify(plan, identity)
            if not bool(verified.get("ok")):
                raise SetupError(str(verified.get("error", "macOS verification failed")))
            stages.append({"stage": "verify", "ok": True})
            return {
                "schemaVersion": 1,
                "ok": True,
                "experimental": True,
                "verified": True,
                "rollback": "not-required",
                "stages": stages,
            }
        except (OSError, ValueError) as exc:
            stages.append({"stage": "failure", "ok": False, "error": str(exc)})
            try:
                rollback = self._rollback(plan, identity)
                complete = bool(rollback.get("ok")) and bool(
                    rollback.get("complete", False)
                )
            except (OSError, ValueError) as rollback_exc:
                complete = False
                stages.append(
                    {"stage": "rollback", "ok": False, "error": str(rollback_exc)}
                )
            else:
                stages.append({"stage": "rollback", "ok": complete})
            return {
                "schemaVersion": 1,
                "ok": False,
                "experimental": True,
                "verified": False,
                "error": str(exc),
                "rollback": "complete" if complete else "incomplete",
                "rollbackIncomplete": not complete,
                "stages": stages,
            }

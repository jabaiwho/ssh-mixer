from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .versions import COMPANION_VERSIONS, PROTOCOL_VERSION, RECEIVER_VERSIONS

PUBLIC_KEY_RE = re.compile(r"^ssh-ed25519 ([A-Za-z0-9+/]+={0,3})(?:\s+.*)?$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[^0-9].*)?$")
WINDOWS_PROFILE_RE = re.compile(r"^[A-Za-z]:\\Users\\[^\x00-\x1f\"<>|]{1,128}$")
REQUIRED_RESTRICTIONS = {
    "no-agent-forwarding",
    "no-port-forwarding",
    "no-X11-forwarding",
    "no-pty",
    "no-user-rc",
}
QUIET_LEVELS = set(range(-40, 1))


class SetupError(ValueError):
    """Raised when Windows Receiver setup cannot proceed safely."""


def receiver_artifact_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "receiver"
        / "windows"
        / "ssh-mixer-receiver-v1.ps1"
    )


def setup_artifact_path() -> Path:
    return receiver_artifact_path().with_name("setup-v1.ps1")


def artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version(value: object) -> tuple[int, int, int, int]:
    match = VERSION_RE.fullmatch(str(value))
    if not match:
        raise SetupError("Windows OpenSSH version is missing or malformed")
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def build_windows_plan(
    probe: dict[str, Any], *, administrator_confirmed: bool
) -> dict[str, Any]:
    if probe.get("platform") != "windows":
        raise SetupError("Receiver platform is not supported by the Windows adapter")
    user = str(probe.get("user", "")).strip()
    profile = str(probe.get("profile", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_. -]{1,64}", user) or not WINDOWS_PROFILE_RE.fullmatch(
        profile
    ):
        raise SetupError("Windows Receiver account information is invalid")
    administrator = probe.get("administratorCapable") is True
    elevated = probe.get("elevated") is True
    try:
        ssh_port = int(probe.get("sshPort", 22))
    except (TypeError, ValueError) as exc:
        raise SetupError("Windows OpenSSH port is invalid") from exc
    if not 1 <= ssh_port <= 65535:
        raise SetupError("Windows OpenSSH port is invalid")
    sshd_installed = probe.get("sshdInstalled") is True
    if sshd_installed and _version(probe.get("openSshVersion")) < (8, 1, 0, 0):
        raise SetupError("Windows OpenSSH 8.1 or newer is required for verified restrictions")
    sshd_running = probe.get("sshdRunning") is True
    sshd_connection_evidence = (
        probe.get("serviceInspectable") is False
        and probe.get("inboundSshVerified") is True
    )
    if sshd_installed and not sshd_running and not sshd_connection_evidence:
        raise SetupError("Windows OpenSSH is installed but the bootstrap service is not running")

    packages: list[str] = []
    package_command: list[str] = []
    if probe.get("ffplay") is not True:
        if probe.get("winget") is not True:
            raise SetupError("FFplay is missing and the trusted winget source is unavailable")
        packages = ["Gyan.FFmpeg"]
        package_command = [
            "winget",
            "install",
            "--id",
            "Gyan.FFmpeg",
            "--exact",
            "--source",
            "winget",
            "--scope",
            "user",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ]

    changes: list[dict[str, Any]] = []
    if not sshd_installed:
        changes.extend(
            [
                {
                    "kind": "openssh-capability",
                    "summary": "Install the signed Microsoft OpenSSH Server Windows capability",
                    "requiresPrivilege": True,
                },
                {
                    "kind": "openssh-service",
                    "summary": "Start sshd and set its service startup type to Automatic",
                    "requiresPrivilege": True,
                },
            ]
        )
    firewall_matches = probe.get("firewallRule") is True and str(
        probe.get("firewallPort", ssh_port)
    ) == str(ssh_port)
    firewall_connection_evidence = (
        probe.get("firewallInspectable") is False
        and probe.get("inboundSshVerified") is True
    )
    if not firewall_matches and not firewall_connection_evidence:
        changes.append(
            {
                "kind": "firewall-rule",
                "summary": f"Add the inbound Windows OpenSSH TCP/{ssh_port} firewall rule",
                "requiresPrivilege": True,
            }
        )
    if packages:
        changes.append(
            {
                "kind": "package-install",
                "summary": "Install FFmpeg/FFplay for the current user from the winget source",
                "requiresPrivilege": False,
                "command": package_command,
            }
        )
    authorized_keys = (
        "C:\\ProgramData\\ssh\\administrators_authorized_keys"
        if administrator
        else f"{profile}\\.ssh\\authorized_keys"
    )
    changes.extend(
        [
            {
                "kind": "receiver-install",
                "summary": f"Install checksummed Receiver Protocol v1 under {profile}\\.ssh",
                "requiresPrivilege": False,
            },
            {
                "kind": "managed-key-enroll",
                "summary": "Install one forced Receiver key with shell and SSH forwarding features disabled",
                "requiresPrivilege": administrator,
            },
            {
                "kind": "key-acl",
                "summary": (
                    f"Apply and verify Windows OpenSSH ACL requirements on {authorized_keys}"
                    if administrator
                    else f"Verify pre-prepared Windows OpenSSH ACL requirements on {authorized_keys}"
                ),
                "requiresPrivilege": administrator,
            },
        ]
    )
    plan = {
        "schemaVersion": 1,
        "platform": "windows",
        "companionVersion": COMPANION_VERSIONS["windows"],
        "receiverVersion": RECEIVER_VERSIONS["windows"],
        "protocolVersion": PROTOCOL_VERSION,
        "user": user,
        "profile": profile,
        "openSshVersion": str(probe.get("openSshVersion", "pending-install")),
        "sshPort": ssh_port,
        "sshdEvidence": (
            "service-running" if sshd_running else "verified-inbound-connection"
            if sshd_connection_evidence else "change-required"
        ),
        "firewallEvidence": (
            "named-rule" if firewall_matches else "verified-inbound-connection"
            if firewall_connection_evidence else "change-required"
        ),
        "administratorCapable": administrator,
        "administratorConfirmationRequired": administrator,
        "administratorConfirmed": administrator and administrator_confirmed,
        "bootstrapElevated": elevated,
        "requiredRuntimeElevated": False,
        "authorizedKeysPath": authorized_keys,
        "receiverPath": f"{profile}\\.ssh\\ssh-mixer-receiver-v1.ps1",
        "receiverSha256": artifact_sha256(receiver_artifact_path()),
        "setupSha256": artifact_sha256(setup_artifact_path()),
        "packageSource": "winget" if packages else "",
        "packages": packages,
        "packageCommand": package_command,
        "changes": changes,
        "changesApplied": False,
        "authentication": "native-openssh",
        "rollback": "automatic; incomplete rollback is reported explicitly",
    }
    plan["planHash"] = plan_hash(plan)
    return plan


def _forced_command(key_body: str) -> str:
    script = (
        '& "$env:USERPROFILE\\.ssh\\ssh-mixer-receiver-v1.ps1" '
        f"-Forced -KeyBody '{key_body}'"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return (
        "powershell.exe -NoLogo -NoProfile -NonInteractive "
        f"-ExecutionPolicy RemoteSigned -EncodedCommand {encoded}"
    )


def authorized_key_entry(public_key: str) -> str:
    match = PUBLIC_KEY_RE.fullmatch(public_key.strip())
    if not match:
        raise SetupError("Managed Identity public key is invalid")
    options = [
        f'command="{_forced_command(match.group(1))}"',
        "no-agent-forwarding",
        "no-port-forwarding",
        "no-X11-forwarding",
        "no-pty",
        "no-user-rc",
    ]
    return (
        ",".join(options)
        + f" ssh-ed25519 {match.group(1)} ssh-mixer-managed-windows-v1"
    )


def verify_restrictions(entry: str, expected_key_body: str) -> bool:
    options, separator, key_data = entry.partition(" ssh-ed25519 ")
    if not separator or key_data.split(maxsplit=1)[0] != expected_key_body:
        return False
    option_set = set(options.split(","))
    if not REQUIRED_RESTRICTIONS.issubset(option_set):
        return False
    commands = [item for item in option_set if item.startswith('command="')]
    if len(commands) != 1:
        return False
    command = commands[0][len('command="') : -1]
    return command == _forced_command(expected_key_body)


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


class WindowsSetupTracer:
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

    def trace_plan(
        self, probe: dict[str, Any], *, administrator_confirmed: bool
    ) -> dict[str, Any]:
        return build_windows_plan(
            probe, administrator_confirmed=administrator_confirmed
        )

    def execute(
        self,
        plan: dict[str, Any],
        identity: dict[str, Any],
        *,
        approved_plan_hash: str,
    ) -> dict[str, Any]:
        if approved_plan_hash != plan_hash(plan):
            raise SetupError("Windows setup plan changed before approval")
        if plan.get("administratorConfirmationRequired") and not plan.get(
            "administratorConfirmed"
        ):
            raise SetupError("administrator-capable setup was not confirmed")
        stages: list[dict[str, Any]] = []
        try:
            applied = self._apply(plan, identity)
            if not bool(applied.get("ok")):
                raise SetupError(str(applied.get("error", "Windows setup apply failed")))
            stages.append({"stage": "apply", "ok": True})
            verified = self._verify(plan, identity)
            if not bool(verified.get("ok")):
                raise SetupError(str(verified.get("error", "Windows verification failed")))
            stages.append({"stage": "verify", "ok": True})
            return {
                "schemaVersion": 1,
                "ok": True,
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
                "verified": False,
                "error": str(exc),
                "rollback": "complete" if complete else "incomplete",
                "rollbackIncomplete": not complete,
                "stages": stages,
            }


def package_command_text(plan: dict[str, Any]) -> str:
    return shlex.join([str(value) for value in plan.get("packageCommand", [])])

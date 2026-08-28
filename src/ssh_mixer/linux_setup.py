from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .versions import COMPANION_VERSIONS, PROTOCOL_VERSION, RECEIVER_VERSIONS

PUBLIC_KEY_RE = re.compile(r"^ssh-ed25519 ([A-Za-z0-9+/]+={0,3})(?:\s+.*)?$")
SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
REQUIRED_RESTRICTIONS = {
    "restrict",
    "no-agent-forwarding",
    "no-port-forwarding",
    "no-X11-forwarding",
    "no-pty",
    "no-user-rc",
}
PACKAGE_MANAGERS: tuple[tuple[str, list[str]], ...] = (
    ("apt-get", ["sudo", "apt-get", "install", "--"]),
    ("dnf", ["sudo", "dnf", "install", "-y", "--"]),
    ("pacman", ["sudo", "pacman", "-S", "--needed", "--"]),
    ("zypper", ["sudo", "zypper", "install", "--"]),
)


class SetupError(ValueError):
    """Raised when Linux Receiver setup cannot proceed safely."""


def artifact_path() -> Path:
    return Path(__file__).resolve().parents[2] / "receiver" / "linux" / "ssh-mixer-receiver-v1.py"


def artifact_sha256(path: Path | None = None) -> str:
    data = (path or artifact_path()).read_bytes()
    return hashlib.sha256(data).hexdigest()


def setup_artifact_path() -> Path:
    return artifact_path().with_name("setup-v1.sh")


def build_linux_plan(probe: dict[str, Any]) -> dict[str, Any]:
    if probe.get("platform") != "linux":
        raise SetupError("Receiver platform is not supported by the Linux setup adapter")
    user = str(probe.get("user", "")).strip()
    home = str(probe.get("home", "")).strip()
    if user == "root" or home == "/root":
        raise SetupError("direct root Receiver setup is not allowed")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", user):
        raise SetupError("Receiver user is invalid")
    if not SAFE_PATH_RE.fullmatch(home) or home == "/":
        raise SetupError("Receiver home path is invalid")
    commands = probe.get("commands", {})
    if not isinstance(commands, dict):
        raise SetupError("Linux Receiver capability data is invalid")

    packages: list[str] = []
    if not bool(commands.get("python3")):
        packages.append("python3")
    if not bool(commands.get("ffplay")):
        packages.append("ffmpeg")
    package_manager = ""
    package_command: list[str] = []
    if packages:
        for candidate, prefix in PACKAGE_MANAGERS:
            if bool(commands.get(candidate)):
                package_manager = candidate
                package_command = [*prefix, *packages]
                break
        if not package_manager:
            raise SetupError(
                "Receiver dependencies are missing and no supported trusted package manager was found"
            )

    receiver_path = f"{home}/.local/lib/ssh-mixer/ssh-mixer-receiver-v1.py"
    changes: list[dict[str, Any]] = []
    if packages:
        changes.append(
            {
                "kind": "package-install",
                "summary": f"Install {', '.join(packages)} with {package_manager}",
                "requiresPrivilege": True,
                "command": package_command,
            }
        )
    changes.extend(
        [
            {
                "kind": "receiver-install",
                "summary": f"Install the checksummed Receiver Protocol v1 at {receiver_path}",
                "requiresPrivilege": False,
            },
            {
                "kind": "managed-key-enroll",
                "summary": "Add one forced-command key with shell, forwarding, agent, X11, PTY, and user RC disabled",
                "requiresPrivilege": False,
            },
        ]
    )
    return {
        "schemaVersion": 1,
        "platform": "linux",
        "companionVersion": COMPANION_VERSIONS["linux"],
        "receiverVersion": RECEIVER_VERSIONS["linux"],
        "protocolVersion": PROTOCOL_VERSION,
        "user": user,
        "home": home,
        "receiverPath": receiver_path,
        "receiverSha256": artifact_sha256(),
        "setupSha256": artifact_sha256(setup_artifact_path()),
        "packageManager": package_manager,
        "packages": packages,
        "packageCommand": package_command,
        "changes": changes,
        "changesApplied": False,
        "authentication": "native-openssh",
        "rollback": "automatic; incomplete rollback is reported explicitly",
    }


def authorized_key_entry(receiver_path: str, public_key: str) -> str:
    if not SAFE_PATH_RE.fullmatch(receiver_path) or not receiver_path.endswith(
        "/ssh-mixer-receiver-v1.py"
    ):
        raise SetupError("Receiver Protocol path is invalid")
    match = PUBLIC_KEY_RE.fullmatch(public_key.strip())
    if not match:
        raise SetupError("Managed Identity public key is invalid")
    key_without_comment = f"ssh-ed25519 {match.group(1)}"
    forced_command = f'command="{receiver_path} --forced --key {match.group(1)}"'
    restrictions = [
        forced_command,
        "restrict",
        "no-agent-forwarding",
        "no-port-forwarding",
        "no-X11-forwarding",
        "no-pty",
        "no-user-rc",
    ]
    return ",".join(restrictions) + " " + key_without_comment + " ssh-mixer-managed-v1"


def verify_restrictions(entry: str, expected_key_body: str) -> bool:
    options, separator, key_data = entry.partition(" ssh-ed25519 ")
    if not separator:
        return False
    key_body = key_data.split(maxsplit=1)[0]
    if key_body != expected_key_body:
        return False
    option_set = set(options.split(","))
    if not REQUIRED_RESTRICTIONS.issubset(option_set):
        return False
    command_options = [value for value in option_set if value.startswith('command="')]
    suffix = f' --forced --key {expected_key_body}"'
    if len(command_options) != 1 or not command_options[0].endswith(suffix):
        return False
    command_path = command_options[0][len('command="') : -len(suffix)]
    return bool(SAFE_PATH_RE.fullmatch(command_path))


def probe_command() -> list[str]:
    """A fixed bootstrap probe; its output is parsed locally before planning changes."""
    names = "python3 ffplay apt-get dnf pacman zypper"
    script = (
        "set -eu; "
        "test \"$(uname -s)\" = Linux; "
        "printf 'platform=linux\\nuser=%s\\nhome=%s\\n' \"$(id -un)\" \"$HOME\"; "
        f"for c in {names}; do command -v \"$c\" >/dev/null 2>&1 && "
        "printf 'command.%s=true\\n' \"$c\" || printf 'command.%s=false\\n' \"$c\"; done"
    )
    return ["sh", "-c", script]


def parse_probe(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {"commands": {}}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            raise SetupError("Linux capability probe returned malformed output")
        if key.startswith("command."):
            result["commands"][key.removeprefix("command.")] = value == "true"
        elif key in {"platform", "user", "home"}:
            result[key] = value
        else:
            raise SetupError("Linux capability probe returned an unknown field")
    return result


def plan_hash(plan: dict[str, Any]) -> str:
    public_plan = {key: value for key, value in plan.items() if key != "planHash"}
    encoded = json.dumps(public_plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class LinuxSetupTracer:
    """Runs an approved setup as apply, verify, and explicit rollback stages."""

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
        plan = build_linux_plan(probe)
        plan["planHash"] = plan_hash(plan)
        return plan

    def execute(
        self,
        plan: dict[str, Any],
        identity: dict[str, Any],
        *,
        approved_plan_hash: str,
    ) -> dict[str, Any]:
        if approved_plan_hash != plan_hash(plan):
            raise SetupError("Linux setup plan changed before approval")
        stages: list[dict[str, Any]] = []
        try:
            applied = self._apply(plan, identity)
            if not bool(applied.get("ok")):
                raise SetupError(str(applied.get("error", "Linux setup apply failed")))
            stages.append({"stage": "apply", "ok": True})
            verified = self._verify(plan, identity)
            if not bool(verified.get("ok")):
                raise SetupError(str(verified.get("error", "restriction verification failed")))
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
                rolled_back = self._rollback(plan, identity)
                complete = bool(rolled_back.get("ok")) and bool(
                    rolled_back.get("complete", False)
                )
                stages.append({"stage": "rollback", "ok": complete})
            except (OSError, ValueError) as rollback_exc:
                complete = False
                stages.append(
                    {"stage": "rollback", "ok": False, "error": str(rollback_exc)}
                )
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
    """Display-only shell rendering of the exact package command requiring approval."""
    return shlex.join([str(value) for value in plan.get("packageCommand", [])])

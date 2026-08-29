from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .connections import connection_id, normalize_connection
from .linux_setup import (
    SAFE_PATH_RE,
    SetupError,
    artifact_path,
    artifact_sha256,
    parse_probe,
    probe_command,
    setup_artifact_path,
    verify_restrictions,
)
from .versions import PROTOCOL_VERSION, RECEIVER_VERSIONS

Runner = Callable[..., subprocess.CompletedProcess[str]]
REMOTE_TEMP_RE = re.compile(r"^/tmp/ssh-mixer-setup\.[A-Za-z0-9]+$")
KEY_BODY_RE = re.compile(r"^[A-Za-z0-9+/]+={0,3}$")


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **kwargs)


class LinuxBootstrap:
    """Uses native OpenSSH authentication to install and verify one Linux Receiver."""

    def __init__(
        self,
        connection: dict[str, Any],
        *,
        known_hosts: Path,
        address: str | None = None,
        bootstrap_key_path: str = "",
        runner: Runner = _run,
    ) -> None:
        self.connection = normalize_connection(connection)
        if self.connection["type"] == "openssh-profile":
            raise SetupError("Managed Identity bootstrap requires a verified Tailscale or Direct Connection")
        if self.connection["user"] == "root":
            raise SetupError("direct root Receiver setup is not allowed")
        self.host = address or str(self.connection["host"])
        self.known_hosts = known_hosts
        self.bootstrap_key_path = ""
        if bootstrap_key_path:
            key_path = Path(bootstrap_key_path).expanduser()
            if (
                key_path.is_symlink()
                or not key_path.is_file()
                or key_path.stat().st_mode & 0o077
            ):
                raise SetupError(
                    "legacy bootstrap identity must be a private non-symlink file"
                )
            self.bootstrap_key_path = str(key_path)
        self.runner = runner
        self._apply_changed_receiver = False
        self._apply_rolled_back = False
        self._update_remote_dir = ""
        self._update_receiver_path = ""
        self._update_authorized_keys = ""
        self._update_previous_version = ""
        self._control_dir: Path | None = None
        self._control_path = ""

    def _host(self) -> str:
        return f"[{self.host}]" if ":" in self.host else self.host

    def _bootstrap_control_path(self) -> str:
        if self._control_path:
            return self._control_path
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        parent = runtime if runtime and Path(runtime).is_dir() and not Path(runtime).is_symlink() else None
        self._control_dir = Path(tempfile.mkdtemp(prefix="ssh-mixer-control-", dir=parent))
        self._control_dir.chmod(0o700)
        self._control_path = str(self._control_dir / "control")
        return self._control_path

    def _bootstrap_control_options(self) -> list[str]:
        return [
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=120",
            "-o",
            f"ControlPath={self._bootstrap_control_path()}",
        ]

    def _close_bootstrap_control(self) -> None:
        if not self._control_path:
            return
        self.runner(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-p",
                str(self.connection["port"]),
                "-o",
                f"ControlPath={self._control_path}",
                "-O",
                "exit",
                "--",
                f"{self.connection['user']}@{self._host()}",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if self._control_dir is not None:
            shutil.rmtree(self._control_dir, ignore_errors=True)
        self._control_dir = None
        self._control_path = ""

    def _transport_options(
        self,
        *,
        scp: bool = False,
        include_bootstrap_identity: bool = True,
        clear_forwardings: bool = True,
    ) -> list[str]:
        identity_options = (
            ["-i", self.bootstrap_key_path, "-o", "IdentitiesOnly=yes"]
            if self.bootstrap_key_path and include_bootstrap_identity
            else []
        )
        control_options = self._bootstrap_control_options() if include_bootstrap_identity else []
        return [
            "-F",
            "/dev/null",
            "-P" if scp else "-p",
            str(self.connection["port"]),
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            f"HostKeyAlias={connection_id(self.connection)}",
            "-o",
            "CheckHostIP=no",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "VerifyHostKeyDNS=no",
            *(["-o", "ClearAllForwardings=yes"] if clear_forwardings else []),
            "-o",
            "ForwardAgent=no",
            "-o",
            "ForwardX11=no",
            "-o",
            "PermitLocalCommand=no",
            *control_options,
            *identity_options,
        ]

    def _ssh(self, *, tty: bool = False) -> list[str]:
        tty_arguments = ["-tt"] if tty else ["-T"]
        return [
            "ssh",
            *self._transport_options(),
            *tty_arguments,
            "--",
            f"{self.connection['user']}@{self._host()}",
        ]

    def probe(self) -> dict[str, Any]:
        completed = self.runner(
            [*self._ssh(), shlex.join(probe_command())],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            raise SetupError("Linux capability probe failed")
        return parse_probe(completed.stdout)

    def apply(
        self,
        plan: dict[str, Any],
        identity: dict[str, Any],
        *,
        setup_path: Path | None = None,
        receiver_path: Path | None = None,
        retain_transaction: bool = False,
    ) -> dict[str, Any]:
        public_path = Path(str(identity.get("publicKeyPath", "")))
        setup_path = setup_path or setup_artifact_path()
        receiver_path = receiver_path or artifact_path()
        if plan.get("receiverSha256") != artifact_sha256(receiver_path) or plan.get(
            "setupSha256"
        ) != artifact_sha256(setup_path):
            raise SetupError("approved Linux setup artifact checksums changed")
        if not public_path.is_file() or public_path.is_symlink():
            raise SetupError("Managed Identity public key is unavailable")
        created = self.runner(
            [*self._ssh(), "umask 077; mktemp -d /tmp/ssh-mixer-setup.XXXXXXXX"],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        remote_dir = created.stdout.strip()
        if created.returncode != 0 or not REMOTE_TEMP_RE.fullmatch(remote_dir):
            raise SetupError("could not create a safe Receiver staging directory")
        remote = f"{self.connection['user']}@{self._host()}"
        try:
            for local, name in (
                (setup_path, "setup-v1.sh"),
                (receiver_path, "receiver-v1.py"),
                (public_path, "managed.pub"),
            ):
                copied = self.runner(
                    [
                        "scp",
                        *self._transport_options(scp=True),
                        "--",
                        str(local),
                        f"{remote}:{remote_dir}/{name}",
                    ],
                    check=False,
                )
                if copied.returncode != 0:
                    raise SetupError("Receiver artifact transfer failed before changes were applied")
            checked = self.runner(
                [
                    *self._ssh(),
                    f"sha256sum -- {shlex.quote(remote_dir)}/setup-v1.sh {shlex.quote(remote_dir)}/receiver-v1.py",
                ],
                check=False,
                stdout=subprocess.PIPE,
                text=True,
            )
            checksum_lines = checked.stdout.splitlines()
            expected_checksums = [artifact_sha256(setup_path), artifact_sha256(receiver_path)]
            actual_checksums = [line.split(maxsplit=1)[0] for line in checksum_lines]
            if checked.returncode != 0 or actual_checksums != expected_checksums:
                raise SetupError("Receiver artifact transfer checksum verification failed")
            if retain_transaction:
                home = str(plan.get("home", ""))
                installed_receiver = str(plan.get("receiverPath", ""))
                authorized_keys = f"{home}/.ssh/authorized_keys"
                if (
                    not SAFE_PATH_RE.fullmatch(home)
                    or not SAFE_PATH_RE.fullmatch(installed_receiver)
                    or not SAFE_PATH_RE.fullmatch(authorized_keys)
                ):
                    raise SetupError("approved Linux update paths are invalid")
                backup_command = (
                    "set -eu; umask 077; "
                    f"test -f {shlex.quote(installed_receiver)}; "
                    f"test ! -L {shlex.quote(installed_receiver)}; "
                    f"test -f {shlex.quote(authorized_keys)}; "
                    f"test ! -L {shlex.quote(authorized_keys)}; "
                    f"cp -p -- {shlex.quote(installed_receiver)} {shlex.quote(remote_dir)}/receiver.backup; "
                    f"cp -p -- {shlex.quote(authorized_keys)} {shlex.quote(remote_dir)}/authorized_keys.backup"
                )
                backed_up = self.runner([*self._ssh(), backup_command], check=False)
                if backed_up.returncode != 0:
                    raise SetupError("Linux Receiver update backup could not be retained")
                self._update_remote_dir = remote_dir
                self._update_receiver_path = installed_receiver
                self._update_authorized_keys = authorized_keys
                self._update_previous_version = str(plan.get("previousReceiverVersion", ""))

            package_manager = str(plan.get("packageManager") or "none")
            remote_command = shlex.join(
                [
                    "bash",
                    f"{remote_dir}/setup-v1.sh",
                    "apply",
                    f"{remote_dir}/receiver-v1.py",
                    f"{remote_dir}/managed.pub",
                    artifact_sha256(receiver_path),
                    package_manager,
                ]
            )
            self._apply_changed_receiver = True
            applied = self.runner(
                [*self._ssh(tty=bool(plan.get("packages"))), remote_command],
                check=False,
                stdout=subprocess.PIPE,
                text=True,
            )
            if applied.returncode != 0:
                self._apply_rolled_back = (
                    '"code":"rolled-back"' in applied.stdout
                    and "ROLLBACK_INCOMPLETE" not in applied.stdout
                )
                raise SetupError(
                    "Linux Companion Setup failed and rollback status was reported"
                )
            lines = [line.strip() for line in applied.stdout.splitlines() if line.strip().startswith("{")]
            result = json.loads(lines[-1]) if lines else {}
            if not isinstance(result, dict) or not bool(result.get("ok")):
                raise SetupError("Linux Companion Setup did not confirm completion")
            return {"ok": True, "setup": result}
        finally:
            if not self._update_remote_dir:
                self.runner(
                    [*self._ssh(), f"rm -rf -- {shlex.quote(remote_dir)}"],
                    check=False,
                )

    def verify(self, plan: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
        public_key = str(identity.get("publicKey", ""))
        fields = public_key.split()
        if len(fields) < 2:
            raise SetupError("Managed Identity public key is invalid")
        line_command = "awk '/ ssh-mixer-managed-v1$/ {print}' \"$HOME/.ssh/authorized_keys\""
        inspected = self.runner(
            [*self._ssh(), line_command],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        entries = [line for line in inspected.stdout.splitlines() if fields[1] in line]
        if inspected.returncode != 0 or len(entries) != 1 or not verify_restrictions(
            entries[0], fields[1]
        ):
            raise SetupError("Managed Identity forced-command restrictions could not be verified")

        key_path = str(identity.get("privateKeyPath", ""))
        managed = [
            "ssh",
            *self._transport_options(include_bootstrap_identity=False),
            "-i",
            key_path,
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "RequestTTY=no",
            "--",
            f"{self.connection['user']}@{self._host()}",
        ]
        capability = self.runner(
            [*managed, "ssh-mixer-receiver v1 capabilities"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if capability.returncode != 0:
            raise SetupError("Managed Identity could not run the Receiver Protocol")
        try:
            payload = json.loads(capability.stdout)
        except json.JSONDecodeError as exc:
            raise SetupError("Receiver Protocol verification returned malformed output") from exc
        if (
            payload.get("protocol") != "v1"
            or payload.get("protocolVersion") != PROTOCOL_VERSION
            or payload.get("helperVersion")
            != plan.get("expectedReceiverVersion", RECEIVER_VERSIONS["linux"])
            or payload.get("platform") != "linux"
        ):
            raise SetupError("Receiver Protocol verification returned unexpected capabilities")
        arbitrary = self.runner(
            [*managed, "uname -a"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if arbitrary.returncode == 0 or "protocol-rejected" not in arbitrary.stderr:
            raise SetupError("Managed Identity arbitrary-command rejection could not be verified")
        forwarding = self.runner(
            [
                "ssh",
                *self._transport_options(
                    include_bootstrap_identity=False,
                    clear_forwardings=False,
                ),
                "-i",
                key_path,
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "RequestTTY=no",
                "-o",
                "ExitOnForwardFailure=yes",
                "-R",
                "0:localhost:9",
                "--",
                f"{self.connection['user']}@{self._host()}",
                "ssh-mixer-receiver v1 capabilities",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if forwarding.returncode == 0:
            raise SetupError("Managed Identity port-forwarding rejection could not be verified")
        return {"ok": True, "restrictionsVerified": True, "protocol": "v1"}

    def remove(self, identity: dict[str, Any]) -> dict[str, Any]:
        key_path = Path(str(identity.get("privateKeyPath", "")))
        key_body = str(identity.get("publicKeyBody", ""))
        if (
            key_path.is_symlink()
            or not key_path.is_file()
            or key_path.stat().st_mode & 0o077
            or not KEY_BODY_RE.fullmatch(key_body)
        ):
            raise SetupError("Managed Identity cleanup credentials are unavailable or unsafe")
        command = [
            "ssh",
            *self._transport_options(include_bootstrap_identity=False),
            "-i",
            str(key_path),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "RequestTTY=no",
            "--",
            f"{self.connection['user']}@{self._host()}",
            "ssh-mixer-receiver v1 remove",
        ]
        completed = self.runner(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            return {
                "ok": False,
                "verified": False,
                "code": (
                    "receiver-offline"
                    if completed.returncode == 255
                    else "remote-cleanup-failed"
                ),
            }
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "verified": False,
                "code": "remote-cleanup-unverified",
            }
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("keyRevoked") is not True
            or not isinstance(payload.get("helperRemoved"), bool)
        ):
            return {
                "ok": False,
                "verified": False,
                "code": "remote-cleanup-unverified",
            }
        return {**payload, "verified": True}

    def commit(self) -> bool:
        try:
            if not self._update_remote_dir:
                return False
            remote_dir = self._update_remote_dir
            completed = self.runner(
                [
                    *self._ssh(),
                    f"rm -rf -- {shlex.quote(remote_dir)} && test ! -e {shlex.quote(remote_dir)}",
                ],
                check=False,
            )
            if completed.returncode == 0:
                self._update_remote_dir = ""
                self._update_receiver_path = ""
                self._update_authorized_keys = ""
                return True
            return False
        finally:
            self._close_bootstrap_control()

    def rollback(self, _plan: dict[str, Any], _identity: dict[str, Any]) -> dict[str, Any]:
        try:
            if self._update_remote_dir:
                remote_dir = self._update_remote_dir
                receiver = self._update_receiver_path
                authorized_keys = self._update_authorized_keys
                command = (
                    "set -eu; "
                    f"install -m 755 -- {shlex.quote(remote_dir)}/receiver.backup {shlex.quote(receiver)}; "
                    f"install -m 600 -- {shlex.quote(remote_dir)}/authorized_keys.backup {shlex.quote(authorized_keys)}; "
                    f"rm -rf -- {shlex.quote(remote_dir)}; test ! -e {shlex.quote(remote_dir)}"
                )
                completed = self.runner([*self._ssh(), command], check=False)
                previous = self._update_previous_version
                self._update_remote_dir = ""
                self._update_receiver_path = ""
                self._update_authorized_keys = ""
                return {
                    "ok": completed.returncode == 0,
                    "complete": completed.returncode == 0,
                    "restoredVersion": previous if completed.returncode == 0 else "",
                }
            if not self._apply_changed_receiver or self._apply_rolled_back:
                return {"ok": True, "complete": True}
            # A failure found only by post-install verification cannot safely
            # reconstruct a prior package state after the apply transaction exits.
            return {"ok": False, "complete": False, "reason": "post-verification rollback required"}
        finally:
            self._close_bootstrap_control()

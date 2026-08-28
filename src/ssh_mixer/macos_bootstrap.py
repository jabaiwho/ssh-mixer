from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .bootstrap import LinuxBootstrap, Runner
from .macos_setup import (
    SAFE_PATH_RE,
    SetupError,
    artifact_sha256,
    receiver_artifact_path,
    setup_artifact_path,
    verify_restrictions,
)
from .versions import PROTOCOL_VERSION, RECEIVER_VERSIONS

REMOTE_TEMP_RE = re.compile(r"^/(?:private/)?var/folders/[A-Za-z0-9_./-]+/ssh-mixer-setup\.[A-Za-z0-9]+$|^/tmp/ssh-mixer-setup\.[A-Za-z0-9]+$")


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **kwargs)


def _probe_command() -> str:
    script = r"""
set -eu
[ "$(uname -s)" = Darwin ]
arch=$(uname -m)
case "$arch" in
 arm64) brew=/opt/homebrew/bin/brew; ffplay=/opt/homebrew/bin/ffplay ;;
 x86_64) brew=/usr/local/bin/brew; ffplay=/usr/local/bin/ffplay ;;
 *) brew=; ffplay= ;;
esac
remote=false
/usr/sbin/systemsetup -getremotelogin 2>/dev/null | grep -q 'On$' && remote=true || true
/bin/launchctl print system/com.openssh.sshd >/dev/null 2>&1 && remote=true || true
admin=false
/usr/sbin/dseditgroup -o checkmember -m "$(id -un)" admin 2>/dev/null | grep -q yes && admin=true || true
elevated=false
[ "$(id -u)" -eq 0 ] && elevated=true || true
brew_available=
[ -n "$brew" ] && [ -x "$brew" ] && brew_available=$brew || true
ffplay_available=false
[ -n "$ffplay" ] && [ -x "$ffplay" ] && ffplay_available=true || true
ssh_version=$(/usr/bin/ssh -V 2>&1 | sed -n 's/^OpenSSH_\([0-9][0-9.]*\).*/\1/p')
printf 'platform=macos\narchitecture=%s\nversion=%s\nuser=%s\nhome=%s\nopenSshVersion=%s\nremoteLogin=%s\nadministratorCapable=%s\nhomebrewPath=%s\nffplay=%s\nelevated=%s\n' "$arch" "$(sw_vers -productVersion)" "$(id -un)" "$HOME" "$ssh_version" "$remote" "$admin" "$brew_available" "$ffplay_available" "$elevated"
""".strip()
    return shlex.join(["sh", "-c", script])


def _parse_probe(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    allowed = {
        "platform",
        "architecture",
        "version",
        "user",
        "home",
        "openSshVersion",
        "remoteLogin",
        "administratorCapable",
        "homebrewPath",
        "ffplay",
        "elevated",
    }
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in result:
            raise SetupError("macOS capability probe returned malformed output")
        if value == "true":
            result[key] = True
        elif value == "false":
            result[key] = False
        else:
            result[key] = value
    for boolean in ("remoteLogin", "administratorCapable", "ffplay", "elevated"):
        if result.get(boolean) not in {True, False}:
            raise SetupError("macOS capability probe returned an invalid boolean")
    return result


class MacOsBootstrap(LinuxBootstrap):
    """Experimental macOS OpenSSH bootstrap with fixed POSIX operations."""

    def __init__(
        self,
        connection: dict[str, Any],
        *,
        known_hosts: Path,
        address: str | None = None,
        bootstrap_key_path: str = "",
        runner: Runner = _run,
    ) -> None:
        super().__init__(
            connection,
            known_hosts=known_hosts,
            address=address,
            bootstrap_key_path=bootstrap_key_path,
            runner=runner,
        )
        self._staging_cleanup_failed = False

    def probe(self) -> dict[str, Any]:
        completed = self.runner(
            [*self._ssh(), _probe_command()],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            raise SetupError("Experimental macOS capability probe failed")
        return _parse_probe(completed.stdout)

    def apply(
        self,
        plan: dict[str, Any],
        identity: dict[str, Any],
        *,
        setup_path: Path | None = None,
        receiver_path: Path | None = None,
        retain_transaction: bool = False,
    ) -> dict[str, Any]:
        setup_path = setup_path or setup_artifact_path()
        receiver_path = receiver_path or receiver_artifact_path()
        public_path = Path(str(identity.get("publicKeyPath", "")))
        if (
            plan.get("setupSha256") != artifact_sha256(setup_path)
            or plan.get("receiverSha256") != artifact_sha256(receiver_path)
        ):
            raise SetupError("approved Experimental macOS artifact checksums changed")
        if not public_path.is_file() or public_path.is_symlink():
            raise SetupError("Managed Identity public key is unavailable")
        created = self.runner(
            [*self._ssh(), 'umask 077; mktemp -d "${TMPDIR:-/tmp}/ssh-mixer-setup.XXXXXXXX"'],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        remote_dir = created.stdout.strip()
        if created.returncode != 0 or not REMOTE_TEMP_RE.fullmatch(remote_dir):
            raise SetupError("could not create a safe macOS staging directory")
        remote = f"{self.connection['user']}@{self._host()}"
        try:
            for local, name in (
                (setup_path, "setup-v1.sh"),
                (receiver_path, "receiver-v1"),
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
                    raise SetupError("macOS artifact transfer failed before changes were applied")
            checked = self.runner(
                [
                    *self._ssh(),
                    f"/usr/bin/shasum -a 256 {shlex.quote(remote_dir)}/setup-v1.sh {shlex.quote(remote_dir)}/receiver-v1",
                ],
                check=False,
                stdout=subprocess.PIPE,
                text=True,
            )
            actual = [line.split(maxsplit=1)[0] for line in checked.stdout.splitlines()]
            expected = [artifact_sha256(setup_path), artifact_sha256(receiver_path)]
            if checked.returncode != 0 or actual != expected:
                raise SetupError("macOS artifact transfer checksum verification failed")
            if retain_transaction:
                home = str(plan.get("home", ""))
                installed_receiver = str(plan.get("receiverPath", ""))
                authorized_keys = f"{home}/.ssh/authorized_keys"
                if (
                    not SAFE_PATH_RE.fullmatch(home)
                    or not SAFE_PATH_RE.fullmatch(installed_receiver)
                    or not SAFE_PATH_RE.fullmatch(authorized_keys)
                ):
                    raise SetupError("approved macOS update paths are invalid")
                backup_command = (
                    "set -eu; umask 077; "
                    f"test -f {shlex.quote(installed_receiver)}; "
                    f"test ! -L {shlex.quote(installed_receiver)}; "
                    f"test -f {shlex.quote(authorized_keys)}; "
                    f"test ! -L {shlex.quote(authorized_keys)}; "
                    f"cp -p {shlex.quote(installed_receiver)} {shlex.quote(remote_dir)}/receiver.backup; "
                    f"cp -p {shlex.quote(authorized_keys)} {shlex.quote(remote_dir)}/authorized_keys.backup"
                )
                backed_up = self.runner([*self._ssh(), backup_command], check=False)
                if backed_up.returncode != 0:
                    raise SetupError("macOS Receiver update backup could not be retained")
                self._update_remote_dir = remote_dir
                self._update_receiver_path = installed_receiver
                self._update_authorized_keys = authorized_keys
                self._update_previous_version = str(plan.get("previousReceiverVersion", ""))

            enable_remote_login = "true" if not plan.get("remoteLogin") else "false"
            command = shlex.join(
                [
                    "sh",
                    f"{remote_dir}/setup-v1.sh",
                    "apply",
                    f"{remote_dir}/receiver-v1",
                    f"{remote_dir}/managed.pub",
                    artifact_sha256(receiver_path),
                    str(plan["architecture"]),
                    str(plan["homebrewPath"]),
                    enable_remote_login,
                ]
            )
            self._apply_changed_receiver = True
            requires_tty = any(
                change.get("requiresPrivilege") is True
                for change in plan.get("changes", [])
            )
            applied = self.runner(
                [*self._ssh(tty=requires_tty), command],
                check=False,
                stdout=subprocess.PIPE,
                text=True,
            )
            if applied.returncode != 0:
                self._apply_rolled_back = (
                    '"code":"rolled-back"' in applied.stdout
                    and "ROLLBACK_INCOMPLETE" not in applied.stdout
                )
                raise SetupError("Experimental macOS Companion Setup failed")
            lines = [line.strip() for line in applied.stdout.splitlines() if line.strip().startswith("{")]
            result = json.loads(lines[-1]) if lines else {}
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise SetupError("macOS Companion Setup did not confirm completion")
            return {"ok": True, "setup": result}
        finally:
            if not self._update_remote_dir:
                cleanup = self.runner(
                    [
                        *self._ssh(),
                        f"rm -rf -- {shlex.quote(remote_dir)} && test ! -e {shlex.quote(remote_dir)}",
                    ],
                    check=False,
                )
                if cleanup.returncode != 0:
                    self._staging_cleanup_failed = True

    def verify(self, plan: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
        public_key = str(identity.get("publicKey", ""))
        fields = public_key.split()
        if len(fields) < 2:
            raise SetupError("Managed Identity public key is invalid")
        line_command = "awk '/ ssh-mixer-managed-macos-v1$/ {print}' \"$HOME/.ssh/authorized_keys\""
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
            raise SetupError("Experimental macOS key restrictions could not be verified")
        managed = [
            "ssh",
            *self._transport_options(include_bootstrap_identity=False),
            "-i",
            str(identity.get("privateKeyPath", "")),
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
            raise SetupError("Experimental macOS Receiver Protocol verification failed")
        try:
            payload = json.loads(capability.stdout)
        except json.JSONDecodeError as exc:
            raise SetupError("macOS Receiver returned malformed capabilities") from exc
        if (
            payload.get("platform") != "macos"
            or payload.get("protocol") != "v1"
            or payload.get("protocolVersion") != PROTOCOL_VERSION
            or payload.get("helperVersion")
            != plan.get("expectedReceiverVersion", RECEIVER_VERSIONS["macos"])
            or payload.get("experimental") is not True
            or payload.get("runtimeElevated") is not False
        ):
            raise SetupError("macOS Receiver capabilities failed verification")
        arbitrary = self.runner(
            [*managed, "zsh -c whoami"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if arbitrary.returncode == 0 or "protocol-rejected" not in arbitrary.stderr:
            raise SetupError("macOS arbitrary-command rejection could not be verified")
        forwarding = self.runner(
            [
                "ssh",
                *self._transport_options(
                    include_bootstrap_identity=False,
                    clear_forwardings=False,
                ),
                "-i",
                str(identity.get("privateKeyPath", "")),
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
            raise SetupError("macOS forwarding rejection could not be verified")
        if self._staging_cleanup_failed:
            raise SetupError("Experimental macOS staging cleanup failed")
        return {
            "ok": True,
            "experimental": True,
            "restrictionsVerified": True,
            "runtimeElevated": False,
            "protocol": "v1",
        }

    def rollback(self, plan: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
        if self._update_remote_dir:
            remote_dir = self._update_remote_dir
            receiver = self._update_receiver_path
            authorized_keys = self._update_authorized_keys
            command = (
                "set -eu; "
                f"install -m 755 {shlex.quote(remote_dir)}/receiver.backup {shlex.quote(receiver)}; "
                f"install -m 600 {shlex.quote(remote_dir)}/authorized_keys.backup {shlex.quote(authorized_keys)}; "
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
        if self._staging_cleanup_failed:
            return {"ok": False, "complete": False, "reason": "staging cleanup failed"}
        return super().rollback(plan, identity)

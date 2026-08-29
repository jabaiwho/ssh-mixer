from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .bootstrap import LinuxBootstrap, Runner
from .diagnostics import redact
from .linux_setup import SetupError
from .versions import PROTOCOL_VERSION, RECEIVER_VERSIONS
from .windows_setup import (
    artifact_sha256,
    receiver_artifact_path,
    setup_artifact_path,
    verify_restrictions,
)

def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **kwargs)


WINDOWS_STAGING_RE = re.compile(
    r"^[A-Za-z]:\\[^\r\n\"<>|]*\\ssh-mixer-setup-[0-9a-f]{32}$"
)
WINDOWS_TRANSACTION_RE = re.compile(
    r"^[A-Za-z]:\\[^\r\n\"<>|]*\\ssh-mixer-windows-setup-[0-9a-f]{32}$"
)


def _structured_setup_failure(output: str) -> str:
    for line in reversed(output[-65_536:].splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("code") == "setup-failed"
            and isinstance(payload.get("message"), str)
        ):
            return str(payload["message"]).strip()[-4_096:]
    return ""


def _encoded_powershell(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return (
        "powershell.exe -NoLogo -NoProfile -NonInteractive "
        f"-ExecutionPolicy RemoteSigned -EncodedCommand {encoded}"
    )


def _probe_script() -> str:
    return r"""
$ErrorActionPreference='Stop'
$sshd=Get-Command 'sshd.exe' -CommandType Application -ErrorAction SilentlyContinue
if($null -eq $sshd){$sshdPath=Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'}else{$sshdPath=$sshd.Source}
$version=if(Test-Path -LiteralPath $sshdPath){(Get-Item -LiteralPath $sshdPath).VersionInfo.FileVersion}else{''}
$service=$null
$serviceInspectable=$true
try{$service=Get-Service -Name 'sshd' -ErrorAction Stop}catch{$serviceInspectable=$false}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
$groups=(& whoami.exe /groups /fo csv /nh | Out-String)
$firewall=$null
$firewallInspectable=$true
try{$firewall=Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction Stop}catch{$firewallInspectable=$false}
$firewallPort=if($null -ne $firewall){($firewall|Get-NetFirewallPortFilter).LocalPort}else{''}
@{
 schemaVersion=1;platform='windows';user=$env:USERNAME;profile=$env:USERPROFILE
 openSshVersion=$version;sshdInstalled=(Test-Path -LiteralPath $sshdPath)
 sshdRunning=($null -ne $service -and $service.Status -eq 'Running')
 serviceInspectable=$serviceInspectable
 firewallRule=($null -ne $firewall);firewallPort=[string]$firewallPort
 firewallInspectable=$firewallInspectable;inboundSshVerified=$true
 ffplay=($null -ne (Get-Command 'ffplay.exe' -CommandType Application -ErrorAction SilentlyContinue))
 winget=($null -ne (Get-Command 'winget.exe' -CommandType Application -ErrorAction SilentlyContinue))
 administratorCapable=($groups -match 'S-1-5-32-544')
 elevated=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}|ConvertTo-Json -Compress
""".strip()


class WindowsBootstrap(LinuxBootstrap):
    """Windows OpenSSH bootstrap using only fixed or encoded PowerShell commands."""

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
        self._staging_remote_dir = ""
        self._transaction_path = ""
        self._transaction_requires_tty = False
        self._staging_cleanup_failed = False

    def probe(self) -> dict[str, Any]:
        completed = self.runner(
            [*self._ssh(), _encoded_powershell(_probe_script())],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            raise SetupError("Windows/OpenSSH capability probe failed")
        try:
            result = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise SetupError("Windows capability probe returned malformed output") from exc
        if not isinstance(result, dict):
            raise SetupError("Windows capability probe returned an invalid object")
        return result

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
            raise SetupError("approved Windows artifact checksums changed")
        if not public_path.is_file() or public_path.is_symlink():
            raise SetupError("Managed Identity public key is unavailable")
        create_script = (
            "$p=Join-Path $env:TEMP ('ssh-mixer-setup-'+[Guid]::NewGuid().ToString('N'));"
            "New-Item -ItemType Directory -Path $p|Out-Null;Write-Output $p"
        )
        created = self.runner(
            [*self._ssh(), _encoded_powershell(create_script)],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        remote_dir = created.stdout.strip()
        if created.returncode != 0 or not WINDOWS_STAGING_RE.fullmatch(remote_dir):
            raise SetupError("could not create a safe Windows Receiver staging directory")
        remote = f"{self.connection['user']}@{self._host()}"
        remote_scp_dir = remote_dir.replace("\\", "/")
        try:
            for local, name in (
                (setup_path, "setup-v1.ps1"),
                (receiver_path, "receiver-v1.ps1"),
                (public_path, "managed.pub"),
            ):
                copied = self.runner(
                    [
                        "scp",
                        *self._transport_options(scp=True),
                        "--",
                        str(local),
                        f"{remote}:{remote_scp_dir}/{name}",
                    ],
                    check=False,
                )
                if copied.returncode != 0:
                    raise SetupError("Windows artifact transfer failed before changes were applied")
            escaped = remote_dir.replace("'", "''")
            administrator = "$true" if plan.get("administratorConfirmed") else "$false"
            inbound_verified = (
                "$true"
                if plan.get("sshdEvidence") == "verified-inbound-connection"
                and plan.get("firewallEvidence") == "verified-inbound-connection"
                else "$false"
            )
            install_ffmpeg = (
                "$true"
                if "Gyan.FFmpeg" in {str(package) for package in plan.get("packages", [])}
                else "$false"
            )
            apply_script = (
                f"& '{escaped}\\setup-v1.ps1' -Mode Apply "
                f"-ReceiverSource '{escaped}\\receiver-v1.ps1' "
                f"-PublicKeyFile '{escaped}\\managed.pub' "
                f"-ReceiverSha256 '{artifact_sha256(receiver_path)}' "
                f"-SetupSha256 '{artifact_sha256(setup_path)}' "
                f"-AdministratorConfirmed {administrator} "
                f"-SshPort {int(plan.get('sshPort', 22))} "
                f"-InboundSshVerified {inbound_verified} "
                f"-InstallFfmpegApproved {install_ffmpeg}"
            )
            self._apply_changed_receiver = True
            applied = self.runner(
                [*self._ssh(), _encoded_powershell(apply_script)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._apply_rolled_back = (
                any(
                    f'"code":"{code}"' in applied.stdout
                    for code in ("rolled-back", "no-changes-applied")
                )
                and "Rollback-Incomplete" not in applied.stdout
            )
            if applied.returncode != 0:
                error = _structured_setup_failure(applied.stdout)
                sensitive = [
                    str(self.connection.get("host", "")),
                    str(self.connection.get("user", "")),
                    str(plan.get("profile", "")),
                ]
                detail = redact(error, sensitive) if error else "failure details were unavailable"
                raise SetupError(
                    f"Windows Companion Setup failed: {detail}; rollback status was reported"
                )
            lines = [line.strip() for line in applied.stdout.splitlines() if line.strip().startswith("{")]
            result = json.loads(lines[-1]) if lines else {}
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise SetupError("Windows Companion Setup did not confirm completion")
            transaction = str(result.get("transaction", ""))
            if not WINDOWS_TRANSACTION_RE.fullmatch(transaction):
                raise SetupError("Windows setup transaction path is invalid")
            self._staging_remote_dir = remote_dir
            self._transaction_path = transaction
            self._transaction_requires_tty = False
            public_result = {key: value for key, value in result.items() if key != "transaction"}
            return {"ok": True, "setup": public_result}
        finally:
            if not self._transaction_path and not self._cleanup_staging(remote_dir):
                self._staging_cleanup_failed = True

    def verify(self, plan: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
        public_key = str(identity.get("publicKey", ""))
        fields = public_key.split()
        if len(fields) < 2:
            raise SetupError("Managed Identity public key is invalid")
        key_path = str(plan.get("authorizedKeysPath", ""))
        escaped_key_path = key_path.replace("'", "''")
        read_script = (
            f"Get-Content -LiteralPath '{escaped_key_path}' | "
            "Where-Object { $_ -match ' ssh-mixer-managed-windows-v1$' }"
        )
        inspected = self.runner(
            [*self._ssh(), _encoded_powershell(read_script)],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        entries = [line for line in inspected.stdout.splitlines() if fields[1] in line]
        if inspected.returncode != 0 or len(entries) != 1 or not verify_restrictions(
            entries[0], fields[1]
        ):
            raise SetupError("Windows Managed Identity restrictions could not be verified")

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
            raise SetupError("Windows Managed Identity could not run non-elevated Receiver Protocol")
        try:
            payload = json.loads(capability.stdout)
        except json.JSONDecodeError as exc:
            raise SetupError("Windows Receiver Protocol returned malformed capabilities") from exc
        if (
            payload.get("protocol") != "v1"
            or payload.get("protocolVersion") != PROTOCOL_VERSION
            or payload.get("helperVersion")
            != plan.get("expectedReceiverVersion", RECEIVER_VERSIONS["windows"])
            or payload.get("platform") != "windows"
            or payload.get("runtimeElevated") is not False
        ):
            raise SetupError("Windows Receiver runtime elevation check failed")
        arbitrary = self.runner(
            [*managed, "cmd.exe /c whoami"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if arbitrary.returncode == 0 or "protocol-rejected" not in arbitrary.stderr:
            raise SetupError("Windows arbitrary-command rejection could not be verified")
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
            raise SetupError("Windows port-forwarding rejection could not be verified")
        return {
            "ok": True,
            "restrictionsVerified": True,
            "protocol": "v1",
            "runtimeElevated": False,
        }

    def commit(self) -> bool:
        return self._run_transaction_mode("Commit")

    def _cleanup_staging(self, remote_dir: str) -> bool:
        if not remote_dir:
            return True
        cleanup_path = remote_dir.replace("'", "''")
        cleanup = (
            f"Remove-Item -LiteralPath '{cleanup_path}' -Recurse -Force "
            "-ErrorAction SilentlyContinue;"
            f"if(Test-Path -LiteralPath '{cleanup_path}'){{exit 1}}"
        )
        completed = self.runner(
            [*self._ssh(), _encoded_powershell(cleanup)], check=False
        )
        return completed.returncode == 0

    def _run_transaction_mode(self, mode: str) -> bool:
        if not self._staging_remote_dir or not self._transaction_path:
            return False
        staging = self._staging_remote_dir.replace("'", "''")
        transaction = self._transaction_path.replace("'", "''")
        script = (
            f"& '{staging}\\setup-v1.ps1' -Mode {mode} "
            f"-TransactionPath '{transaction}'"
        )
        completed = self.runner(
            [*self._ssh(tty=self._transaction_requires_tty), _encoded_powershell(script)],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        success = completed.returncode == 0
        if mode == "Commit" and success:
            self._transaction_path = ""
            self._transaction_requires_tty = False
            cleaned = self._cleanup_staging(self._staging_remote_dir)
            self._staging_remote_dir = ""
            if not cleaned:
                self._staging_cleanup_failed = True
            return cleaned
        return success

    def rollback(self, plan: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
        if self._transaction_path:
            complete = self._run_transaction_mode("Rollback")
            self._transaction_path = ""
            self._transaction_requires_tty = False
            cleaned = self._cleanup_staging(self._staging_remote_dir)
            self._staging_remote_dir = ""
            complete = complete and cleaned
            return {"ok": complete, "complete": complete}
        if self._staging_cleanup_failed:
            return {"ok": False, "complete": False, "reason": "staging cleanup failed"}
        return super().rollback(plan, identity)

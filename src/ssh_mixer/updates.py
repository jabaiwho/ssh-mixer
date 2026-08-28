from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .versions import (
    COMPANION_VERSIONS,
    PINNED_RECEIVER_RELEASE,
    PLUGIN_VERSION,
    PROTOCOL_VERSION,
    RECEIVER_VERSIONS,
)

SUPPORTED_PLATFORMS = frozenset(RECEIVER_VERSIONS)
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(
    r"^receiver-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\+[0-9a-f]{40}$"
)
MAX_METADATA_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
SIGNING_IDENTITY = "ssh-mixer-release"
SIGNING_NAMESPACE = "ssh-mixer-release"


class UpdateError(ValueError):
    """Raised when an update is untrusted, incompatible, or unapproved."""


def _runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, **kwargs)


class ReleaseSignatureVerifier:
    """Verifies detached OpenSSH signatures against a pinned allowed-signers file."""

    def __init__(
        self,
        allowed_signers: Path,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = _runner,
    ) -> None:
        self.allowed_signers = allowed_signers
        self.runner = runner

    def verify(self, metadata: bytes, signature: Path | str | bytes) -> bool:
        temporary: Path | None = None
        if isinstance(signature, bytes):
            if not 1 <= len(signature) <= MAX_SIGNATURE_BYTES:
                raise UpdateError("release signature exceeds the size limit")
            temporary = Path(tempfile.mkdtemp(prefix="ssh-mixer-signature-"))
            temporary.chmod(0o700)
            signature_path = temporary / "release-metadata.sig"
            signature_path.write_bytes(signature)
            signature_path.chmod(0o600)
        else:
            signature_path = Path(signature)
        if len(metadata) > MAX_METADATA_BYTES:
            if temporary is not None:
                shutil.rmtree(temporary)
            raise UpdateError("release metadata exceeds the size limit")
        for path, name in (
            (self.allowed_signers, "release trust root"),
            (signature_path, "release signature"),
        ):
            if path.is_symlink() or not path.is_file():
                if temporary is not None:
                    shutil.rmtree(temporary)
                raise UpdateError(f"{name} is missing or unsafe")
        try:
            completed = self.runner(
                [
                    "ssh-keygen",
                    "-Y",
                    "verify",
                    "-f",
                    str(self.allowed_signers),
                    "-I",
                    SIGNING_IDENTITY,
                    "-n",
                    SIGNING_NAMESPACE,
                    "-s",
                    str(signature_path),
                ],
                input=metadata,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
            )
        except Exception as exc:
            raise UpdateError("release metadata signature verification failed") from exc
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
        if completed.returncode != 0:
            raise UpdateError("release metadata signature verification failed")
        return True


def _semver(value: object, label: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(str(value))
    if not match:
        raise UpdateError(f"{label} must be a stable semantic version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _immutable_artifact_url(url: object, version: str) -> str:
    value = str(url)
    parsed = urlsplit(value)
    expected_prefix = f"/jabaiwho/ssh-mixer/releases/download/receiver-v{version}/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or not parsed.path.startswith(expected_prefix)
        or parsed.path == expected_prefix
        or parsed.query
        or parsed.fragment
        or "/latest/" in parsed.path
        or ".." in parsed.path
    ):
        raise UpdateError("release artifact URL is not immutable or repository-scoped")
    filename = parsed.path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", filename):
        raise UpdateError("release artifact filename is unsafe")
    return value


def _parse_metadata(metadata: bytes) -> dict[str, Any]:
    if len(metadata) > MAX_METADATA_BYTES:
        raise UpdateError("release metadata exceeds the size limit")
    try:
        value = json.loads(metadata)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("release metadata is malformed") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise UpdateError("release metadata schema is unsupported")
    release_id = str(value.get("releaseId", ""))
    release_match = RELEASE_ID_RE.fullmatch(release_id)
    if not release_match:
        raise UpdateError("release metadata id is not immutable")
    release_version = ".".join(release_match.groups())
    release_commit = release_id.rsplit("+", 1)[-1]
    _semver(value.get("pluginVersion"), "plugin version")
    published = str(value.get("publishedAt", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", published):
        raise UpdateError("release publication timestamp is invalid")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise UpdateError("release metadata contains no artifacts")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in artifacts:
        if not isinstance(raw, dict):
            raise UpdateError("release artifact metadata is invalid")
        platform = str(raw.get("platform", ""))
        kind = str(raw.get("kind", ""))
        if platform not in SUPPORTED_PLATFORMS or kind not in {"companion", "receiver"}:
            raise UpdateError("release artifact platform or kind is unsupported")
        if (platform, kind) in seen:
            raise UpdateError("release metadata contains duplicate artifacts")
        seen.add((platform, kind))
        version = str(raw.get("version", ""))
        _semver(version, f"{platform} {kind} version")
        source_commit = str(raw.get("sourceCommit", ""))
        if version != release_version or source_commit != release_commit:
            raise UpdateError("release id does not match artifact version and source commit")
        protocol_minimum = raw.get("protocolMinimum")
        protocol_maximum = raw.get("protocolMaximum")
        if (
            not isinstance(protocol_minimum, int)
            or isinstance(protocol_minimum, bool)
            or not isinstance(protocol_maximum, int)
            or isinstance(protocol_maximum, bool)
            or protocol_minimum < 1
            or protocol_maximum < protocol_minimum
        ):
            raise UpdateError("release artifact protocol range is invalid")
        digest = str(raw.get("sha256", ""))
        if not SHA256_RE.fullmatch(digest):
            raise UpdateError("release artifact SHA-256 is invalid")
        size = raw.get("size")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 1 <= size <= MAX_ARTIFACT_BYTES
        ):
            raise UpdateError("release artifact size is invalid")
        changes = raw.get("changes")
        if (
            not isinstance(changes, list)
            or not changes
            or len(changes) > 50
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 300
                or any(ord(character) < 32 or ord(character) == 127 for character in item)
                for item in changes
            )
        ):
            raise UpdateError("release artifact changes are missing or invalid")
        normalized.append(
            {
                "platform": platform,
                "kind": kind,
                "version": version,
                "protocolMinimum": protocol_minimum,
                "protocolMaximum": protocol_maximum,
                "url": _immutable_artifact_url(raw.get("url"), version),
                "sha256": digest,
                "size": size,
                "changes": [item.strip() for item in changes],
                "sourceCommit": source_commit,
            }
        )
    return {
        "schemaVersion": 1,
        "releaseId": release_id,
        "pluginVersion": str(value["pluginVersion"]),
        "publishedAt": published,
        "artifacts": normalized,
    }


def _plan_hash(plan: dict[str, Any]) -> str:
    value = {key: item for key, item in plan.items() if key != "planHash"}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_fetch(url: str, maximum: int) -> bytes:
    request = Request(url, headers={"User-Agent": f"ssh-mixer/{PLUGIN_VERSION}"})
    with urlopen(request, timeout=15) as response:
        final_url = urlsplit(response.geturl())
        if final_url.scheme != "https":
            raise UpdateError("release artifact redirect is not HTTPS")
        declared = response.headers.get("Content-Length")
        if declared is not None and int(declared) > maximum:
            raise UpdateError("release artifact exceeds its signed size")
        content = response.read(maximum + 1)
    if len(content) > maximum:
        raise UpdateError("release artifact exceeds its signed size")
    return content


def fetch_release_pair(
    version: str,
    *,
    fetch: Callable[[str, int], bytes] = _default_fetch,
) -> tuple[bytes, bytes]:
    if not SEMVER_RE.fullmatch(version):
        raise UpdateError("pinned Receiver release version is invalid")
    base = (
        "https://github.com/jabaiwho/ssh-mixer/releases/download/"
        f"receiver-v{version}/receiver-v{version}.json"
    )
    metadata = fetch(base, MAX_METADATA_BYTES)
    signature = fetch(base + ".sig", MAX_SIGNATURE_BYTES)
    if not isinstance(metadata, bytes) or not isinstance(signature, bytes):
        raise UpdateError("release metadata response is not bytes")
    if not metadata or not signature:
        raise UpdateError("release metadata or signature is empty")
    return metadata, signature


class UpdateService:
    """Plans and executes signed, approved, transactional Receiver updates."""

    def __init__(
        self,
        *,
        signature_verifier: Callable[[bytes, Any], bool],
        fetch_artifact: Callable[[str, int], bytes] = _default_fetch,
        installer: Callable[[dict[str, Any], dict[str, Path]], dict[str, Any]],
        post_verify: Callable[[dict[str, Any]], dict[str, Any]],
        rollback: Callable[[dict[str, Any]], dict[str, Any]],
        commit: Callable[[dict[str, Any]], dict[str, Any]] = lambda _plan: {
            "ok": True,
            "complete": True,
        },
        transaction_plan: Callable[[str], dict[str, Any]] = lambda _platform: {
            "authentication": "test-adapter"
        },
        staging_root: Path | None = None,
    ) -> None:
        self.signature_verifier = signature_verifier
        self.fetch_artifact = fetch_artifact
        self.installer = installer
        self.post_verify = post_verify
        self.rollback = rollback
        self.commit = commit
        self.transaction_plan = transaction_plan
        self.staging_root = staging_root

    def _verify_metadata(self, metadata: bytes, signature: Any) -> dict[str, Any]:
        try:
            verified = self.signature_verifier(metadata, signature)
        except Exception as exc:
            raise UpdateError("release metadata signature verification failed") from exc
        if verified is not True:
            raise UpdateError("release metadata signature verification failed")
        return _parse_metadata(metadata)

    def plan(
        self,
        metadata: bytes,
        signature: Any,
        *,
        platform: str,
        installed: dict[str, Any],
    ) -> dict[str, Any]:
        if platform not in SUPPORTED_PLATFORMS:
            raise UpdateError("update platform is unsupported")
        catalog = self._verify_metadata(metadata, signature)
        selected = [item for item in catalog["artifacts"] if item["platform"] == platform]
        if {item["kind"] for item in selected} != {"companion", "receiver"} or len(selected) != 2:
            raise UpdateError("release metadata is incomplete for this Receiver platform")
        for item in selected:
            if not item["protocolMinimum"] <= PROTOCOL_VERSION <= item["protocolMaximum"]:
                raise UpdateError(
                    "release is incompatible with this plugin Receiver Protocol; update the plugin first"
                )
        helper_version = str(installed.get("helperVersion", ""))
        _semver(helper_version, "installed helper version")
        installed_protocol = installed.get("protocol")
        if not isinstance(installed_protocol, int) or isinstance(installed_protocol, bool):
            raise UpdateError("installed Receiver Protocol version is invalid")
        target = {item["kind"]: item["version"] for item in selected}
        compatible_installed = installed_protocol == PROTOCOL_VERSION
        if (
            _semver(target["receiver"], "target receiver version")
            < _semver(helper_version, "installed helper version")
            or _semver(target["companion"], "target companion version")
            < _semver(COMPANION_VERSIONS[platform], "installed Companion version")
        ):
            raise UpdateError("signed release would downgrade an installed component")
        newer = any(
            _semver(target[kind], f"target {kind} version")
            > _semver(
                helper_version if kind == "receiver" else COMPANION_VERSIONS[platform],
                f"current {kind} version",
            )
            for kind in ("companion", "receiver")
        )
        status = (
            "update-required"
            if not compatible_installed
            else ("update-available" if newer else "current")
        )
        changes = [
            {"component": item["kind"], "version": item["version"], "items": item["changes"]}
            for item in sorted(selected, key=lambda artifact: artifact["kind"])
        ]
        transaction = self.transaction_plan(platform)
        if not isinstance(transaction, dict) or not transaction:
            raise UpdateError("production update transaction plan is unavailable")
        plan = {
            "schemaVersion": 1,
            "releaseId": catalog["releaseId"],
            "publishedAt": catalog["publishedAt"],
            "platform": platform,
            "status": status,
            "required": not compatible_installed,
            "automaticInstall": False,
            "current": {
                "plugin": PLUGIN_VERSION,
                "companion": COMPANION_VERSIONS[platform],
                "receiver": helper_version,
                "protocol": installed_protocol,
            },
            "target": {
                "plugin": catalog["pluginVersion"],
                "companion": target["companion"],
                "receiver": target["receiver"],
                "protocol": PROTOCOL_VERSION,
            },
            "guidance": (
                "Installed Receiver Protocol is incompatible; review and apply this signed update before streaming."
                if not compatible_installed
                else "The installed helper remains compatible; updating is optional and requires approval."
            ),
            "metadataSha256": hashlib.sha256(metadata).hexdigest(),
            "artifacts": selected,
            "changes": changes,
            "transaction": transaction,
            "signatureVerified": True,
        }
        plan["planHash"] = _plan_hash(plan)
        return plan

    def execute(
        self,
        plan: dict[str, Any],
        metadata: bytes,
        signature: Any,
        *,
        approved_plan_hash: str,
    ) -> dict[str, Any]:
        if approved_plan_hash != _plan_hash(plan) or approved_plan_hash != plan.get(
            "planHash"
        ):
            raise UpdateError("exact update plan approval is required")
        if plan.get("status") == "current":
            raise UpdateError("installed Receiver components are already current")
        if hashlib.sha256(metadata).hexdigest() != plan.get("metadataSha256"):
            raise UpdateError("release metadata changed after approval")
        current = plan.get("current", {})
        if not isinstance(current, dict):
            raise UpdateError("approved update plan is malformed")
        expected_plan = self.plan(
            metadata,
            signature,
            platform=str(plan.get("platform", "")),
            installed={
                "helperVersion": current.get("receiver", ""),
                "protocol": current.get("protocol"),
            },
        )
        if expected_plan != plan:
            raise UpdateError("approved update plan does not match signed release metadata")
        staging = Path(
            tempfile.mkdtemp(prefix="ssh-mixer-update-", dir=self.staging_root)
        )
        staging.chmod(0o700)
        staged: dict[str, Path] = {}
        install_started = False
        install_completed = False
        result: dict[str, Any]
        try:
            for item in plan.get("artifacts", []):
                content = self.fetch_artifact(item["url"], int(item["size"]))
                if not isinstance(content, bytes):
                    raise UpdateError("release artifact response is not bytes")
                if (
                    len(content) != item["size"]
                    or hashlib.sha256(content).hexdigest() != item["sha256"]
                ):
                    raise UpdateError("release artifact checksum or size verification failed")
                filename = urlsplit(item["url"]).path.rsplit("/", 1)[-1]
                path = staging / f"{item['kind']}-{filename}"
                path.write_bytes(content)
                path.chmod(0o600)
                staged[item["kind"]] = path
        except Exception as exc:
            result = {
                "schemaVersion": 1,
                "ok": False,
                "stage": "artifact-verification",
                "error": str(exc),
                "rollback": "not-required",
                "stagingPath": str(staging),
            }
        else:
            try:
                install_started = True
                target = plan["target"]
                installed = self.installer(plan, staged)
                if installed.get("ok") is not True:
                    raise UpdateError(
                        str(installed.get("error", "update installation failed"))
                    )
                if installed.get("companionVersion") != target["companion"]:
                    raise UpdateError("installed Companion version could not be verified")
                install_completed = True
                verified = self.post_verify(plan)
                if (
                    verified.get("ok") is not True
                    or verified.get("platform") != plan["platform"]
                    or verified.get("helperVersion") != target["receiver"]
                    or verified.get("protocol") != target["protocol"]
                ):
                    raise UpdateError(
                        str(verified.get("error", "post-update verification failed"))
                    )
                committed = self.commit(plan)
                if (
                    committed.get("ok") is not True
                    or committed.get("complete") is not True
                ):
                    raise UpdateError("Receiver update transaction commit failed")
                result = {
                    "schemaVersion": 1,
                    "ok": True,
                    "verified": True,
                    "releaseId": plan["releaseId"],
                    "installed": target,
                    "rollback": "not-required",
                    "stagingPath": str(staging),
                }
            except Exception as exc:
                try:
                    rollback = (
                        self.rollback(plan)
                        if install_started
                        else {"ok": True, "complete": True}
                    )
                    complete = (
                        rollback.get("ok") is True
                        and rollback.get("complete") is True
                    )
                    rollback_error = ""
                except Exception as rollback_exc:
                    rollback = {}
                    complete = False
                    rollback_error = type(rollback_exc).__name__
                result = {
                    "schemaVersion": 1,
                    "ok": False,
                    "stage": (
                        "post-update-verification"
                        if install_completed
                        else "installation"
                    ),
                    "error": str(exc),
                    "rollback": "complete" if complete else "incomplete",
                    "rollbackIncomplete": not complete,
                    "restoredVersion": rollback.get("restoredVersion", ""),
                    "rollbackError": rollback_error,
                    "stagingPath": str(staging),
                }
        try:
            shutil.rmtree(staging)
        except OSError as exc:
            result.update(
                {
                    "ok": False,
                    "stage": "staging-cleanup",
                    "error": "verified update staging cleanup failed",
                    "cleanupIncomplete": True,
                    "cleanupError": type(exc).__name__,
                }
            )
        return result

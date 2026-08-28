#!/usr/bin/env python3
"""Dependency-free repository safety checks used locally and in CI."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MANIFEST_FIELDS = {"id", "name", "version", "kinds", "entryPoints"}
PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ACTION_PIN = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:OPENSSH|RSA|DSA|EC|PGP) PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Slack token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
}
PRIVATE_DEFAULTS = re.compile(
    b"(?i)("
    + b"tt" + b"4070ti"
    + b"|id_ed25519_" + b"tt" + b"4070ti"
    + b"|jak" + b"ea)"
)
PRODUCTION_SIGNER_FINGERPRINT = "SHA256:EKQn+VLM6BR1gMybF35yITfzfYWNmB8N0FB2rDuqZV0"
DOWNLOAD_AND_EXECUTE = re.compile(
    rb"(?i)\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b"
)


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    )
    return [ROOT / raw.decode() for raw in output.split(b"\0") if raw]


def check_manifest(errors: list[str]) -> None:
    path = ROOT / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"manifest.json: invalid JSON: {exc}")
        return
    if manifest.get("schemaVersion") != 1:
        errors.append("manifest.json: schemaVersion must be the number 1")
    missing = REQUIRED_MANIFEST_FIELDS - set(manifest)
    if missing:
        errors.append("manifest.json: missing fields: " + ", ".join(sorted(missing)))
    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not PLUGIN_ID.fullmatch(plugin_id) or ".." in plugin_id:
        errors.append("manifest.json: invalid plugin id")
    kinds = manifest.get("kinds")
    if not isinstance(kinds, list) or not kinds:
        errors.append("manifest.json: kinds must be a non-empty array")
    entries = manifest.get("entryPoints")
    if not isinstance(entries, dict):
        errors.append("manifest.json: entryPoints must be an object")
        return
    for name, raw in entries.items():
        if not isinstance(raw, str) or not raw:
            errors.append(f"manifest.json: entry point {name!r} must be a path string")
            continue
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts or "\n" in raw:
            errors.append(f"manifest.json: unsafe entry point {name!r}")
            continue
        target = ROOT / pure
        if target.is_symlink() or not target.is_file():
            errors.append(f"manifest.json: entry point {name!r} is missing or unsafe")


def check_links(files: list[Path], errors: list[str]) -> None:
    link = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for raw in link.findall(text):
            destination = raw.strip().split(maxsplit=1)[0].strip("<>")
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (path.parent / unquote(parsed.path)).resolve()
            try:
                target.relative_to(ROOT)
            except ValueError:
                errors.append(f"{path.relative_to(ROOT)}: link escapes repository: {raw}")
                continue
            if not target.exists():
                errors.append(f"{path.relative_to(ROOT)}: broken link: {raw}")


def check_workflows(files: list[Path], errors: list[str]) -> None:
    workflow_root = ROOT / ".github" / "workflows"
    qml_syntax_checked = False
    for path in files:
        if workflow_root not in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        if "qmlformat Panel.qml" in text:
            qml_syntax_checked = True
        if re.search(r"(?m)^\s*pull_request_target\s*:", text):
            errors.append(f"{path.relative_to(ROOT)}: pull_request_target is forbidden")
        for action in ACTION_PIN.findall(text):
            if action.startswith("./"):
                continue
            _, separator, revision = action.rpartition("@")
            if not separator or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
                errors.append(
                    f"{path.relative_to(ROOT)}: action is not pinned to a full commit: {action}"
                )
    if not qml_syntax_checked:
        errors.append("CI must parse every QML entry point with qmlformat")
    release_workflow = workflow_root / "attest-release.yml"
    if not release_workflow.is_file():
        errors.append("manual release attestation workflow is missing")
    else:
        release_text = release_workflow.read_text(encoding="utf-8")
        for marker in (
            "workflow_dispatch:",
            "id-token: write",
            "attestations: write",
            "check_public_history.py --initial-release",
            "actions/attest-build-provenance@",
            "actions/upload-artifact@",
        ):
            if marker not in release_text:
                errors.append(f"attest-release.yml: missing release control: {marker}")
        for forbidden in ("gh release", "softprops/action-gh-release", "contents: write"):
            if forbidden in release_text:
                errors.append(f"attest-release.yml: automatic publication is forbidden: {forbidden}")


def check_sensitive_content(files: list[Path], errors: list[str]) -> None:
    for path in files:
        if not path.is_file():
            continue
        data = path.read_bytes()
        relative = path.relative_to(ROOT)
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                errors.append(f"{relative}: possible {name}")
        if PRIVATE_DEFAULTS.search(data):
            errors.append(f"{relative}: contains a private development default")
        if DOWNLOAD_AND_EXECUTE.search(data):
            errors.append(f"{relative}: download-and-execute shell pipeline is forbidden")


def check_windows_receiver(errors: list[str]) -> None:
    setup = ROOT / "receiver" / "windows" / "setup-v1.ps1"
    receiver = ROOT / "receiver" / "windows" / "ssh-mixer-receiver-v1.ps1"
    for path in (setup, receiver):
        if not path.is_file():
            errors.append(f"{path.relative_to(ROOT)}: required Windows artifact is missing")
            return
    setup_text = setup.read_text(encoding="utf-8")
    receiver_text = receiver.read_text(encoding="utf-8")
    forbidden = {
        "Invoke-Expression": "dynamic PowerShell execution",
        "ExecutionPolicy Bypass": "Windows security-policy bypass",
        "Invoke-WebRequest": "unverified direct download",
        "Set-MpPreference": "Windows security-control modification",
    }
    for token, description in forbidden.items():
        if token.lower() in setup_text.lower() or token.lower() in receiver_text.lower():
            errors.append(f"Windows artifacts contain forbidden {description}: {token}")
    for required in (
        "no-agent-forwarding",
        "no-port-forwarding",
        "no-X11-forwarding",
        "no-pty",
        "no-user-rc",
        "Set-Acl",
        "Rollback-Incomplete",
    ):
        if required not in setup_text:
            errors.append(f"receiver/windows/setup-v1.ps1: missing security control: {required}")
    for required in ("Assert-NonElevated", "$QuietStartDbfs = -40", "$QuietMaximumDbfs = -24"):
        if required not in receiver_text:
            errors.append(
                f"receiver/windows/ssh-mixer-receiver-v1.ps1: missing security control: {required}"
            )


def check_macos_receiver(errors: list[str]) -> None:
    setup = ROOT / "receiver" / "macos" / "setup-v1.sh"
    receiver = ROOT / "receiver" / "macos" / "ssh-mixer-receiver-v1"
    for path in (setup, receiver):
        if not path.is_file():
            errors.append(f"{path.relative_to(ROOT)}: required macOS artifact is missing")
            return
    setup_text = setup.read_text(encoding="utf-8")
    receiver_text = receiver.read_text(encoding="utf-8")
    for forbidden in ("spctl --master-disable", "xattr -dr", "xattr -cr"):
        if forbidden.lower() in setup_text.lower() or forbidden.lower() in receiver_text.lower():
            errors.append(f"macOS artifacts bypass a platform security control: {forbidden}")
    for required in (
        "Experimental",
        "systemsetup -setremotelogin on",
        "brew install ffmpeg",
        "restrict",
        "no-port-forwarding",
        "ROLLBACK_INCOMPLETE",
    ):
        if required.lower() not in setup_text.lower():
            errors.append(f"receiver/macos/setup-v1.sh: missing security control: {required}")
    for required in (
        '"experimental":true',
        '"realDeviceVerified":false',
        "QUIET_START_DBFS=-40",
        "QUIET_MAXIMUM_DBFS=-24",
    ):
        if required not in receiver_text:
            errors.append(
                f"receiver/macos/ssh-mixer-receiver-v1: missing Experimental control: {required}"
            )


def check_update_versions(errors: list[str]) -> None:
    versions_text = (ROOT / "src" / "ssh_mixer" / "versions.py").read_text(
        encoding="utf-8"
    )
    plugin_match = re.search(r'^PLUGIN_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', versions_text, re.MULTILINE)
    protocol_match = re.search(r"^PROTOCOL_VERSION = ([0-9]+)$", versions_text, re.MULTILINE)
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if plugin_match is None or manifest.get("version") != plugin_match.group(1):
        errors.append("manifest and plugin versions are not synchronized")
    if protocol_match is None:
        errors.append("Receiver Protocol version is not represented independently")
    builder_text = (ROOT / "scripts" / "build_release_metadata.py").read_text(
        encoding="utf-8"
    )
    if plugin_match is not None and f'"pluginVersion": "{plugin_match.group(1)}"' not in builder_text:
        errors.append("release metadata builder plugin version is not synchronized")
    expected_markers = {
        "receiver/linux/setup-v1.sh": 'COMPANION_VERSION="1.1.1"',
        "receiver/linux/ssh-mixer-receiver-v1.py": 'HELPER_VERSION = "1.1.1"',
        "receiver/windows/setup-v1.ps1": "$CompanionVersion = '1.1.1'",
        "receiver/windows/ssh-mixer-receiver-v1.ps1": "$HelperVersion = '1.1.1'",
        "receiver/macos/setup-v1.sh": "COMPANION_VERSION=1.1.1",
        "receiver/macos/ssh-mixer-receiver-v1": "HELPER_VERSION=1.1.1",
    }
    for relative, marker in expected_markers.items():
        if marker not in (ROOT / relative).read_text(encoding="utf-8"):
            errors.append(f"{relative}: missing independent component version: {marker}")
    trust_root = ROOT / "release" / "allowed_signers"
    if trust_root.exists():
        text = trust_root.read_text(encoding="utf-8")
        if (
            trust_root.is_symlink()
            or "PLACEHOLDER" in text
            or not re.search(r'^ssh-mixer-release namespaces="ssh-mixer-release" ssh-ed25519 [A-Za-z0-9+/]+={0,3}$', text, re.MULTILINE)
        ):
            errors.append("release/allowed_signers is not a valid reviewed release trust root")


def check_privacy_lifecycle(errors: list[str]) -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    kinds = set(manifest.get("kinds", []))
    if not {"panel", "service", "bar-widget"}.issubset(kinds):
        errors.append("privacy lifecycle requires panel, service, and bar-widget kinds")
    if manifest.get("keepLoaded") is not True:
        errors.append("privacy lifecycle service must remain loaded")
    entry_points = manifest.get("entryPoints", {})
    expected = {
        "panel": "Panel.qml",
        "service": "Lifecycle.qml",
        "barWidget": "Indicator.qml",
    }
    for kind, relative in expected.items():
        if entry_points.get(kind) != relative or not (ROOT / relative).is_file():
            errors.append(f"privacy lifecycle entry point is missing: {kind} -> {relative}")
    indicator = (ROOT / "Indicator.qml").read_text(encoding="utf-8")
    lifecycle = (ROOT / "src" / "ssh_mixer" / "lifecycle.py").read_text(
        encoding="utf-8"
    )
    for required in ("captureActive", "receiverLabel", "indicator-status"):
        if required not in indicator:
            errors.append(f"Indicator.qml: missing persistent privacy control: {required}")
    for required in (
        '"capture-screen-lock"',
        '"continue-playback"',
        '"suspend"',
        '"logout"',
        '"fatal-network-loss"',
        '"privacy-monitor-failure"',
        "privacy_services_ready",
        "write_lifecycle_heartbeat",
        "write_indicator_heartbeat",
    ):
        if required not in lifecycle:
            errors.append(f"lifecycle.py: missing lifecycle policy: {required}")


def check_legacy_migration(errors: list[str]) -> None:
    migration_path = ROOT / "src" / "ssh_mixer" / "migration.py"
    if not migration_path.is_file():
        errors.append("src/ssh_mixer/migration.py: required migration seam is missing")
        return
    migration = migration_path.read_text(encoding="utf-8")
    for required in (
        '"import-secure"',
        '"keep-user-managed"',
        '"start-fresh"',
        '"session-active"',
        '"legacy-backup.json"',
        '"post-migration-verification"',
        'candidate["sourceIds"] = []',
        "backupRetained",
    ):
        if required not in migration:
            errors.append(f"migration.py: missing transactional control: {required}")
    panel = (ROOT / "Panel.qml").read_text(encoding="utf-8")
    for required in (
        "LEGACY MIGRATION REQUIRED",
        "Import & secure",
        "Keep user-managed",
        "Start fresh",
    ):
        if required not in panel:
            errors.append(f"Panel.qml: missing guided migration choice: {required}")


def check_verified_removal(errors: list[str]) -> None:
    removal_path = ROOT / "src" / "ssh_mixer" / "removal.py"
    if not removal_path.is_file():
        errors.append("src/ssh_mixer/removal.py: required removal seam is missing")
        return
    removal = removal_path.read_text(encoding="utf-8")
    for required in (
        "ABANDON WITHOUT VERIFIED REVOCATION",
        '"receiver-offline"',
        '"remoteCleanupVerified"',
        '"abandoned-not-revoked"',
        "omarchy-plugin-remove",
        "pending-removals.json",
        "helperRemoved",
    ):
        if required not in removal:
            errors.append(f"removal.py: missing verified cleanup control: {required}")
    session = (ROOT / "src" / "ssh_mixer" / "session.py").read_text(
        encoding="utf-8"
    )
    if "require_no_pending_removal" not in session:
        errors.append("session.py: pending Receiver cleanup must block new Sessions")
    panel = (ROOT / "Panel.qml").read_text(encoding="utf-8")
    for required in (
        "VERIFIED REMOVAL",
        "Retry Connection cleanup",
        "Plan full uninstall",
        "Abandon — not revoked",
    ):
        if required not in panel:
            errors.append(f"Panel.qml: missing guided removal control: {required}")
    artifacts = (
        ROOT / "receiver" / "linux" / "setup-v1.sh",
        ROOT / "receiver" / "windows" / "setup-v1.ps1",
        ROOT / "receiver" / "macos" / "setup-v1.sh",
    )
    for artifact in artifacts:
        text = artifact.read_text(encoding="utf-8")
        lowered = text.lower()
        for required in ("remove", "keyRevoked", "helperRemoved"):
            if required.lower() not in lowered:
                errors.append(
                    f"{artifact.relative_to(ROOT)}: missing Companion removal control: {required}"
                )


def check_public_documentation(errors: list[str]) -> None:
    required_documents = {
        "SECURITY.md": (
            "security/advisories/new",
            "Do not open a public issue",
            "docs/security-model.md",
        ),
        "CONTRIBUTING.md": (
            "git commit -s",
            "Signed-off-by:",
            "maintainer",
            "Platform-fix expectations",
        ),
        "docs/security-model.md": (
            "Omarchy plugins run unsandboxed",
            "Commands executed",
            "Files written",
            "Audio access",
            "Network access",
            "Persistence",
            "Limitations and non-goals",
        ),
        "docs/user-guide.md": (
            "Tailscale Connection",
            "Direct SSH Connection",
            "OpenSSH Profile Connection",
            "Review host trust",
            "Quiet test",
            "Session privacy and lock behavior",
            "Diagnostics and normal failure reports",
            "Contribute a fix",
            "Minimal",
            "Extended",
            "Remove a Connection or uninstall",
        ),
        "docs/releasing.md": (
            "Clean public-history candidate",
            "check_public_history.py",
            "immutable",
            "OpenSSH",
            "SHA-256",
            "attestations",
            "Protocol",
            "rollback",
            "Manual publication approval",
        ),
        "docs/release-readiness.md": (
            "Standards",
            "Specification",
            "Security and privacy",
            "Supply chain and repository controls",
            "Blockers requiring evidence or maintainer approval",
        ),
        "docs/testing/smoke-tests.md": (
            "Linux source and Linux Receiver",
            "Linux source and Windows Receiver",
            "Future real-device macOS procedure",
            "experimental: true",
            "realDeviceVerified: false",
        ),
    }
    for relative, markers in required_documents.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"{relative}: required public trust documentation is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                errors.append(f"{relative}: missing public trust topic: {marker}")
    panel = (ROOT / "Panel.qml").read_text(encoding="utf-8")
    for marker in ("DIAGNOSTICS & CONTRIBUTING", "Contribute a fix", "RetentionButton"):
        if marker not in panel:
            errors.append(f"Panel.qml: missing public diagnostic control: {marker}")


def check_release_trust_root(errors: list[str]) -> None:
    path = ROOT / "release" / "allowed_signers"
    if not path.is_file() or path.is_symlink():
        errors.append("release/allowed_signers: reviewed production trust root is missing or unsafe")
        return
    if path.stat().st_mode & 0o022:
        errors.append("release/allowed_signers: trust root must not be group/world writable")
    fields = path.read_text(encoding="utf-8").strip().split()
    if len(fields) != 4 or fields[:3] != [
        "ssh-mixer-release",
        'namespaces="ssh-mixer-release"',
        "ssh-ed25519",
    ]:
        errors.append("release/allowed_signers: principal, namespace, or key type is invalid")
        return
    try:
        key_blob = base64.b64decode(fields[3], validate=True)
    except (ValueError, binascii.Error):
        errors.append("release/allowed_signers: public key body is invalid")
        return
    fingerprint = "SHA256:" + base64.b64encode(
        hashlib.sha256(key_blob).digest()
    ).decode("ascii").rstrip("=")
    if fingerprint != PRODUCTION_SIGNER_FINGERPRINT:
        errors.append("release/allowed_signers: production signer fingerprint changed")


def check_release_signing_wizard(errors: list[str]) -> None:
    path = ROOT / "scripts" / "setup_release_signing.sh"
    if not path.is_file() or path.is_symlink():
        errors.append("scripts/setup_release_signing.sh: durable signing wizard is missing or unsafe")
        return
    if not path.stat().st_mode & 0o100:
        errors.append("scripts/setup_release_signing.sh: signing wizard must be executable")
    text = path.read_text(encoding="utf-8")
    for marker in (
        "ssh-keygen -t ed25519 -a 100",
        "ssh-keygen -Y sign",
        "ssh-keygen -Y verify",
        'namespaces=\\\"ssh-mixer-release\\\"',
        "RELEASE_SIGNING_KEY_PATH",
        "release/allowed_signers",
        "empty passphrase",
    ):
        if marker not in text:
            errors.append(f"scripts/setup_release_signing.sh: missing signing control: {marker}")
    for forbidden in ("git push", "git tag", "gh release create"):
        if forbidden in text:
            errors.append(
                f"scripts/setup_release_signing.sh: publication command is forbidden: {forbidden}"
            )


def check_symlinks(files: list[Path], errors: list[str]) -> None:
    for path in files:
        if path.is_symlink():
            errors.append(f"{path.relative_to(ROOT)}: tracked symlinks are not allowed")


def main() -> int:
    errors: list[str] = []
    files = tracked_files()
    check_manifest(errors)
    check_links(files, errors)
    check_workflows(files, errors)
    check_sensitive_content(files, errors)
    check_windows_receiver(errors)
    check_macos_receiver(errors)
    check_update_versions(errors)
    check_privacy_lifecycle(errors)
    check_legacy_migration(errors)
    check_verified_removal(errors)
    check_public_documentation(errors)
    check_release_trust_root(errors)
    check_release_signing_wizard(errors)
    check_symlinks(files, errors)
    if errors:
        print("repository checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"repository checks passed ({len(files)} tracked files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

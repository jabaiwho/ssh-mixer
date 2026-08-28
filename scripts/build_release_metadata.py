#!/usr/bin/env python3
"""Build deterministic unsigned metadata for a manually approved Receiver release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {
    ("linux", "companion"): ROOT / "receiver" / "linux" / "setup-v1.sh",
    ("linux", "receiver"): ROOT / "receiver" / "linux" / "ssh-mixer-receiver-v1.py",
    ("windows", "companion"): ROOT / "receiver" / "windows" / "setup-v1.ps1",
    ("windows", "receiver"): ROOT / "receiver" / "windows" / "ssh-mixer-receiver-v1.ps1",
    ("macos", "companion"): ROOT / "receiver" / "macos" / "setup-v1.sh",
    ("macos", "receiver"): ROOT / "receiver" / "macos" / "ssh-mixer-receiver-v1",
}
EXTENSIONS = {
    ("linux", "companion"): "sh",
    ("linux", "receiver"): "py",
    ("windows", "companion"): "ps1",
    ("windows", "receiver"): "ps1",
    ("macos", "companion"): "sh",
    ("macos", "receiver"): "sh",
}
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--published-at", required=True, help="UTC timestamp ending in Z")
    parser.add_argument("--changes", required=True, type=Path, help="Reviewed JSON object keyed by platform/kind")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    args = parser.parse_args()
    if not SEMVER.fullmatch(args.version):
        parser.error("--version must be stable semantic versioning")
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        parser.error("--commit must be a full lowercase commit id")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", args.published_at):
        parser.error("--published-at must be a UTC second timestamp")
    changes = json.loads(args.changes.read_text(encoding="utf-8"))
    if not isinstance(changes, dict):
        parser.error("--changes must contain a JSON object")
    tag = f"receiver-v{args.version}"
    artifacts: list[dict[str, object]] = []
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    for (platform, kind), path in sorted(ARTIFACTS.items()):
        key = f"{platform}/{kind}"
        reviewed = changes.get(key)
        if not isinstance(reviewed, list) or not reviewed or not all(
            isinstance(item, str) and item.strip() for item in reviewed
        ):
            parser.error(f"--changes is missing reviewed entries for {key}")
        content = path.read_bytes()
        filename = (
            f"ssh-mixer-{platform}-{kind}-v{args.version}."
            f"{EXTENSIONS[(platform, kind)]}"
        )
        output_artifact = args.artifact_dir / filename
        output_artifact.write_bytes(content)
        output_artifact.chmod(0o644)
        artifacts.append(
            {
                "platform": platform,
                "kind": kind,
                "version": args.version,
                "protocolMinimum": 1,
                "protocolMaximum": 1,
                "url": (
                    "https://github.com/jabaiwho/ssh-mixer/releases/download/"
                    f"{tag}/{filename}"
                ),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "changes": [item.strip() for item in reviewed],
                "sourceCommit": args.commit,
            }
        )
    metadata = {
        "schemaVersion": 1,
        "releaseId": f"{tag}+{args.commit}",
        "pluginVersion": "0.1.1",
        "publishedAt": args.published_at,
        "artifacts": artifacts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(
        "Review metadata, then sign manually: "
        f"ssh-keygen -Y sign -f RELEASE_SIGNING_KEY -n ssh-mixer-release {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

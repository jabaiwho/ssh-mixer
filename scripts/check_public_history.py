#!/usr/bin/env python3
"""Audit the history intended for the initial public SSH-mixer repository."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

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
FORBIDDEN_PATH = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|"
    r"[^/]*\.(?:log|key|pem|p12|pfx|kdbx)|authorized_keys|known_hosts)$"
)
DCO = re.compile(r"(?m)^Signed-off-by: (?!Name <email@example\.com>$).+ <[^<>\s]+@[^<>\s]+>$")


def git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments],
        input=input_bytes,
        stderr=subprocess.STDOUT,
    )


def commits(repository: Path) -> list[str]:
    return git(repository, "rev-list", "--reverse", "HEAD").decode().splitlines()


def audit_dco(repository: Path, history: list[str], errors: list[str]) -> None:
    for commit in history:
        message = git(repository, "show", "-s", "--format=%B", commit).decode(
            errors="replace"
        )
        if not DCO.search(message):
            errors.append(f"{commit}: commit is missing a valid DCO Signed-off-by line")


def audit_objects(repository: Path, errors: list[str]) -> None:
    listed = git(repository, "rev-list", "--objects", "HEAD").decode(
        errors="surrogateescape"
    )
    checked: set[str] = set()
    for line in listed.splitlines():
        object_id, separator, path = line.partition(" ")
        if separator and FORBIDDEN_PATH.search(path):
            errors.append(f"{object_id} {path}: sensitive runtime/artifact path is in history")
        if object_id in checked:
            continue
        object_type = git(repository, "cat-file", "-t", object_id).strip()
        if object_type != b"blob":
            continue
        checked.add(object_id)
        content = git(repository, "cat-file", "blob", object_id)
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"{object_id}: historical blob contains a possible {name}")
        if PRIVATE_DEFAULTS.search(content):
            errors.append(f"{object_id}: historical blob contains a private development default")


def verify_tag(repository: Path, tag: str, errors: list[str]) -> None:
    try:
        tagged = git(repository, "rev-list", "-n", "1", tag).decode().strip()
        head = git(repository, "rev-parse", "HEAD").decode().strip()
        if tagged != head:
            errors.append(f"{tag}: release tag does not point to HEAD")
        subprocess.run(
            ["git", "-C", str(repository), "verify-tag", tag],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError:
        errors.append(f"{tag}: release tag is missing or its cryptographic signature failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--initial-release",
        action="store_true",
        help="also require a one-commit clean-root public history",
    )
    parser.add_argument("--tag", help="also verify a signed release tag at HEAD")
    args = parser.parse_args()
    repository = args.repository.resolve()
    errors: list[str] = []
    try:
        history = commits(repository)
        if not history:
            errors.append("HEAD has no commits")
        if args.initial_release and len(history) != 1:
            errors.append(
                "initial public history must contain exactly one reviewed clean-root commit; "
                f"found {len(history)}"
            )
        audit_dco(repository, history, errors)
        audit_objects(repository, errors)
        if args.tag:
            verify_tag(repository, args.tag, errors)
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"history audit could not complete: {exc}")

    if errors:
        print("public history audit failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"public history audit passed ({len(history)} commits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

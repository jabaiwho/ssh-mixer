from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE

RECEIVER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PUBLIC_KEY_RE = re.compile(r"^(ssh-ed25519) ([A-Za-z0-9+/]+={0,3})(?:\s+.*)?$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


class IdentityError(ValueError):
    """Raised when a Managed Identity cannot be created safely."""


def _default_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **kwargs)


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise IdentityError(f"Managed Identity directory may not be a symlink: {path}")
    path.mkdir(parents=True, mode=PRIVATE_DIR_MODE, exist_ok=True)
    path.chmod(PRIVATE_DIR_MODE)


class ManagedIdentityStore:
    """Owns dedicated per-Receiver OpenSSH identities without exposing key data."""

    def __init__(self, root: Path, *, runner: Runner = _default_runner) -> None:
        self.root = root
        self.runner = runner

    def generate(self, receiver_id: str, *, encrypted: bool) -> dict[str, Any]:
        if not RECEIVER_ID_RE.fullmatch(receiver_id):
            raise IdentityError("Receiver identity id is invalid")
        if encrypted and not os.environ.get("SSH_AUTH_SOCK"):
            raise IdentityError("encrypted Managed Identities require a running ssh-agent")
        _secure_directory(self.root)
        receiver_dir = self.root / receiver_id
        if receiver_dir.is_symlink():
            raise IdentityError("Managed Identity path may not be a symlink")
        if receiver_dir.exists():
            raise IdentityError("a Managed Identity already exists for this Receiver")

        temporary = Path(tempfile.mkdtemp(prefix=f".{receiver_id}-", dir=self.root))
        temporary.chmod(PRIVATE_DIR_MODE)
        key_path = temporary / "id_ed25519"
        command = [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-f",
            str(key_path),
            "-C",
            f"ssh-mixer:{receiver_id}",
        ]
        if not encrypted:
            command.extend(["-N", ""])
        try:
            completed = self.runner(command, check=False)
            if completed is None or completed.returncode != 0:
                raise IdentityError("ssh-keygen did not create the Managed Identity")
            public_path = key_path.with_suffix(".pub")
            if key_path.is_symlink() or public_path.is_symlink():
                raise IdentityError("ssh-keygen produced an unsafe identity path")
            if not key_path.is_file() or not public_path.is_file():
                raise IdentityError("ssh-keygen did not produce both identity files")
            public_key = public_path.read_text(encoding="utf-8").strip()
            if not PUBLIC_KEY_RE.fullmatch(public_key):
                raise IdentityError("ssh-keygen produced an unsupported public key")
            key_path.chmod(PRIVATE_FILE_MODE)
            public_path.chmod(0o644)
            temporary.rename(receiver_dir)
            final_key = receiver_dir / key_path.name
            final_public = receiver_dir / public_path.name
            if encrypted:
                added = self.runner(["ssh-add", str(final_key)], check=False)
                if added is None or added.returncode != 0:
                    shutil.rmtree(receiver_dir)
                    raise IdentityError("ssh-agent did not accept the encrypted Managed Identity")
            return {
                "schemaVersion": 1,
                "receiverId": receiver_id,
                "privateKeyPath": str(final_key),
                "publicKeyPath": str(final_public),
                "publicKey": public_key,
                "encrypted": encrypted,
                "agentBacked": encrypted,
                "securityLevel": "receiver-only",
            }
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def load(self, receiver_id: str) -> dict[str, Any]:
        if not RECEIVER_ID_RE.fullmatch(receiver_id):
            raise IdentityError("Receiver identity id is invalid")
        if self.root.is_symlink():
            raise IdentityError("Managed Identity root may not be a symlink")
        receiver_dir = self.root / receiver_id
        private_path = receiver_dir / "id_ed25519"
        public_path = receiver_dir / "id_ed25519.pub"
        if (
            receiver_dir.is_symlink()
            or not receiver_dir.is_dir()
            or private_path.is_symlink()
            or not private_path.is_file()
            or private_path.stat().st_mode & 0o077
            or public_path.is_symlink()
            or not public_path.is_file()
        ):
            raise IdentityError("Managed Identity files are missing or unsafe")
        public_key = public_path.read_text(encoding="utf-8").strip()
        if not PUBLIC_KEY_RE.fullmatch(public_key):
            raise IdentityError("Managed Identity public key is invalid")
        return {
            "schemaVersion": 1,
            "receiverId": receiver_id,
            "privateKeyPath": str(private_path),
            "publicKeyPath": str(public_path),
            "publicKey": public_key,
            "securityLevel": "receiver-only",
        }

    def revoke_local(self, receiver_id: str) -> bool:
        if not RECEIVER_ID_RE.fullmatch(receiver_id):
            raise IdentityError("Receiver identity id is invalid")
        if self.root.is_symlink():
            raise IdentityError("Managed Identity root may not be a symlink")
        receiver_dir = self.root / receiver_id
        if receiver_dir.is_symlink():
            raise IdentityError("Managed Identity path may not be a symlink")
        if not receiver_dir.exists():
            return False
        if not receiver_dir.is_dir():
            raise IdentityError("Managed Identity path is not a directory")
        shutil.rmtree(receiver_dir)
        return True

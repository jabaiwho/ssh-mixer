#!/usr/bin/env python3
"""SSH-mixer Linux Receiver Protocol v1.

This standalone file is installed from a checksummed release artifact and is the
only forced command available to a Managed Identity. It never dispatches a
shell or evaluates receiver-provided command text.
"""

from __future__ import annotations

import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

PROTOCOL = "ssh-mixer-receiver"
VERSION = "v1"
PROTOCOL_VERSION = 1
HELPER_VERSION = "1.1.1"
QUIET_START_DBFS = -40
QUIET_MAX_DBFS = -24
QUIET_STEP_DB = 4
QUIET_DURATION_SECONDS = 0.5
QUIET_FADE_SECONDS = 0.08


class ProtocolError(ValueError):
    pass


class Operation(NamedTuple):
    operation: str
    dbfs: int | None = None


def parse_operation(command: str) -> Operation:
    try:
        arguments = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ProtocolError("malformed Receiver Protocol command") from exc
    if len(arguments) < 3 or arguments[:2] != [PROTOCOL, VERSION]:
        raise ProtocolError("Receiver Protocol v1 is required")
    operation = arguments[2]
    if operation in {"capabilities", "diagnostics", "play", "remove"}:
        if len(arguments) != 3:
            raise ProtocolError("operation does not accept arguments")
        return Operation(operation)
    if operation == "quiet-test":
        if len(arguments) != 5 or arguments[3] != "--dbfs":
            raise ProtocolError("quiet-test requires --dbfs LEVEL")
        try:
            level = int(arguments[4])
        except ValueError as exc:
            raise ProtocolError("quiet-test level must be an integer") from exc
        return Operation(operation, level)
    raise ProtocolError("operation is not allowed")


def quiet_test_settings(dbfs: int, *, previous_dbfs: int | None) -> dict[str, object]:
    if previous_dbfs is None:
        if dbfs != QUIET_START_DBFS:
            raise ProtocolError("the first quiet test must start at -40 dBFS")
    elif dbfs not in {QUIET_START_DBFS, previous_dbfs, previous_dbfs + QUIET_STEP_DB}:
        raise ProtocolError("quiet test increases must use 4 dB steps")
    if dbfs < QUIET_START_DBFS or dbfs > QUIET_MAX_DBFS:
        raise ProtocolError("quiet test level must be between -40 and -24 dBFS")
    return {
        "dbfs": dbfs,
        "durationSeconds": QUIET_DURATION_SECONDS,
        "fadeInSeconds": QUIET_FADE_SECONDS,
        "fadeOutSeconds": QUIET_FADE_SECONDS,
        "loop": False,
        "changesSystemVolume": False,
    }


def state_path() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "ssh-mixer" / "quiet-test-v1.json"


def previous_quiet_level() -> int | None:
    path = state_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    level = value.get("dbfs") if isinstance(value, dict) else None
    return level if isinstance(level, int) else None


def save_quiet_level(dbfs: int) -> None:
    path = state_path()
    if path.parent.is_symlink() or path.is_symlink():
        raise ProtocolError("quiet test state path is unsafe")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps({"schemaVersion": 1, "dbfs": dbfs}) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def capabilities() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "protocol": VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "helperVersion": HELPER_VERSION,
        "platform": "linux",
        "operations": ["capabilities", "diagnostics", "play", "quiet-test", "remove"],
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffplay": shutil.which("ffplay") is not None,
        "quietTest": {
            "startDbfs": QUIET_START_DBFS,
            "maximumDbfs": QUIET_MAX_DBFS,
            "stepDb": QUIET_STEP_DB,
            "durationSeconds": QUIET_DURATION_SECONDS,
        },
    }


def diagnostics() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "protocol": VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "helperVersion": HELPER_VERSION,
        "platform": "linux",
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "ffmpegAvailable": shutil.which("ffmpeg") is not None,
        "ffplayAvailable": shutil.which("ffplay") is not None,
    }


def play() -> int:
    executable = shutil.which("ffplay")
    if not executable:
        raise ProtocolError("ffplay is unavailable")
    os.execv(
        executable,
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nodisp",
            "-autoexit",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-sync",
            "ext",
            "-f",
            "ogg",
            "-",
        ],
    )
    return 70


def run_quiet_test(dbfs: int) -> int:
    settings = quiet_test_settings(dbfs, previous_dbfs=previous_quiet_level())
    ffmpeg = shutil.which("ffmpeg")
    ffplay = shutil.which("ffplay")
    if not ffmpeg or not ffplay:
        raise ProtocolError("quiet test requires ffmpeg and ffplay")
    amplitude = math.pow(10.0, dbfs / 20.0)
    source = (
        f"aevalsrc={amplitude:.8f}*sin(2*PI*440*t):"
        f"s=48000:d={QUIET_DURATION_SECONDS}"
    )
    fade_out_start = QUIET_DURATION_SECONDS - QUIET_FADE_SECONDS
    producer = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-af",
            (
                f"afade=t=in:st=0:d={QUIET_FADE_SECONDS},"
                f"afade=t=out:st={fade_out_start}:d={QUIET_FADE_SECONDS}"
            ),
            "-t",
            str(QUIET_DURATION_SECONDS),
            "-f",
            "wav",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert producer.stdout is not None
    consumer = subprocess.run(
        [ffplay, "-hide_banner", "-loglevel", "error", "-nodisp", "-autoexit", "-"],
        stdin=producer.stdout,
        check=False,
    )
    producer.stdout.close()
    producer_status = producer.wait()
    if producer_status != 0 or consumer.returncode != 0:
        raise ProtocolError("quiet test playback failed")
    save_quiet_level(int(settings["dbfs"]))
    return 0


def remove(key_body: str) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,3}", key_body):
        raise ProtocolError("Managed Identity key body is invalid")
    authorized_keys = Path.home() / ".ssh" / "authorized_keys"
    suffix = f" ssh-ed25519 {key_body} ssh-mixer-managed-v1"
    if authorized_keys.exists():
        if (
            authorized_keys.parent.is_symlink()
            or authorized_keys.is_symlink()
            or not authorized_keys.is_file()
        ):
            raise ProtocolError("authorized_keys path is unsafe")
        lines = authorized_keys.read_text(encoding="utf-8").splitlines()
        retained = [line for line in lines if not line.endswith(suffix)]
        fd, temporary_name = tempfile.mkstemp(
            prefix=".authorized_keys.remove-", dir=authorized_keys.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write("\n".join(retained) + ("\n" if retained else ""))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, authorized_keys)
        finally:
            temporary.unlink(missing_ok=True)
        if any(line.endswith(suffix) for line in authorized_keys.read_text(encoding="utf-8").splitlines()):
            raise ProtocolError("Managed Identity key revocation could not be verified")
    remaining = []
    if authorized_keys.is_file() and not authorized_keys.is_symlink():
        remaining = [
            line
            for line in authorized_keys.read_text(encoding="utf-8").splitlines()
            if line.endswith(" ssh-mixer-managed-v1")
        ]
    helper_removed = False
    if not remaining:
        quiet_state = state_path()
        quiet_state.unlink(missing_ok=True)
        try:
            quiet_state.parent.rmdir()
        except OSError:
            pass
        helper = Path(__file__)
        if helper.is_symlink() or not helper.is_file():
            raise ProtocolError("Receiver helper path is unsafe")
        helper.unlink()
        helper_removed = not helper.exists() and not quiet_state.exists()
        if not helper_removed:
            raise ProtocolError("unshared Receiver state removal could not be verified")
    return {
        "schemaVersion": 1,
        "ok": True,
        "keyRevoked": True,
        "helperRemoved": helper_removed,
    }


def emit_error(message: str) -> None:
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "ok": False,
                "stage": "receiver.protocol",
                "code": "protocol-rejected",
                "message": message,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def main() -> int:
    if (
        len(sys.argv) != 4
        or sys.argv[1:3] != ["--forced", "--key"]
        or not re.fullmatch(r"[A-Za-z0-9+/]+={0,3}", sys.argv[3])
    ):
        emit_error("Receiver Protocol requires its fixed Managed Identity context")
        return 64
    key_body = sys.argv[3]
    try:
        request = parse_operation(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        if request.operation == "capabilities":
            print(json.dumps(capabilities(), sort_keys=True))
            return 0
        if request.operation == "diagnostics":
            print(json.dumps(diagnostics(), sort_keys=True))
            return 0
        if request.operation == "play":
            return play()
        if request.operation == "remove":
            print(json.dumps(remove(key_body), sort_keys=True))
            return 0
        if request.operation == "quiet-test" and request.dbfs is not None:
            return run_quiet_test(request.dbfs)
        raise ProtocolError("operation is not implemented")
    except ProtocolError as exc:
        emit_error(str(exc))
        return 64


if __name__ == "__main__":
    raise SystemExit(main())

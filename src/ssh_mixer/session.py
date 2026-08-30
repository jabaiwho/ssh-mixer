from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audio import (
    discover_sources,
    matchers_for_source_ids,
    parse_pactl_objects,
    resolve_source_ids,
)
from .config import (
    DEFAULT_RECEIVER_COMMAND,
    ensure_dirs,
    expand_user_path,
    load_config,
    lock_path,
    logs_dir,
    public_config,
    save_config,
    secure_open_lock,
    secure_write_text,
    state_dir,
    state_path,
    trust_dir,
)
from .connections import (
    discover_tailscale_peers,
    normalize_connection,
    verify_tailscale_peer,
)
from .diagnostics import DiagnosticStore, redact
from .openssh_profiles import inspect_profile, profile_connection
from .routing import (
    DEFAULT_MIX_SINK,
    RoutingError,
    build_playback_reconciliation,
    build_route_plan,
    resolve_session_source_ids,
)
from .streaming import StreamEpochPolicy, StreamSilenceState, build_encoder_command
from .versions import PROTOCOL_VERSION

ACTIVE_STATES = {"starting", "streaming", "local", "stopping"}


class SessionError(RuntimeError):
    """Raised for user-facing session failures."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def session_lock() -> Any:
    ensure_dirs()
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with secure_open_lock(path) as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield


def read_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return stopped_state()
    if path.is_symlink():
        return stopped_state(error="Unsafe session state path")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return stopped_state(error="Could not read session state")
    if not isinstance(loaded, dict):
        return stopped_state(error="Invalid session state")
    return loaded


def write_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    state = dict(state)
    state["updatedAt"] = utc_now()
    secure_write_text(state_path(), json.dumps(state, indent=2, sort_keys=True) + "\n")


def stopped_state(error: str = "") -> dict[str, Any]:
    state: dict[str, Any] = {
        "schemaVersion": 1,
        "state": "stopped",
        "active": False,
        "pid": None,
        "sessionId": "",
        "startedAt": "",
        "updatedAt": utc_now(),
        "destination": "",
        "selectedInputs": [],
        "captureActive": False,
        "stopReason": "",
        "resources": empty_resources(),
        "remote": {},
        "error": error,
        "logPath": "",
    }
    return state


def empty_resources() -> dict[str, Any]:
    return {
        "modules": [],
        "movedSinkInputs": [],
        "processes": [],
        "mixSink": "",
        "defaultSink": "",
    }


def process_identity(pid: int, *, proc_root: Path = Path("/proc")) -> dict[str, Any] | None:
    process_dir = proc_root / str(pid)
    try:
        stat_text = (process_dir / "stat").read_text(encoding="utf-8")
        command_end = stat_text.rfind(")")
        if command_end < 0:
            return None
        fields = stat_text[command_end + 2 :].split()
        # The remainder begins at proc field 3; process start time is field 22.
        start_time_ticks = fields[19]
        executable = os.readlink(process_dir / "exe")
    except (IndexError, OSError):
        return None
    return {
        "pid": int(pid),
        "startTimeTicks": str(start_time_ticks),
        "executable": str(Path(executable).resolve(strict=False)),
    }


def process_matches_identity(
    tracked: dict[str, Any], *, proc_root: Path = Path("/proc")
) -> bool:
    try:
        pid = int(tracked.get("pid"))
    except (TypeError, ValueError):
        return False
    current = process_identity(pid, proc_root=proc_root)
    if current is None:
        return False
    return (
        bool(tracked.get("startTimeTicks"))
        and bool(tracked.get("executable"))
        and str(tracked["startTimeTicks"]) == current["startTimeTicks"]
        and str(Path(str(tracked["executable"])).resolve(strict=False))
        == current["executable"]
    )


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_matches_session(pid: int | None, session_id: str) -> bool:
    if not pid_alive(pid):
        return False
    if not session_id:
        return True
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        cmdline = cmdline_path.read_text(errors="ignore").replace("\x00", " ")
    except OSError:
        return False
    return session_id in cmdline and "ssh-mixer" in cmdline


def normalize_status(state: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(state or read_state())
    session_state = str(raw.get("state", "stopped"))
    pid = raw.get("pid")
    try:
        pid_int = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid_int = None
    session_id = str(raw.get("sessionId", ""))
    active = session_state in ACTIVE_STATES and pid_matches_session(pid_int, session_id)

    if session_state in ACTIVE_STATES and not active:
        session_state = "error"
        raw["state"] = "error"
        raw["active"] = False
        raw["error"] = raw.get("error") or "Session process is gone; Stop will clean any tracked resources."
    else:
        raw["active"] = active
    return raw


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def require_commands(names: list[str]) -> None:
    missing = [name for name in names if not command_exists(name)]
    if missing:
        raise SessionError("missing required command(s): " + ", ".join(missing))


def read_encoder_diagnostics(stream: Any) -> str:
    try:
        output = os.read(stream.fileno(), 65_536)
    except (BlockingIOError, OSError):
        return ""
    return output.decode("utf-8", errors="replace")


def receiver_protocol_error(output: str) -> str:
    """Extract one bounded structured Receiver error from SSH stderr."""
    bounded = output[-16_384:]
    for line in reversed(bounded.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("ok") is False:
            message = str(payload.get("message", "")).strip()
            if message:
                return message[-4_096:]
    return ""


def run_pactl(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pactl", *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def sink_exists(name: str) -> bool:
    completed = run_pactl(["list", "short", "sinks"], check=False)
    if completed.returncode != 0:
        return False
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def default_sink_name() -> str:
    completed = run_pactl(["get-default-sink"])
    name = completed.stdout.strip()
    if not name:
        raise SessionError("pactl did not report a default sink")
    return name


def source_exists(name: str) -> bool:
    completed = run_pactl(["list", "short", "sources"], check=False)
    if completed.returncode != 0:
        return False
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def unload_module(module_id: str) -> None:
    if not module_id:
        return
    run_pactl(["unload-module", str(module_id)], check=False)


def _field(obj: dict[str, Any], name: str) -> str:
    return str(obj.get("fields", {}).get(name, ""))


def _module_is_owned(module: dict[str, Any], module_id: str, mix_sink: str) -> bool:
    if str(module.get("id", "")) != module_id or not mix_sink:
        return False
    name = _field(module, "Name")
    argument_tokens = set(_field(module, "Argument").split())
    if name == "module-null-sink":
        return f"sink_name={mix_sink}" in argument_tokens
    if name == "module-loopback":
        return (
            f"sink={mix_sink}" in argument_tokens
            or f"source={mix_sink}.monitor" in argument_tokens
        )
    return False


def cleanup_resources(state: dict[str, Any]) -> None:
    resources = state.get("resources") if isinstance(state.get("resources"), dict) else {}
    mix_sink = str(resources.get("mixSink", ""))

    sinks_result = run_pactl(["list", "sinks"], check=False)
    inputs_result = run_pactl(["list", "sink-inputs"], check=False)
    modules_result = run_pactl(["list", "modules"], check=False)
    sinks = parse_pactl_objects(sinks_result.stdout, "Sink") if sinks_result.returncode == 0 else []
    inputs = (
        parse_pactl_objects(inputs_result.stdout, "Sink Input")
        if inputs_result.returncode == 0
        else []
    )
    modules = (
        parse_pactl_objects(modules_result.stdout, "Module")
        if modules_result.returncode == 0
        else []
    )
    default_result = run_pactl(["get-default-sink"], check=False)
    restore_tracked_default = (
        default_result.returncode == 0
        and bool(mix_sink)
        and default_result.stdout.strip() == mix_sink
    )
    mix_sink_ids = {
        str(sink.get("id", "")) for sink in sinks if _field(sink, "Name") == mix_sink
    }

    for moved in reversed(resources.get("movedSinkInputs", []) or []):
        sink_input_id = str(moved.get("sinkInputId", ""))
        original_sink = str(moved.get("originalSink", ""))
        current = next(
            (item for item in inputs if str(item.get("id", "")) == sink_input_id),
            None,
        )
        if (
            current is not None
            and original_sink
            and _field(current, "Sink") in mix_sink_ids
        ):
            run_pactl(["move-sink-input", sink_input_id, original_sink], check=False)

    for tracked in reversed(resources.get("modules", []) or []):
        module_id = str(tracked.get("id", ""))
        if any(_module_is_owned(module, module_id, mix_sink) for module in modules):
            unload_module(module_id)

    default_sink = str(resources.get("defaultSink", ""))
    if (
        restore_tracked_default
        and default_sink
        and default_sink != mix_sink
        and sink_exists(default_sink)
    ):
        run_pactl(["set-default-sink", default_sink], check=False)


def cleanup_named_mix(mix_sink: str = DEFAULT_MIX_SINK) -> None:
    """Best-effort cleanup when an older tracked state is stale or missing."""

    completed = run_pactl(["list", "modules"], check=False)
    if completed.returncode != 0:
        return
    modules = parse_pactl_objects(completed.stdout, "Module")

    loopbacks: list[str] = []
    nulls: list[str] = []
    for module in modules:
        module_id = str(module.get("id", ""))
        name = _field(module, "Name")
        argument_tokens = set(_field(module, "Argument").split())
        owns_loopback = (
            f"sink={mix_sink}" in argument_tokens
            or f"source={mix_sink}.monitor" in argument_tokens
        )
        if name == "module-loopback" and owns_loopback:
            loopbacks.append(module_id)
        elif name == "module-null-sink" and f"sink_name={mix_sink}" in argument_tokens:
            nulls.append(module_id)
    for module_id in loopbacks:
        unload_module(module_id)
    for module_id in nulls:
        unload_module(module_id)


def kill_process_group(pid: int, sig: signal.Signals) -> None:
    """Signal a process and, when safe, the process group it currently owns.

    OpenSSH can leave the tracked child in a different process group than its
    PID on this desktop. Looking up the live pgid avoids leaked remote ffplay
    SSH processes while skipping the caller's own group during worker cleanup.
    """

    with suppress(ProcessLookupError, PermissionError):
        pgid = os.getpgid(pid)
        if pgid != os.getpgrp():
            os.killpg(pgid, sig)
    with suppress(ProcessLookupError, PermissionError):
        os.kill(pid, sig)


def terminate_processes(processes: list[dict[str, Any]], timeout: float = 3.0) -> None:
    pids: list[int] = []
    for process in processes:
        try:
            pid = int(process.get("pid"))
        except (TypeError, ValueError):
            continue
        if process_matches_identity(process) and pid_alive(pid):
            pids.append(pid)
    for pid in pids:
        kill_process_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_alive(pid) for pid in pids):
            return
        time.sleep(0.1)
    for pid in pids:
        if pid_alive(pid):
            kill_process_group(pid, signal.SIGKILL)


def resolve_remote(remote: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(remote)
    connection_value = resolved.get("connection")
    if isinstance(connection_value, dict):
        try:
            connection = normalize_connection(connection_value)
            if connection["type"] == "tailscale":
                verified = verify_tailscale_peer(
                    connection,
                    discover_tailscale_peers(),
                )
                host = str(verified["address"])
            elif connection["type"] == "openssh-profile":
                inspected = inspect_profile(str(connection["profile"]))
                connection = normalize_connection(
                    profile_connection(
                        inspected,
                        proxy_confirmed=bool(connection.get("proxyConfirmed", False)),
                        expected_proxy_hash=str(connection.get("proxyCommandHash", "")),
                        expected_effective_hash=str(connection.get("effectiveConfigHash", "")),
                    )
                )
                host = str(connection["host"])
            else:
                host = str(connection["host"])
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
    else:
        try:
            connection = normalize_connection(
                {
                    "type": "direct",
                    "host": resolved.get("host", ""),
                    "user": resolved.get("user", ""),
                    "port": resolved.get("port", 22),
                }
            )
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        host = str(connection["host"])
    user = str(connection["user"])
    key_path = expand_user_path(str(resolved.get("keyPath", "")).strip())
    if connection["type"] != "openssh-profile":
        if not key_path:
            raise SessionError("SSH key path is required")
        if not Path(key_path).is_file():
            raise SessionError(f"SSH key is not readable: {key_path}")
    resolved["host"] = host
    resolved["hostKeyAlias"] = str(connection["host"])
    resolved["user"] = user
    resolved["port"] = int(connection["port"])
    resolved["connection"] = connection
    resolved["profile"] = str(connection.get("profile", ""))
    resolved["keyPath"] = key_path
    resolved["receiverCommand"] = (
        str(resolved.get("receiverCommand", DEFAULT_RECEIVER_COMMAND)).strip()
        or DEFAULT_RECEIVER_COMMAND
    )
    try:
        timeout = int(resolved.get("connectTimeoutSeconds", 5))
    except (TypeError, ValueError):
        timeout = 5
    resolved["connectTimeoutSeconds"] = max(1, min(timeout, 30))
    return resolved


def ssh_base_command(remote: dict[str, Any]) -> list[str]:
    resolved = resolve_remote(remote)
    timeout = str(resolved["connectTimeoutSeconds"])
    common = [
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RequestTTY=no",
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "LogLevel=ERROR",
    ]
    connection = resolved["connection"]
    if connection["type"] == "openssh-profile":
        return [
            "ssh",
            "-F",
            str(Path.home() / ".ssh" / "config"),
            *common,
            "--",
            str(connection["profile"]),
        ]

    known_hosts = trust_dir() / "known_hosts"
    target_host = f"[{resolved['host']}]" if ":" in resolved["host"] else resolved["host"]
    return [
        "ssh",
        "-F",
        "/dev/null",
        "-p",
        str(resolved["port"]),
        "-i",
        resolved["keyPath"],
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        f"HostKeyAlias={resolved['hostKeyAlias']}",
        "-o",
        "CheckHostIP=no",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "VerifyHostKeyDNS=no",
        *common,
        "--",
        f"{resolved['user']}@{target_host}",
    ]


def test_connection(remote: dict[str, Any]) -> dict[str, Any]:
    require_commands(["ssh"])
    resolved = resolve_remote(remote)
    command = [*ssh_base_command(resolved), "ssh-mixer-receiver v1 capabilities"]
    timeout = int(resolved.get("connectTimeoutSeconds", 5)) + 4
    started = time.monotonic()
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    ok = completed.returncode == 0
    error = "" if ok else (completed.stderr.strip() or "Receiver Protocol preflight failed")
    capabilities: dict[str, Any] = {}
    if ok:
        try:
            loaded = json.loads(completed.stdout)
        except json.JSONDecodeError:
            ok = False
            error = "Receiver Protocol returned malformed capabilities; run verified setup or update"
        else:
            if isinstance(loaded, dict):
                capabilities = loaded
            protocol_version = capabilities.get("protocolVersion")
            if protocol_version is None and capabilities.get("protocol") == "v1":
                # First-release v1 helpers predate the numeric capability field
                # but remain wire-compatible and do not need a needless update.
                protocol_version = 1
            if protocol_version != PROTOCOL_VERSION:
                ok = False
                error = (
                    "Receiver Protocol is incompatible; review and apply a signed "
                    "compatible Receiver update"
                )
    result: dict[str, Any] = {
        "ok": ok,
        "target": f"{resolved['user']}@{resolved['host']}",
        "elapsedMs": elapsed_ms,
        "error": error,
        "capabilities": {
            "platform": str(capabilities.get("platform", "")),
            "helperVersion": str(capabilities.get("helperVersion", "")),
            "protocolVersion": capabilities.get("protocolVersion", 1 if capabilities else None),
        },
    }
    if not ok:
        sensitive = [resolved["host"], resolved["user"], resolved["keyPath"]]
        diagnostic = DiagnosticStore(logs_dir()).record(
            stage="connection.preflight",
            code=(
                f"ssh-exit-{completed.returncode}"
                if completed.returncode != 0
                else "protocol-incompatible"
            ),
            message=error,
            sensitive_values=sensitive,
        )
        result["error"] = diagnostic["message"]
        result["diagnostic"] = diagnostic
    return result


def quiet_test(remote: dict[str, Any], dbfs: int) -> dict[str, Any]:
    if isinstance(dbfs, bool) or not isinstance(dbfs, int) or dbfs < -40 or dbfs > 0:
        raise SessionError("Receiver test level must be an integer from -40 to 0 dBFS")
    resolved = resolve_remote(remote)
    connection = resolved["connection"]
    if connection.get("securityLevel") != "receiver-only":
        raise SessionError("quiet testing requires a verified Managed Identity")
    completed = subprocess.run(
        [*ssh_base_command(resolved), f"ssh-mixer-receiver v1 quiet-test --dbfs {dbfs}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=int(resolved["connectTimeoutSeconds"]) + 5,
    )
    if completed.returncode != 0:
        raise SessionError(completed.stderr.strip() or "quiet Receiver test failed")
    return {
        "ok": True,
        "schemaVersion": 1,
        "dbfs": dbfs,
        "durationSeconds": 0.5,
        "changesSystemVolume": False,
    }


def executable_path() -> str:
    # argv[0] is the installed bin/ssh-mixer wrapper; preserving it lets worker
    # imports resolve relative to the same plugin/repository checkout.
    return str(Path(sys.argv[0]).resolve())


class SessionWorker:
    def __init__(self, config: dict[str, Any], session_id: str, foreground: bool = False):
        self.config = config
        self.session_id = session_id
        self.foreground = foreground
        self.resources = empty_resources()
        self.children: list[subprocess.Popen[Any]] = []
        self.stop_requested = False
        self.diagnostics = DiagnosticStore(logs_dir())
        self.verbose_diagnostics = self.diagnostics.consume_verbose_for_session()
        self.current_state: dict[str, Any] = stopped_state()

    def _handle_stop(self, _signum: int, _frame: Any) -> None:
        self.stop_requested = True
        self._write_state("stopping")

    def _write_state(self, state: str, error: str = "", plan: dict[str, Any] | None = None) -> None:
        selected_inputs = (
            plan.get("selectedInputs", [])
            if plan
            else self.current_state.get("selectedInputs", [])
        )
        capture_active = any(
            isinstance(source, dict) and source.get("type") == "capture"
            for source in selected_inputs
        )
        if not selected_inputs and state == "starting":
            # Before discovery finishes, fail closed if the requested runtime
            # selection contains a Capture Source Matcher.
            capture_active = any(
                isinstance(matcher, dict) and matcher.get("kind") == "capture"
                for matcher in self.config.get("sourceMatchers", [])
            )
        self.current_state = {
            "schemaVersion": 1,
            "state": state,
            "active": state in ACTIVE_STATES,
            "pid": os.getpid(),
            "sessionId": self.session_id,
            "startedAt": self.current_state.get("startedAt") or utc_now(),
            "updatedAt": utc_now(),
            "destination": self.config.get("destination", ""),
            "selectedInputs": selected_inputs,
            "captureActive": capture_active,
            "stopReason": self.current_state.get("stopReason", ""),
            "resources": self.resources,
            "remote": public_config(self.config).get("remote", {}),
            "error": error,
            "logPath": "",
            "planWarnings": plan.get("warnings", []) if plan else self.current_state.get("planWarnings", []),
        }
        write_state(self.current_state)

    def _track_module(
        self, module_id: str, role: str, **details: str
    ) -> None:
        tracked = {"id": str(module_id), "role": role}
        tracked.update({key: str(value) for key, value in details.items() if value})
        self.resources["modules"].append(tracked)
        self._write_state(str(self.current_state.get("state", "starting")))

    def _track_move(self, sink_input_id: str, original_sink: str) -> None:
        self.resources["movedSinkInputs"].append(
            {"sinkInputId": str(sink_input_id), "originalSink": str(original_sink)}
        )
        self._write_state(str(self.current_state.get("state", "starting")))

    def _track_process(self, name: str, pid: int) -> None:
        identity = process_identity(pid)
        if identity is None:
            identity = {"pid": pid, "startTimeTicks": "", "executable": ""}
        identity["name"] = name
        self.resources["processes"].append(identity)
        self._write_state(str(self.current_state.get("state", "starting")))

    def _apply_operation(self, operation: dict[str, Any]) -> None:
        op = operation.get("op")
        if op == "load-null-sink":
            sink = str(operation["sink"])
            self.resources["mixSink"] = sink
            if sink_exists(sink):
                self._restore_local_default_sink(sink)
                return
            completed = run_pactl(
                [
                    "load-module",
                    "module-null-sink",
                    f"sink_name={sink}",
                    "sink_properties=device.description=SSH-mixer",
                ]
            )
            module_id = completed.stdout.strip()
            if not module_id:
                raise SessionError("pactl did not return a module id for the mix sink")
            self._track_module(module_id, "mix-sink")
            self._restore_local_default_sink(sink)
            return

        if op == "move-sink-input":
            sink_input_id = str(operation["sinkInputId"])
            original_sink = str(operation.get("fromSink", ""))
            target_sink = str(operation["toSink"])
            run_pactl(["move-sink-input", sink_input_id, target_sink])
            self._track_move(sink_input_id, original_sink)
            return

        if op == "load-loopback":
            source = str(operation["source"])
            sink = str(operation["sink"])
            role = str(operation.get("role", "loopback"))
            if source != f"{sink}.monitor" and not source_exists(source):
                raise SessionError(f"Pulse source is no longer available: {source}")
            completed = run_pactl(
                [
                    "load-module",
                    "module-loopback",
                    f"source={source}",
                    f"sink={sink}",
                    "latency_msec=50",
                ]
            )
            module_id = completed.stdout.strip()
            if not module_id:
                raise SessionError("pactl did not return a module id for a loopback")
            self._track_module(module_id, role, source=source, sink=sink)
            return

        if op == "stream-remote":
            return

        raise SessionError(f"unknown routing operation: {op}")

    def _restore_local_default_sink(self, mix_sink: str) -> None:
        default_sink = str(self.resources.get("defaultSink", ""))
        if default_sink and default_sink != mix_sink:
            run_pactl(["set-default-sink", default_sink])

    def _start_pipeline(self, capture_source: str, remote: dict[str, Any]) -> None:
        require_commands(["ffmpeg", "ssh"])
        resolved = resolve_remote(remote)
        bitrate = str(resolved.get("bitrate", "128k")) or "128k"
        receiver = str(resolved.get("receiverCommand", DEFAULT_RECEIVER_COMMAND))
        ffmpeg_cmd = build_encoder_command(capture_source, bitrate)
        ssh_cmd = [*ssh_base_command(resolved), receiver]
        ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        if ffmpeg.stdout is None or ffmpeg.stderr is None:
            kill_process_group(ffmpeg.pid, signal.SIGTERM)
            raise SessionError("could not capture ffmpeg stream pipes")
        os.set_blocking(ffmpeg.stderr.fileno(), False)
        try:
            ssh = subprocess.Popen(
                ssh_cmd,
                stdin=ffmpeg.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception:
            ffmpeg.stdout.close()
            kill_process_group(ffmpeg.pid, signal.SIGTERM)
            raise
        ffmpeg.stdout.close()
        if ssh.stderr is None:
            kill_process_group(ffmpeg.pid, signal.SIGTERM)
            kill_process_group(ssh.pid, signal.SIGTERM)
            raise SessionError("could not capture SSH transport diagnostics")
        os.set_blocking(ssh.stderr.fileno(), False)
        self.children = [ffmpeg, ssh]
        self._track_process("ffmpeg", ffmpeg.pid)
        self._track_process("ssh", ssh.pid)

    def _forget_pipeline_processes(self) -> None:
        pipeline_pids = {child.pid for child in self.children}
        self.resources["processes"] = [
            process
            for process in self.resources["processes"]
            if process.get("pid") not in pipeline_pids
        ]
        self.children = []

    def _reconcile_playback_sources(self) -> None:
        matchers = self.config.get("sourceMatchers", [])
        if not any(
            isinstance(matcher, dict) and matcher.get("kind") == "playback"
            for matcher in matchers
        ):
            return
        sources = discover_sources()
        routed_ids = {
            str(moved.get("sinkInputId", ""))
            for moved in self.resources.get("movedSinkInputs", [])
            if isinstance(moved, dict)
        }
        local_copy_sinks = {
            str(module.get("sink", ""))
            for module in self.resources.get("modules", [])
            if isinstance(module, dict)
            and module.get("role") == "preserve-local-playback"
            and module.get("sink")
        }
        operations = build_playback_reconciliation(
            sources,
            matchers,
            destination=str(self.config.get("destination", "both")),
            routed_sink_input_ids=routed_ids,
            local_copy_sinks=local_copy_sinks,
        )
        moved_source_ids: set[str] = set()
        for operation in operations:
            self._apply_operation(operation)
            if operation.get("op") == "move-sink-input":
                moved_source_ids.add(str(operation.get("sourceId", "")))
        if moved_source_ids:
            selected = [
                source
                for source in self.current_state.get("selectedInputs", [])
                if isinstance(source, dict)
            ]
            selected_ids = {str(source.get("id", "")) for source in selected}
            selected.extend(
                source
                for source in sources
                if str(source.get("id", "")) in moved_source_ids - selected_ids
            )
            self.current_state["selectedInputs"] = selected
            self._write_state(str(self.current_state.get("state", "streaming")))

    def _run_pipeline_epochs(
        self, capture_source: str, remote: dict[str, Any]
    ) -> tuple[int, str]:
        while not self.stop_requested:
            exit_code, error, refresh_reason = self._wait_for_pipeline()
            if not refresh_reason:
                return exit_code, error
            self.diagnostics.record(
                stage="session.stream",
                code=f"refresh-{refresh_reason}-starting",
                message=(
                    "Remote stream epoch reached its hard deadline; replacement started."
                    if refresh_reason == "deadline"
                    else "Qualifying stream silence detected; replacement started."
                ),
                session_id=self.session_id,
            )
            self._terminate_children()
            self._forget_pipeline_processes()
            if self.stop_requested:
                return 0, ""
            self._start_pipeline(capture_source, remote)
            self.diagnostics.record(
                stage="session.stream",
                code=f"refresh-{refresh_reason}-launched",
                message="Remote stream epoch replacement processes launched.",
                session_id=self.session_id,
            )
        return 0, ""

    def _terminate_children(self) -> None:
        for child in self.children:
            if child.poll() is None:
                kill_process_group(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all(child.poll() is not None for child in self.children):
                return
            time.sleep(0.05)
        for child in self.children:
            if child.poll() is None:
                kill_process_group(child.pid, signal.SIGKILL)

    def _wait_for_pipeline(self) -> tuple[int, str, str]:
        ffmpeg, ssh = self.children
        epoch = StreamEpochPolicy(started_at=time.monotonic())
        next_reconciliation_at = epoch.started_at
        silence = StreamSilenceState()
        ssh_diagnostics = ""
        while not self.stop_requested:
            ffmpeg_code = ffmpeg.poll()
            ssh_code = ssh.poll()
            ssh_diagnostics = (
                ssh_diagnostics + read_encoder_diagnostics(ssh.stderr)
            )[-16_384:]
            if ffmpeg_code is not None or ssh_code is not None:
                if ssh_code not in (None, 0):
                    error = receiver_protocol_error(ssh_diagnostics)
                    return int(ssh_code), error or f"ssh exited with status {ssh_code}", ""
                if ffmpeg_code not in (None, 0):
                    return int(ffmpeg_code), f"ffmpeg exited with status {ffmpeg_code}", ""
                return 0, "", ""
            now = time.monotonic()
            if now >= next_reconciliation_at:
                self._reconcile_playback_sources()
                next_reconciliation_at = now + 0.5
            silence_started = silence.feed(read_encoder_diagnostics(ffmpeg.stderr))
            refresh_reason = epoch.refresh_reason(
                now=now,
                silence_active=silence.active or silence_started,
            )
            if refresh_reason:
                return 0, "", refresh_reason
            time.sleep(0.2)
        return 0, "", ""

    def _cleanup(self) -> None:
        self._terminate_children()
        cleanup_resources({"resources": self.resources})

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        require_commands(["pactl"])
        self.diagnostics.record(
            stage="session.start",
            code="starting",
            message=(
                "Session worker started with one-Session verbose diagnostics."
                if self.verbose_diagnostics
                else "Session worker started."
            ),
            session_id=self.session_id,
        )
        self._write_state("starting")
        plan: dict[str, Any] | None = None
        exit_code = 0
        error = ""
        try:
            sources = discover_sources()
            concrete_ids = resolve_source_ids(sources, self.config.get("sourceIds", []))
            resolution = resolve_session_source_ids(
                sources,
                self.config.get("sourceMatchers", []),
                concrete_ids,
            )
            concrete_ids = resolution["sourceIds"]
            plan = build_route_plan(
                sources,
                concrete_ids,
                str(self.config.get("destination", "both")),
                armed_playback=bool(resolution["armedPlayback"]),
            )
            if plan["destination"] != "local":
                self.resources["defaultSink"] = default_sink_name()
            self.config["sourceIds"] = concrete_ids
            self._write_state("starting", plan=plan)
            if self.verbose_diagnostics:
                self.diagnostics.record(
                    stage="session.plan",
                    code="plan-ready",
                    message=(
                        f"Route Mode: {plan['destination']}; "
                        f"selected source count: {len(plan['selectedInputs'])}; "
                        f"operation count: {len(plan['operations'])}."
                    ),
                    session_id=self.session_id,
                )
            if plan["destination"] == "local":
                self._write_state("local", plan=plan)
                while not self.stop_requested:
                    time.sleep(0.25)
            else:
                for operation in plan["operations"]:
                    if operation.get("op") == "stream-remote":
                        continue
                    self._apply_operation(operation)
                self._start_pipeline(
                    str(plan["captureSource"]), self.config.get("remote", {})
                )
                self._write_state("streaming", plan=plan)
                exit_code, error = self._run_pipeline_epochs(
                    str(plan["captureSource"]), self.config.get("remote", {})
                )
                if error:
                    remote = self.config.get("remote", {})
                    sensitive = [
                        str(remote.get("host", "")),
                        str(remote.get("user", "")),
                        str(remote.get("keyPath", "")),
                    ] if isinstance(remote, dict) else []
                    error = redact(error, sensitive)
                if self.stop_requested:
                    exit_code = 0
                    error = ""
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            RoutingError,
            SessionError,
        ) as exc:
            exit_code = 1
            remote = self.config.get("remote", {})
            sensitive = [
                str(remote.get("host", "")),
                str(remote.get("user", "")),
                str(remote.get("keyPath", "")),
            ] if isinstance(remote, dict) else []
            if plan:
                for selected in plan.get("selectedInputs", []):
                    if not isinstance(selected, dict):
                        continue
                    for field in (
                        "id",
                        "name",
                        "label",
                        "detail",
                        "sinkName",
                        "sinkLabel",
                        "applicationName",
                        "processBinary",
                    ):
                        sensitive.append(str(selected.get(field, "")))
            error = redact(str(exc), sensitive)
            self.diagnostics.record(
                stage="session.run",
                code=type(exc).__name__,
                message=error,
                session_id=self.session_id,
                sensitive_values=sensitive,
            )
        finally:
            with suppress(Exception):
                self._cleanup()
            final_state = "stopped" if exit_code == 0 else "error"
            self._write_state(final_state, error=error, plan=plan)
        return exit_code


def require_migration_complete() -> None:
    from .migration import MigrationService

    if MigrationService(status_reader=lambda: {"active": False}).inspect()["detected"]:
        raise SessionError(
            "legacy configuration requires an explicit migration choice before Start"
        )


def require_no_pending_removal() -> None:
    pending = state_dir() / "pending-removals.json"
    if pending.is_symlink():
        raise SessionError("pending Receiver cleanup state is unsafe; Start is blocked")
    if pending.is_file():
        raise SessionError(
            "Receiver cleanup is pending; retry or explicitly abandon it before Start"
        )


def require_lifecycle_monitor() -> None:
    # Imported lazily because lifecycle policy delegates stopping back to this
    # module. An active heartbeat proves the keep-loaded privacy service and
    # persistent bar integration are running before any Session can start.
    from .lifecycle import privacy_services_ready

    if not privacy_services_ready():
        raise SessionError(
            "privacy lifecycle service is unavailable; refresh the Omarchy shell before Start"
        )


def start_session(config: dict[str, Any]) -> dict[str, Any]:
    with session_lock():
        require_migration_complete()
        require_no_pending_removal()
        require_lifecycle_monitor()
        require_commands(["pactl"])
        current = normalize_status()
        if current.get("active"):
            raise SessionError("an SSH-mixer session is already active")
        cleanup_resources(current)
        cleanup_named_mix(DEFAULT_MIX_SINK)

        if config.get("destination") in {"ssh", "both"}:
            require_commands(["ffmpeg", "ssh"])
            test = test_connection(config.get("remote", {}))
            if not test["ok"]:
                raise SessionError(test["error"] or "Receiver Protocol preflight failed")

        source_ids = config.get("sourceIds", [])
        if isinstance(source_ids, list) and source_ids:
            available_sources = discover_sources()
            concrete_ids = resolve_source_ids(available_sources, source_ids)
            if len(concrete_ids) != len(dict.fromkeys(map(str, source_ids))):
                raise SessionError("one or more selected sources are no longer available")
            config["sourceIds"] = concrete_ids
            config["sourceMatchers"] = matchers_for_source_ids(
                available_sources, concrete_ids
            )
        save_config(config)
        session_id = uuid.uuid4().hex
        config_read_fd, config_write_fd = os.pipe()
        command = [
            executable_path(),
            "worker",
            "--session-id",
            session_id,
            "--config-fd",
            str(config_read_fd),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                pass_fds=(config_read_fd,),
            )
            os.close(config_read_fd)
            config_read_fd = -1
            with os.fdopen(config_write_fd, "w", encoding="utf-8") as config_pipe:
                config_write_fd = -1
                json.dump(config, config_pipe)
        finally:
            if config_read_fd >= 0:
                os.close(config_read_fd)
            if config_write_fd >= 0:
                os.close(config_write_fd)

        deadline = time.monotonic() + 8
        last = stopped_state()
        while time.monotonic() < deadline:
            time.sleep(0.1)
            last = normalize_status()
            if last.get("sessionId") == session_id and last.get("state") in {"streaming", "local", "error"}:
                break
            if process.poll() is not None:
                last = normalize_status()
                break
        if last.get("sessionId") != session_id:
            return normalize_status()
        if last.get("state") == "error":
            raise SessionError(str(last.get("error") or "session failed to start"))
        return last


def stop_session(reason: str = "user-stop") -> dict[str, Any]:
    with session_lock():
        state = normalize_status()
        resources = state.get("resources") if isinstance(state.get("resources"), dict) else {}
        pid = state.get("pid")
        try:
            pid_int = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_int = None

        if state.get("active") and pid_int:
            kill_process_group(pid_int, signal.SIGTERM)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and pid_alive(pid_int):
                time.sleep(0.1)
            if pid_alive(pid_int):
                kill_process_group(pid_int, signal.SIGKILL)

        terminate_processes(resources.get("processes", []) or [])
        cleanup_resources(state)
        if resources.get("mixSink"):
            cleanup_named_mix(str(resources["mixSink"]))
        final = stopped_state()
        final["stopReason"] = str(reason)[:80]
        final["remote"] = public_config(load_config()).get("remote", {})
        write_state(final)
        return normalize_status(final)


def run_foreground(config: dict[str, Any]) -> int:
    require_migration_complete()
    require_no_pending_removal()
    require_lifecycle_monitor()
    if config.get("destination") in {"ssh", "both"}:
        test = test_connection(config.get("remote", {}))
        if not test["ok"]:
            raise SessionError(test["error"] or "Receiver Protocol preflight failed")
    session_id = uuid.uuid4().hex
    return SessionWorker(config, session_id, foreground=True).run()

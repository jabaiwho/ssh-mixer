from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import ensure_dirs, load_config, secure_write_text, xdg_runtime_dir
from .session import normalize_status, stop_session

StatusReader = Callable[[], dict[str, Any]]
ConfigLoader = Callable[[], dict[str, Any]]
Stopper = Callable[..., dict[str, Any]]

STOP_EVENTS = {
    "suspend",
    "logout",
    "receiver-disconnect",
    "fatal-network-loss",
    "privacy-monitor-failure",
}
NO_START_EVENTS = {
    "unlock",
    "wake",
    "network-reconnected",
    "login",
    "discovery",
    "panel-open",
}
VALID_EVENTS = STOP_EVENTS | NO_START_EVENTS | {"lock"}
HEARTBEAT_MAX_AGE_SECONDS = 10


class LifecycleError(ValueError):
    """Raised when an unsupported lifecycle event is requested."""


def _has_capture(status: dict[str, Any]) -> bool:
    if status.get("captureActive") is True:
        return True
    selected = status.get("selectedInputs", [])
    return isinstance(selected, list) and any(
        isinstance(source, dict) and source.get("type") == "capture"
        for source in selected
    )


def handle_lifecycle_event(
    event: str,
    *,
    status_reader: StatusReader = normalize_status,
    config_loader: ConfigLoader = load_config,
    stopper: Stopper = stop_session,
) -> dict[str, Any]:
    """Apply fail-closed Session policy without ever starting or resuming one."""

    normalized_event = str(event).strip().lower()
    if normalized_event not in VALID_EVENTS:
        raise LifecycleError("unsupported Session lifecycle event")
    status = status_reader()
    if normalized_event in NO_START_EVENTS:
        return {
            "ok": True,
            "schemaVersion": 1,
            "event": normalized_event,
            "action": "none",
            "automaticStart": False,
        }
    if not status.get("active"):
        return {
            "ok": True,
            "schemaVersion": 1,
            "event": normalized_event,
            "action": "none",
            "automaticStart": False,
        }

    if normalized_event == "lock":
        config = config_loader()
        privacy = config.get("privacy", {})
        lock_behavior = (
            str(privacy.get("lockBehavior", "stop-all"))
            if isinstance(privacy, dict)
            else "stop-all"
        )
        if _has_capture(status):
            stopped = stopper(reason="capture-screen-lock")
            return {
                "ok": True,
                "schemaVersion": 1,
                "event": normalized_event,
                "action": "stopped",
                "reason": "capture-stops-on-lock",
                "automaticStart": False,
                "status": stopped,
            }
        if lock_behavior == "continue-playback":
            return {
                "ok": True,
                "schemaVersion": 1,
                "event": normalized_event,
                "action": "continued-playback",
                "reason": "approved-non-capture-lock-policy",
                "automaticStart": False,
                "status": status,
            }
        stop_reason = "screen-lock"
    else:
        stop_reason = normalized_event

    stopped = stopper(reason=stop_reason)
    return {
        "ok": True,
        "schemaVersion": 1,
        "event": normalized_event,
        "action": "stopped",
        "reason": stop_reason,
        "automaticStart": False,
        "status": stopped,
    }


def indicator_status(
    status: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = dict(status or normalize_status())
    configured = config or load_config()
    active = current.get("active") is True
    capture = active and _has_capture(current)
    privacy = configured.get("privacy", {})
    show_label = (
        isinstance(privacy, dict) and privacy.get("showReceiverLabel") is True
    )
    remote = configured.get("remote", {})
    label = (
        str(remote.get("host", "")).strip()
        if active and show_label and isinstance(remote, dict)
        else ""
    )
    if any(unicodedata.category(character).startswith("C") for character in label):
        label = ""
    return {
        "ok": True,
        "schemaVersion": 1,
        "active": active,
        "capture": capture,
        "kind": "recording" if capture else "playback",
        "receiverLabel": label[:253],
        "state": str(current.get("state", "stopped")),
        "opensSessionControls": True,
        "disableAllowedWhileActive": False,
    }


def lifecycle_heartbeat_path() -> Path:
    return xdg_runtime_dir() / "lifecycle-heartbeat"


def indicator_heartbeat_path() -> Path:
    return xdg_runtime_dir() / "indicator-heartbeat"


def write_lifecycle_heartbeat() -> None:
    secure_write_text(lifecycle_heartbeat_path(), f"{time.time():.6f}\n")


def write_indicator_heartbeat() -> None:
    secure_write_text(indicator_heartbeat_path(), f"{time.time():.6f}\n")


def _heartbeat_ready(path: Path, *, now: float | None = None) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        updated = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    current = time.time() if now is None else now
    return 0 <= current - updated <= HEARTBEAT_MAX_AGE_SECONDS


def lifecycle_monitor_ready(*, now: float | None = None) -> bool:
    return _heartbeat_ready(lifecycle_heartbeat_path(), now=now)


def indicator_ready(*, now: float | None = None) -> bool:
    return _heartbeat_ready(indicator_heartbeat_path(), now=now)


def privacy_services_ready(*, now: float | None = None) -> bool:
    return lifecycle_monitor_ready(now=now) and indicator_ready(now=now)


def _screen_locked() -> bool | None:
    try:
        completed = subprocess.run(
            ["omarchy-shell", "lock", "isLocked"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip().lower()
    if value not in {"true", "false"}:
        return None
    return value == "true"


def _session_closing() -> bool:
    session_id = os.environ.get("XDG_SESSION_ID", "").strip()
    if not session_id:
        return False
    try:
        completed = subprocess.run(
            ["loginctl", "show-session", session_id, "-p", "State", "--value"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "closing"


def _dbus_monitor() -> subprocess.Popen[str] | None:
    try:
        return subprocess.Popen(
            [
                "dbus-monitor",
                "--system",
                "type='signal',interface='org.freedesktop.login1.Manager'",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError:
        return None


def run_lifecycle_monitor() -> int:
    """Run inside the keep-loaded Omarchy service; never starts a Session."""

    running = True

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    ensure_dirs()
    monitor = _dbus_monitor()
    if monitor is None:
        lifecycle_heartbeat_path().unlink(missing_ok=True)
        return 1
    selector = selectors.DefaultSelector()
    if monitor is not None and monitor.stdout is not None:
        selector.register(monitor.stdout, selectors.EVENT_READ)
    pending_member = ""
    last_locked: bool | None = None
    lock_observation_failures = 0
    logout_handled = False
    try:
        while running:
            write_lifecycle_heartbeat()
            for key, _mask in selector.select(timeout=1):
                line = key.fileobj.readline()
                if not line:
                    selector.unregister(key.fileobj)
                    continue
                stripped = line.strip()
                if "member=PrepareForSleep" in stripped:
                    pending_member = "sleep"
                elif "member=PrepareForShutdown" in stripped:
                    pending_member = "shutdown"
                elif pending_member and stripped.startswith("boolean "):
                    entering = stripped.removeprefix("boolean ").strip() == "true"
                    if pending_member == "sleep":
                        handle_lifecycle_event("suspend" if entering else "wake")
                        if entering:
                            # Exit to release systemd-inhibit's delay lock once
                            # ownership-safe cleanup has completed. The QML
                            # service starts a fresh monitor after wake.
                            running = False
                    elif pending_member == "shutdown" and entering:
                        handle_lifecycle_event("logout")
                        running = False
                    pending_member = ""

            locked = _screen_locked()
            if locked is None:
                lock_observation_failures += 1
                if lock_observation_failures >= 3:
                    handle_lifecycle_event("privacy-monitor-failure")
                    running = False
            else:
                lock_observation_failures = 0
            if locked is True:
                # Re-apply while locked so a Session racing with the lock
                # boundary cannot begin Capture after the first transition.
                handle_lifecycle_event("lock")
                last_locked = True
            elif locked is False and last_locked is not False:
                handle_lifecycle_event("unlock")
                last_locked = False
            if not logout_handled and _session_closing():
                handle_lifecycle_event("logout")
                logout_handled = True
                running = False

            if normalize_status().get("active") and not indicator_ready():
                handle_lifecycle_event("privacy-monitor-failure")
                running = False
            if running:
                write_lifecycle_heartbeat()
    finally:
        try:
            lifecycle_heartbeat_path().unlink(missing_ok=True)
        except OSError:
            pass
        selector.close()
        if monitor is not None and monitor.poll() is None:
            monitor.terminate()
            try:
                monitor.wait(timeout=2)
            except subprocess.TimeoutExpired:
                monitor.kill()
    return 0

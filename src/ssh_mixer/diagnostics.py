from __future__ import annotations

import json
import os
import platform
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .config import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, StorageError, secure_open_append, secure_write_text

RETENTION_POLICIES = {
    "minimal": {"maximumAgeDays": 1, "maximumSessions": 5},
    "standard": {"maximumAgeDays": 7, "maximumSessions": 20},
    "extended": {"maximumAgeDays": 30, "maximumSessions": 50},
}
DEFAULT_RETENTION_POLICY = "standard"
MAX_LOG_BYTES = 64 * 1024
MAX_TOTAL_LOG_BYTES = 512 * 1024
MAX_MESSAGE_CHARS = 4096
GITHUB_ISSUES_URL = "https://github.com/jabaiwho/ssh-mixer/issues/new"
GITHUB_CONTRIBUTING_URL = "https://github.com/jabaiwho/ssh-mixer/blob/main/CONTRIBUTING.md"

_GENERIC_REDACTIONS = (
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[0-9a-fA-F]{0,4}:[0-9a-fA-F:]{2,}\b"),
    re.compile(r"\b[A-Za-z0-9._-]+@[A-Za-z0-9._-]+\b"),
    re.compile(r"(?:~|/home/[^/\s]+)/(?:\.ssh|\.config)/[^\s]+"),
    re.compile(r"(?<![A-Za-z0-9])/(?:[^/\s]+/)+[^\s]+"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\[^\s]+", re.IGNORECASE),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def redact(value: str, sensitive_values: Iterable[str] = ()) -> str:
    redacted = str(value)
    values = sorted(
        {str(item) for item in sensitive_values if str(item)},
        key=len,
        reverse=True,
    )
    for sensitive in values:
        redacted = re.sub(re.escape(sensitive), "[redacted]", redacted, flags=re.IGNORECASE)
    for pattern in _GENERIC_REDACTIONS:
        redacted = pattern.sub("[redacted]", redacted)
    home = str(Path.home())
    if home and home != "/":
        redacted = redacted.replace(home, "[home]")
    return redacted[:MAX_MESSAGE_CHARS]


class DiagnosticStore:
    """Protected, bounded store for redacted operational events."""

    def __init__(
        self,
        directory: Path,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.directory = Path(directory)
        self._now = now
        self._prepare_directory()

    @property
    def settings_path(self) -> Path:
        return self.directory / ".settings.json"

    def _prepare_directory(self) -> None:
        if self.directory.is_symlink():
            raise StorageError(f"diagnostic directory may not be a symlink: {self.directory}")
        self.directory.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        self.directory.chmod(PRIVATE_DIR_MODE)
        # Legacy raw process logs cannot be safely redacted after the fact.
        for path in self.directory.glob("*.log"):
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)

    def record(
        self,
        *,
        stage: str,
        code: str,
        message: str,
        session_id: str = "operation",
        sensitive_values: Iterable[str] = (),
    ) -> dict[str, str]:
        now = self._now()
        self.prune()
        safe_session = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:64] or "operation"
        existing = sorted(self.directory.glob(f"{safe_session}.*.jsonl"))
        path = existing[-1] if existing else (
            self.directory / f"{safe_session}.{int(now.timestamp())}.jsonl"
        )
        event = {
            "schemaVersion": 1,
            "createdAt": now.isoformat(),
            "stage": redact(stage),
            "code": redact(code),
            "message": redact(message, sensitive_values),
        }
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        current_size = path.stat().st_size if path.exists() and not path.is_symlink() else 0
        if current_size + len(encoded.encode("utf-8")) <= MAX_LOG_BYTES:
            with secure_open_append(path) as log:
                log.write(encoded)
        self.prune()
        return event

    def _log_timestamp(self, path: Path) -> float:
        try:
            return float(path.name.rsplit(".", 2)[-2])
        except (IndexError, ValueError):
            return 0.0

    def _logs_oldest_first(self) -> list[Path]:
        logs = []
        for path in self.directory.glob("*.jsonl"):
            if path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_file():
                path.chmod(PRIVATE_FILE_MODE)
                logs.append(path)
        return sorted(logs, key=lambda item: (self._log_timestamp(item), item.name))

    def prune(self) -> None:
        retention = self.retention_settings()
        cutoff = self._now() - timedelta(days=retention["maximumAgeDays"])
        logs = self._logs_oldest_first()
        for path in list(logs):
            timestamp = datetime.fromtimestamp(self._log_timestamp(path), tz=timezone.utc)
            if timestamp < cutoff:
                path.unlink(missing_ok=True)
                logs.remove(path)
        while len(logs) > retention["maximumSessions"]:
            logs.pop(0).unlink(missing_ok=True)
        total = sum(path.stat().st_size for path in logs)
        while logs and total > MAX_TOTAL_LOG_BYTES:
            oldest = logs.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)

    def _events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in self._logs_oldest_first():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        events.append(value)
            except (OSError, json.JSONDecodeError):
                continue
        return events

    def preview_report(self, *, include_logs: bool = False) -> dict[str, Any]:
        self.prune()
        events = self._events()
        latest = events[-1] if events else {
            "stage": "unknown",
            "code": "no-diagnostic",
            "message": "No diagnostic event is available.",
        }
        body = "\n".join(
            (
                "## SSH-mixer diagnostic",
                "",
                f"- Platform: {platform.system() or 'Unknown'}",
                f"- Stage: {latest.get('stage', 'unknown')}",
                f"- Code: {latest.get('code', 'unknown')}",
                "",
                "### Error",
                "",
                str(latest.get("message", "No message available.")),
                "",
                "### Privacy",
                "",
                "This report was generated and redacted locally. No logs are included by default.",
            )
        )
        if include_logs:
            body += (
                "\n\n### Redacted operational events\n\n```json\n"
                + json.dumps(events, indent=2, sort_keys=True)
                + "\n```"
            )
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "diagnostic": latest,
            "body": body,
            "logsIncluded": include_logs,
        }
        if include_logs:
            result["events"] = events
        return result

    def _read_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {}
        if self.settings_path.is_symlink():
            raise StorageError("diagnostic settings may not be a symlink")
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_settings(self, settings: dict[str, Any]) -> None:
        secure_write_text(
            self.settings_path,
            json.dumps(settings, sort_keys=True, separators=(",", ":")) + "\n",
        )

    def retention_settings(self) -> dict[str, Any]:
        configured = str(
            self._read_settings().get("retentionPolicy", DEFAULT_RETENTION_POLICY)
        )
        policy = (
            configured
            if configured in RETENTION_POLICIES
            else DEFAULT_RETENTION_POLICY
        )
        return {"policy": policy, **RETENTION_POLICIES[policy]}

    def configure_retention(self, policy: str) -> dict[str, Any]:
        normalized = str(policy).strip()
        if normalized not in RETENTION_POLICIES:
            raise ValueError("diagnostic retention policy is invalid")
        settings = self._read_settings()
        settings["retentionPolicy"] = normalized
        self._write_settings(settings)
        self.prune()
        return self.retention_settings()

    def enable_verbose_next_session(self) -> None:
        settings = self._read_settings()
        settings["verboseSessionsRemaining"] = 1
        self._write_settings(settings)

    def consume_verbose_for_session(self) -> bool:
        settings = self._read_settings()
        remaining = int(settings.get("verboseSessionsRemaining", 0) or 0)
        enabled = remaining > 0
        settings["verboseSessionsRemaining"] = max(0, remaining - 1)
        self._write_settings(settings)
        return enabled

    def clear(self) -> None:
        for path in self.directory.iterdir():
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)


def github_issue_url(body: str, *, title: str = "SSH-mixer diagnostic") -> str:
    return GITHUB_ISSUES_URL + "?" + urlencode({"title": title, "body": body})

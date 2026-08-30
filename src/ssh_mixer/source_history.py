from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audio import (
    is_internal_playback_loopback,
    matcher_for_source,
    normalize_source_matcher,
)
from .config import ensure_dirs, secure_write_text, state_dir

HISTORY_SCHEMA_VERSION = 1
MAX_RECENT_PLAYBACK_SOURCES = 20


def source_history_path() -> Path:
    return state_dir() / "source-history.json"


def _normalize_playback_matchers(values: Any) -> list[dict[str, Any]]:
    matchers: list[dict[str, Any]] = []
    if not isinstance(values, list):
        return matchers
    for value in values[:MAX_RECENT_PLAYBACK_SOURCES]:
        try:
            matcher = normalize_source_matcher(value)
        except ValueError:
            continue
        if (
            matcher.get("kind") == "playback"
            and not is_internal_playback_loopback(matcher)
            and matcher not in matchers
        ):
            matchers.append(matcher)
    return matchers


def _current_playback_matchers(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matchers: list[dict[str, Any]] = []
    for source in sources:
        if source.get("type") != "playback" or is_internal_playback_loopback(source):
            continue
        try:
            matcher = matcher_for_source(source)
        except ValueError:
            continue
        if matcher not in matchers:
            matchers.append(matcher)
    return matchers


class SourceHistoryStore:
    """Bounded local catalog of recently observed Playback Sources.

    History is presentation-only: these matchers never select or route audio.
    Clearing history suppresses currently active applications until they
    disappear, so an immediate panel refresh does not recreate cleared rows.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or source_history_path()

    def _read(self) -> tuple[dict[str, list[dict[str, Any]]], bool]:
        ensure_dirs()
        empty = {"recent": [], "ignoredUntilAbsent": []}
        if self.path.is_symlink():
            raise ValueError("Source history may not be a symlink")
        if not self.path.exists():
            return empty, False
        self.path.chmod(0o600)
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return empty, False
        if not isinstance(value, dict):
            return empty, False
        state = {
            "recent": _normalize_playback_matchers(value.get("recent")),
            "ignoredUntilAbsent": _normalize_playback_matchers(
                value.get("ignoredUntilAbsent")
            ),
        }
        needs_rewrite = (
            value.get("recent") != state["recent"]
            or value.get("ignoredUntilAbsent") != state["ignoredUntilAbsent"]
        )
        return state, needs_rewrite

    def _write(self, state: dict[str, list[dict[str, Any]]]) -> None:
        ensure_dirs()
        payload = {
            "schemaVersion": HISTORY_SCHEMA_VERSION,
            "recent": state["recent"][:MAX_RECENT_PLAYBACK_SOURCES],
            "ignoredUntilAbsent": state["ignoredUntilAbsent"][
                :MAX_RECENT_PLAYBACK_SOURCES
            ],
        }
        secure_write_text(
            self.path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    def load(self) -> list[dict[str, Any]]:
        state, needs_rewrite = self._read()
        if needs_rewrite:
            self._write(state)
        return state["recent"]

    def observe(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        state, needs_rewrite = self._read()
        current = _current_playback_matchers(sources)
        ignored = [
            matcher
            for matcher in state["ignoredUntilAbsent"]
            if matcher in current
        ]
        observed = [matcher for matcher in current if matcher not in ignored]
        recent = observed + [
            matcher for matcher in state["recent"] if matcher not in observed
        ]
        recent = recent[:MAX_RECENT_PLAYBACK_SOURCES]
        updated = {"recent": recent, "ignoredUntilAbsent": ignored}
        if updated != state or needs_rewrite:
            self._write(updated)
        return recent

    def clear(self, sources: list[dict[str, Any]]) -> None:
        self._write(
            {
                "recent": [],
                "ignoredUntilAbsent": _current_playback_matchers(sources),
            }
        )

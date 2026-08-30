from __future__ import annotations

import hashlib
import json
from typing import Any

from .audio import (
    enforce_desktop_all_exclusivity,
    friendly_app_label,
    friendly_device_label,
    is_desktop_all_source,
    is_internal_playback_loopback,
    matcher_for_source,
    normalize_source_matcher,
)

_TYPE_ORDER = {"playback": 0, "monitor": 1, "capture": 2}
_CATEGORY_LABELS = {
    "playback": "Playback Source",
    "monitor": "Output Monitor",
    "capture": "Capture Source",
}


def _choice_id(matcher: dict[str, Any]) -> str:
    canonical = json.dumps(
        matcher,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"source-choice:{digest}"


def _label(
    matcher: dict[str, Any], members: list[dict[str, Any]]
) -> str:
    kind = matcher["kind"]
    if kind == "playback":
        return friendly_app_label(
            str(
                matcher.get("applicationName")
                or matcher.get("processBinary")
                or matcher.get("name")
                or "Application audio"
            )
        )
    if members and members[0].get("isDefaultMonitor"):
        return "Desktop (All)"
    if members:
        return str(members[0].get("label", "")).strip() or friendly_device_label(
            str(matcher.get("name", ""))
        )
    return friendly_device_label(str(matcher.get("name", ""))) or "Unavailable source"


def _detail(
    matcher: dict[str, Any], members: list[dict[str, Any]]
) -> str:
    kind = matcher["kind"]
    if kind == "playback":
        if not members:
            return "Ready when audio starts"
        if len(members) == 1:
            return "Playing now"
        return f"{len(members)} active streams"
    if members:
        return str(members[0].get("detail", ""))
    return "Not currently available"


def _normalized_unique(matchers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for value in matchers:
        matcher = normalize_source_matcher(value)
        if is_internal_playback_loopback(matcher):
            continue
        if matcher not in normalized:
            normalized.append(matcher)
    return normalized


def _choice_groups(
    sources: list[dict[str, Any]], catalog_matchers: list[dict[str, Any]]
) -> tuple[
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    catalog = _normalized_unique(catalog_matchers)
    matchers = list(catalog)
    members_by_id: dict[str, list[dict[str, Any]]] = {
        _choice_id(matcher): [] for matcher in catalog
    }
    for source in sources:
        if is_internal_playback_loopback(source):
            continue
        try:
            matcher = matcher_for_source(source)
        except ValueError:
            # A concrete diagnostic Source without stable metadata cannot become
            # a persistent logical choice.
            continue
        if matcher not in matchers:
            matchers.append(matcher)
        members_by_id.setdefault(_choice_id(matcher), []).append(source)
    return matchers, members_by_id


def build_source_choices(
    sources: list[dict[str, Any]],
    selected_matchers: list[dict[str, Any]],
    pinned_matchers: list[dict[str, Any]] | None = None,
    recent_matchers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Present logical Sources without exposing temporary stream identity.

    A Playback Source is grouped by its stable Source Matcher, so one choice can
    represent no current stream or several current streams. Capture policy is
    preserved: remembered Capture Sources are recent choices, never selected
    automatically.
    """

    selected = _normalized_unique(selected_matchers)
    pinned = _normalized_unique(pinned_matchers or [])
    recent = _normalized_unique(recent_matchers or [])
    matchers, members_by_id = _choice_groups(
        sources, selected + pinned + recent
    )
    exclusive_selected = next(
        (
            matcher
            for matcher in selected
            if any(
                is_desktop_all_source(member)
                for member in members_by_id.get(_choice_id(matcher), [])
            )
        ),
        None,
    )
    if exclusive_selected is not None:
        selected = [exclusive_selected]
    choices: list[dict[str, Any]] = []
    for matcher in matchers:
        choice_id = _choice_id(matcher)
        members = members_by_id[choice_id]
        kind = str(matcher["kind"])
        remembered = matcher in selected
        choices.append(
            {
                "id": choice_id,
                "type": kind,
                "categoryLabel": _CATEGORY_LABELS[kind],
                "label": _label(matcher, members),
                "detail": _detail(matcher, members),
                "active": bool(members),
                "activeStreamCount": len(members),
                "selected": remembered and kind != "capture",
                "pinned": matcher in pinned,
                "recent": matcher in recent,
                "recentChoice": remembered and kind == "capture",
                "sensitiveCapture": kind == "capture",
                "exclusiveSelection": any(
                    is_desktop_all_source(member) for member in members
                ),
            }
        )
    choices.sort(
        key=lambda choice: (
            _TYPE_ORDER.get(str(choice["type"]), 99),
            not bool(choice["selected"]),
            not bool(choice["pinned"]),
            str(choice["label"]).casefold(),
            str(choice["id"]),
        )
    )
    return choices


def resolve_choice_ids(
    sources: list[dict[str, Any]],
    selected_matchers: list[dict[str, Any]],
    choice_ids: list[str],
) -> dict[str, Any]:
    """Resolve logical choices into persistent matchers and current runtime ids."""

    matchers, members_by_id = _choice_groups(sources, selected_matchers)
    by_id = {_choice_id(matcher): matcher for matcher in matchers}
    resolved_matchers: list[dict[str, Any]] = []
    source_ids: list[str] = []
    for raw_choice_id in choice_ids:
        choice_id = str(raw_choice_id)
        if choice_id not in by_id:
            raise ValueError("one or more selected Source choices are no longer available")
        matcher = by_id[choice_id]
        if matcher not in resolved_matchers:
            resolved_matchers.append(matcher)
        for source in members_by_id[choice_id]:
            source_id = str(source.get("id", ""))
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)
    source_ids = enforce_desktop_all_exclusivity(sources, source_ids)
    desktop_all = next(
        (
            source
            for source in sources
            if str(source.get("id", "")) in source_ids
            and is_desktop_all_source(source)
        ),
        None,
    )
    if desktop_all is not None:
        resolved_matchers = [matcher_for_source(desktop_all)]
    return {"sourceMatchers": resolved_matchers, "sourceIds": source_ids}

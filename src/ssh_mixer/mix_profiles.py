from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Any

from .audio import normalize_source_matcher
from .connections import normalize_connection

PROFILE_SCHEMA_VERSION = 1
MAX_PROFILES = 50
MAX_MATCHERS = 32
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{7,63}$")
VALID_ROUTE_MODES = {"local", "ssh", "both"}
VALID_LOCK_BEHAVIORS = {"stop-all", "continue-playback"}
DEFAULT_PRIVACY = {
    "lockBehavior": "stop-all",
    "showReceiverLabel": False,
}
DEFAULT_STREAM = {
    "bitrate": "128k",
    "connectTimeoutSeconds": 5,
}


class MixProfileError(ValueError):
    """Raised when a Mix Profile cannot be represented safely."""


def _safe_text(value: Any, *, field: str, maximum: int) -> str:
    text = str(value).strip()
    if not text or len(text) > maximum:
        raise MixProfileError(f"Mix Profile {field} is invalid")
    if any(unicodedata.category(character).startswith("C") for character in text):
        raise MixProfileError(f"Mix Profile {field} contains control characters")
    return text


def normalize_privacy(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    lock_behavior = str(
        raw.get("lockBehavior", DEFAULT_PRIVACY["lockBehavior"])
    ).strip().lower()
    if lock_behavior not in VALID_LOCK_BEHAVIORS:
        lock_behavior = "stop-all"
    return {
        "lockBehavior": lock_behavior,
        "showReceiverLabel": raw.get(
            "showReceiverLabel", DEFAULT_PRIVACY["showReceiverLabel"]
        )
        is True,
    }


def normalize_stream(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    bitrate = str(raw.get("bitrate", DEFAULT_STREAM["bitrate"])).strip().lower()
    if not re.fullmatch(r"(?:6[4-9]|[7-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-6])k", bitrate):
        bitrate = "128k"
    try:
        timeout = int(
            raw.get(
                "connectTimeoutSeconds", DEFAULT_STREAM["connectTimeoutSeconds"]
            )
        )
    except (TypeError, ValueError):
        timeout = int(DEFAULT_STREAM["connectTimeoutSeconds"])
    return {
        "bitrate": bitrate,
        "connectTimeoutSeconds": max(1, min(timeout, 30)),
    }


def normalize_mix_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MixProfileError("Mix Profile must be an object")
    profile_id = str(value.get("id", "")).strip().lower()
    if not profile_id:
        profile_id = f"profile-{secrets.token_hex(8)}"
    if not PROFILE_ID_RE.fullmatch(profile_id):
        raise MixProfileError("Mix Profile id is invalid")
    name = _safe_text(value.get("name", ""), field="name", maximum=80)
    route_mode = str(value.get("routeMode", "both")).strip().lower()
    if route_mode not in VALID_ROUTE_MODES:
        raise MixProfileError("Mix Profile Route Mode is invalid")
    raw_matchers = value.get("sourceMatchers", [])
    if not isinstance(raw_matchers, list) or len(raw_matchers) > MAX_MATCHERS:
        raise MixProfileError("Mix Profile Source Matchers are invalid")
    matchers: list[dict[str, Any]] = []
    for raw_matcher in raw_matchers:
        matcher = normalize_source_matcher(raw_matcher)
        if matcher not in matchers:
            matchers.append(matcher)
    has_capture = any(matcher["kind"] == "capture" for matcher in matchers)
    return {
        "schemaVersion": PROFILE_SCHEMA_VERSION,
        "id": profile_id,
        "name": name,
        "connection": normalize_connection(value.get("connection")),
        "routeMode": route_mode,
        "sourceMatchers": matchers,
        "privacy": normalize_privacy(value.get("privacy")),
        "stream": normalize_stream(value.get("stream")),
        "quickStartEnabled": value.get("quickStartEnabled") is True
        and bool(matchers)
        and not has_capture,
        "requiresCaptureConfirmation": has_capture,
    }


def normalize_mix_profiles(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_profile in value[:MAX_PROFILES]:
        if not isinstance(raw_profile, dict) or not raw_profile.get("id"):
            continue
        try:
            profile = normalize_mix_profile(raw_profile)
        except (MixProfileError, ValueError):
            continue
        if profile["id"] in seen:
            continue
        seen.add(profile["id"])
        profiles.append(profile)
    return profiles


def upsert_mix_profile(
    profiles: list[dict[str, Any]], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    normalized = normalize_mix_profiles(profiles)
    replacement = normalize_mix_profile(profile)
    updated = [item for item in normalized if item["id"] != replacement["id"]]
    if len(updated) >= MAX_PROFILES:
        raise MixProfileError("Mix Profile limit reached")
    updated.append(replacement)
    return updated


def find_mix_profile(
    profiles: list[dict[str, Any]], profile_id: str
) -> dict[str, Any]:
    for profile in normalize_mix_profiles(profiles):
        if profile["id"] == profile_id:
            return profile
    raise MixProfileError("Mix Profile was not found")

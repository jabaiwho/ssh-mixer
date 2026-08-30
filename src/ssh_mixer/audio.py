from __future__ import annotations

import re
import subprocess
import unicodedata
from collections.abc import Callable
from typing import Any

CommandRunner = Callable[[list[str]], str]
MATCHER_SCHEMA_VERSION = 1
MATCHER_FIELDS = {
    "playback": ("processBinary", "applicationName", "name"),
    "capture": ("name",),
    "monitor": ("name",),
}


def run_command(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def _unquote_pactl(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value.replace('\\"', '"').replace('\\\\', '\\')


def parse_pactl_objects(text: str, header: str) -> list[dict[str, Any]]:
    """Parse `pactl list` sections into field/property dictionaries."""

    header_re = re.compile(rf"^{re.escape(header)} #(?P<id>\d+)")
    objects: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_properties = False

    for line in text.splitlines():
        header_match = header_re.match(line)
        if header_match:
            if current is not None:
                objects.append(current)
            current = {
                "id": header_match.group("id"),
                "fields": {},
                "properties": {},
            }
            in_properties = False
            continue

        if current is None:
            continue

        stripped = line.strip()
        if stripped == "Properties:":
            in_properties = True
            continue
        if not stripped:
            continue

        if in_properties and " = " in stripped:
            key, value = stripped.split(" = ", 1)
            current["properties"][key.strip()] = _unquote_pactl(value)
            continue

        # pactl field lines are indented and contain a colon. Store the first
        # field value; nested blocks such as Volume/Balance are irrelevant to
        # SSH-mixer's routing decisions.
        if ":" in stripped and not stripped.startswith("#"):
            key, value = stripped.split(":", 1)
            current["fields"][key.strip()] = value.strip()
            in_properties = False

    if current is not None:
        objects.append(current)
    return objects


def _field(obj: dict[str, Any], name: str, default: str = "") -> str:
    return str(obj.get("fields", {}).get(name, default))


def _prop(obj: dict[str, Any], name: str, default: str = "") -> str:
    return str(obj.get("properties", {}).get(name, default))


def _title_from_token(value: str) -> str:
    value = value.strip().strip('"')
    if not value:
        return ""
    known = {"cliamp": "cliamp", "ffmpeg": "FFmpeg", "spotify": "Spotify"}
    lower = value.lower()
    if lower in known:
        return known[lower]
    return value[:1].upper() + value[1:]


def friendly_app_label(raw: str) -> str:
    label = raw.strip()
    pipewire_alsa = re.match(r"^PipeWire ALSA \[(.+)]$", label, flags=re.IGNORECASE)
    if pipewire_alsa:
        return _title_from_token(pipewire_alsa.group(1))
    if label.lower() == "audio-src":
        return "Application audio"
    return _title_from_token(label)


def friendly_device_label(raw: str) -> str:
    label = raw.strip()
    if not label:
        return ""
    replacements = [
        (r"^sof-soundwire\s+", ""),
        (r"^built-?in audio\s+", ""),
        (r"\s+Output$", ""),
        (r"\s+Input$", ""),
    ]
    for pattern, repl in replacements:
        label = re.sub(pattern, repl, label, flags=re.IGNORECASE)
    label = label.replace("Microphones", "Microphone")
    return label


def is_cliamp_stream(obj: dict[str, Any]) -> bool:
    props = obj.get("properties", {})
    needles = [
        props.get("application.process.binary", ""),
        props.get("application.name", ""),
        props.get("node.name", ""),
        props.get("media.name", ""),
        props.get("node.description", ""),
    ]
    return any("cliamp" in str(value).lower() for value in needles)


def _sink_maps(sinks: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for sink in sinks:
        sink_id = str(sink.get("id", ""))
        name = _field(sink, "Name")
        if sink_id:
            by_id[sink_id] = sink
        if name:
            by_name[name] = sink
    return by_id, by_name


def _sink_label(sink: dict[str, Any] | None, fallback: str = "") -> str:
    if not sink:
        return fallback
    return friendly_device_label(
        _prop(sink, "node.nick")
        or _prop(sink, "device.profile.description")
        or _field(sink, "Description")
        or _field(sink, "Name")
        or fallback
    )


def source_id_for_name(name: str) -> str:
    return f"source:{name}"


def sink_input_id(pulse_id: str) -> str:
    return f"sink-input:{pulse_id}"


def build_playback_sources(
    sink_inputs: list[dict[str, Any]], sinks_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in sink_inputs:
        pulse_id = str(item.get("id", ""))
        if not pulse_id:
            continue
        sink_id = _field(item, "Sink")
        sink = sinks_by_id.get(sink_id)
        sink_name = _field(sink, "Name") if sink else ""
        app_name = (
            _prop(item, "application.name")
            or _prop(item, "media.name")
            or _prop(item, "node.name")
            or f"Sink input {pulse_id}"
        )
        label = friendly_app_label(app_name)
        sink_label = _sink_label(sink, f"sink {sink_id}" if sink_id else "unknown output")
        cliamp = is_cliamp_stream(item)
        sources.append(
            {
                "id": sink_input_id(pulse_id),
                "type": "playback",
                "categoryLabel": "Playback Source",
                "sensitiveCapture": False,
                "pulseId": pulse_id,
                "name": _prop(item, "node.name"),
                "label": label,
                "detail": f"Application playback → {sink_label}",
                "sinkId": sink_id,
                "sinkName": sink_name,
                "sinkLabel": sink_label,
                "mediaName": _prop(item, "media.name"),
                "applicationName": _prop(item, "application.name"),
                "processBinary": _prop(item, "application.process.binary"),
                "defaultSelected": False,
                "isCliamp": cliamp,
            }
        )
    return sources


def _source_type(obj: dict[str, Any]) -> str:
    name = _field(obj, "Name")
    monitor_of = _field(obj, "Monitor of Sink")
    media_class = _prop(obj, "media.class") or _prop(obj, "node.class")
    if name.endswith(".monitor") or monitor_of and monitor_of.lower() != "n/a":
        return "monitor"
    if "monitor" in media_class.lower():
        return "monitor"
    return "capture"


def build_capture_sources(
    pactl_sources: list[dict[str, Any]],
    sinks_by_name: dict[str, dict[str, Any]],
    default_sink_name: str,
    default_source_name: str,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in pactl_sources:
        name = _field(item, "Name")
        if not name:
            continue
        if name.startswith("ssh_mixer_mix") or name.startswith("ssh-mixer"):
            continue
        source_type = _source_type(item)
        state = _field(item, "State")
        description = friendly_device_label(
            _prop(item, "node.nick")
            or _prop(item, "device.profile.description")
            or _field(item, "Description")
            or name
        )
        if source_type == "monitor":
            sink_name = name.removesuffix(".monitor")
            sink_label = _sink_label(sinks_by_name.get(sink_name), description)
            is_default_monitor = bool(default_sink_name and sink_name == default_sink_name)
            label = "Default output monitor" if is_default_monitor else f"{sink_label} monitor"
            detail = "System/output monitor"
        else:
            label = description or name
            detail = "Hardware/application capture source"
        if state:
            detail = f"{detail} • {state.lower()}"
        sources.append(
            {
                "id": source_id_for_name(name),
                "type": source_type,
                "categoryLabel": (
                    "Capture Source" if source_type == "capture" else "Output Monitor"
                ),
                "sensitiveCapture": source_type == "capture",
                "pulseId": str(item.get("id", "")),
                "name": name,
                "label": label,
                "detail": detail,
                "state": state,
                "defaultSelected": False,
                "isDefaultSource": bool(default_source_name and name == default_source_name),
                "isDefaultMonitor": bool(source_type == "monitor" and default_sink_name and name == f"{default_sink_name}.monitor"),
            }
        )
    return sources


def _safe_runner(runner: CommandRunner, command: list[str]) -> str:
    try:
        return runner(command)
    except (OSError, subprocess.CalledProcessError):
        return ""


def discover_sources(runner: CommandRunner = run_command) -> list[dict[str, Any]]:
    sinks = parse_pactl_objects(_safe_runner(runner, ["pactl", "list", "sinks"]), "Sink")
    pactl_sources = parse_pactl_objects(_safe_runner(runner, ["pactl", "list", "sources"]), "Source")
    sink_inputs = parse_pactl_objects(
        _safe_runner(runner, ["pactl", "list", "sink-inputs"]), "Sink Input"
    )
    default_sink_name = _safe_runner(runner, ["pactl", "get-default-sink"]).strip()
    default_source_name = _safe_runner(runner, ["pactl", "get-default-source"]).strip()

    sinks_by_id, sinks_by_name = _sink_maps(sinks)
    sources = build_playback_sources(sink_inputs, sinks_by_id)
    sources.extend(
        build_capture_sources(pactl_sources, sinks_by_name, default_sink_name, default_source_name)
    )

    type_order = {"playback": 0, "capture": 1, "monitor": 2}
    sources.sort(
        key=lambda src: (
            not bool(src.get("defaultSelected")),
            type_order.get(str(src.get("type")), 99),
            str(src.get("label", "")).lower(),
            str(src.get("id", "")),
        )
    )
    return sources


def normalize_source_matcher(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Source Matcher must be an object")
    kind = str(value.get("kind", "")).strip().lower()
    if kind not in MATCHER_FIELDS:
        raise ValueError("Source Matcher kind must be playback, capture, or monitor")
    matcher: dict[str, Any] = {
        "schemaVersion": MATCHER_SCHEMA_VERSION,
        "kind": kind,
    }
    for field in MATCHER_FIELDS[kind]:
        field_value = str(value.get(field, "")).strip()
        if any(
            unicodedata.category(character).startswith("C")
            for character in field_value
        ):
            raise ValueError("Source Matcher metadata contains control characters")
        if len(field_value) > 300:
            raise ValueError("Source Matcher metadata is too long")
        if field_value:
            matcher[field] = field_value
    if not any(field in matcher for field in MATCHER_FIELDS[kind]):
        raise ValueError("Source Matcher has no stable metadata")
    return matcher


def matcher_for_source(source: dict[str, Any]) -> dict[str, Any]:
    """Create a persistent matcher without PulseAudio/PipeWire numeric ids."""

    kind = str(source.get("type", "")).strip().lower()
    candidate: dict[str, Any] = {"kind": kind}
    if kind == "playback":
        for field in ("processBinary", "applicationName"):
            candidate[field] = source.get(field, "")
        if not any(
            str(candidate.get(field, "")).strip()
            for field in ("processBinary", "applicationName")
        ):
            candidate["name"] = source.get("name", "")
    else:
        for field in MATCHER_FIELDS.get(kind, ()):
            candidate[field] = source.get(field, "")
    return normalize_source_matcher(candidate)


def match_source(
    source: dict[str, Any], matcher: dict[str, Any]
) -> bool:
    if str(source.get("type", "")) != matcher["kind"]:
        return False
    return all(
        str(source.get(field, "")).strip() == value
        for field, value in matcher.items()
        if field not in {"schemaVersion", "kind"}
    )


def resolve_source_matchers(
    sources: list[dict[str, Any]], matchers: list[dict[str, Any]]
) -> dict[str, Any]:
    """Resolve only unique stable matches; Capture remains an unselected recent choice."""

    selected_ids: list[str] = []
    recent_capture_ids: list[str] = []
    missing: list[int] = []
    ambiguous: list[dict[str, int]] = []
    normalized: list[dict[str, Any]] = []
    has_capture = False
    for index, raw_matcher in enumerate(matchers):
        matcher = normalize_source_matcher(raw_matcher)
        normalized.append(matcher)
        candidates = [source for source in sources if match_source(source, matcher)]
        if not candidates:
            missing.append(index)
            continue
        if matcher["kind"] == "playback":
            for candidate in candidates:
                source_id = str(candidate.get("id", ""))
                if source_id and source_id not in selected_ids:
                    selected_ids.append(source_id)
            continue
        if len(candidates) != 1:
            ambiguous.append({"matcherIndex": index, "candidateCount": len(candidates)})
            continue
        source_id = str(candidates[0].get("id", ""))
        if matcher["kind"] == "capture":
            has_capture = True
            if source_id and source_id not in recent_capture_ids:
                recent_capture_ids.append(source_id)
            continue
        if source_id and source_id not in selected_ids:
            selected_ids.append(source_id)
    # Capture policy applies even while a remembered device is missing.
    has_capture = has_capture or any(matcher["kind"] == "capture" for matcher in normalized)
    return {
        "schemaVersion": MATCHER_SCHEMA_VERSION,
        "selectedIds": selected_ids,
        "recentCaptureIds": recent_capture_ids,
        "missingMatchers": missing,
        "ambiguousMatchers": ambiguous,
        "hasCaptureMatchers": has_capture,
    }


def matchers_for_source_ids(
    sources: list[dict[str, Any]], source_ids: list[str]
) -> list[dict[str, Any]]:
    matchers: list[dict[str, Any]] = []
    for source_id in source_ids:
        source = find_source(sources, str(source_id))
        if source is None:
            raise ValueError("one or more selected sources are no longer available")
        matcher = matcher_for_source(source)
        if matcher not in matchers:
            matchers.append(matcher)
    return matchers


def find_source(sources: list[dict[str, Any]], source_id: str) -> dict[str, Any] | None:
    for source in sources:
        if source.get("id") == source_id:
            return source
    return None


def resolve_source_ids(sources: list[dict[str, Any]], requested_ids: list[str]) -> list[str]:
    """Resolve compatibility aliases into concrete source ids."""

    resolved: list[str] = []
    for requested in requested_ids:
        key = str(requested).strip()
        if not key:
            continue
        match = find_source(sources, key)
        if match:
            resolved.append(str(match["id"]))
            continue
        if key == "cliamp":
            for source in sources:
                if source.get("isCliamp") or source.get("defaultSelected"):
                    resolved.append(str(source["id"]))
                    break
            continue
        if key in {"system", "default", "default-monitor"}:
            for source in sources:
                if source.get("isDefaultMonitor"):
                    resolved.append(str(source["id"]))
                    break
            continue
        if not key.startswith("source:") and any(
            source.get("type") in {"capture", "monitor"} and source.get("name") == key
            for source in sources
        ):
            resolved.append(source_id_for_name(key))
    # Preserve order while removing duplicates.
    unique: list[str] = []
    seen: set[str] = set()
    for source_id in resolved:
        if source_id not in seen:
            seen.add(source_id)
            unique.append(source_id)
    return unique

from __future__ import annotations

from typing import Any

from .audio import (
    enforce_desktop_all_exclusivity,
    is_desktop_all_source,
    match_source,
    matcher_for_source,
    normalize_source_matcher,
)

VALID_DESTINATIONS = {"local", "ssh", "both"}
DEFAULT_MIX_SINK = "ssh_mixer_mix"


class RoutingError(ValueError):
    """Raised when a requested session cannot be represented safely."""


def _source_by_id(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(source.get("id")): source for source in sources if source.get("id")}


def resolve_session_source_ids(
    sources: list[dict[str, Any]],
    source_matchers: list[dict[str, Any]],
    explicit_source_ids: list[str],
) -> dict[str, Any]:
    """Resolve armed Sources for Session start without requiring playback now."""

    matchers = [normalize_source_matcher(value) for value in source_matchers]
    by_id = _source_by_id(sources)
    selected_ids: list[str] = []
    for source_id in explicit_source_ids:
        source = by_id.get(str(source_id))
        if source is None:
            raise RoutingError(f"source not available: {source_id}")
        if not any(match_source(source, matcher) for matcher in matchers):
            raise RoutingError("a selected source changed identity before Session start")
        if str(source_id) not in selected_ids:
            selected_ids.append(str(source_id))

    armed_playback = False
    for matcher in matchers:
        candidates = [source for source in sources if match_source(source, matcher)]
        if matcher["kind"] == "playback":
            armed_playback = True
            for source in candidates:
                source_id = str(source.get("id", ""))
                if source_id and source_id not in selected_ids:
                    selected_ids.append(source_id)
            continue
        if matcher["kind"] == "capture":
            # Capture Matchers are recent choices only. They require a concrete,
            # explicit selection for every Session.
            continue
        if not candidates:
            raise RoutingError("saved Output Monitor is not currently available")
        if len(candidates) != 1:
            raise RoutingError("saved Output Monitor is ambiguous; review the mixer")
        source_id = str(candidates[0].get("id", ""))
        if source_id and source_id not in selected_ids:
            selected_ids.append(source_id)

    selected_ids = enforce_desktop_all_exclusivity(sources, selected_ids)
    selected_sources = [by_id[source_id] for source_id in selected_ids]
    desktop_all = next(
        (source for source in selected_sources if is_desktop_all_source(source)),
        None,
    )
    if desktop_all is not None:
        matchers = [matcher_for_source(desktop_all)]
        armed_playback = False

    return {
        "sourceIds": selected_ids,
        "sourceMatchers": matchers,
        "armedPlayback": armed_playback,
    }


def build_playback_reconciliation(
    sources: list[dict[str, Any]],
    source_matchers: list[dict[str, Any]],
    *,
    destination: str,
    routed_sink_input_ids: set[str],
    local_copy_sinks: set[str],
    mix_sink: str = DEFAULT_MIX_SINK,
) -> list[dict[str, Any]]:
    """Plan idempotent routing for newly appearing armed Playback Sources."""

    destination = str(destination).lower()
    if destination not in VALID_DESTINATIONS:
        raise RoutingError(f"invalid destination: {destination}")
    if destination == "local":
        return []

    playback_matchers = [
        matcher
        for matcher in (
            normalize_source_matcher(value) for value in source_matchers
        )
        if matcher["kind"] == "playback"
    ]
    matching: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source in sources:
        if source.get("type") != "playback":
            continue
        sink_input_id = str(source.get("pulseId", ""))
        if (
            not sink_input_id
            or sink_input_id in routed_sink_input_ids
            or sink_input_id in seen_ids
            or not any(match_source(source, matcher) for matcher in playback_matchers)
        ):
            continue
        seen_ids.add(sink_input_id)
        matching.append(source)

    operations: list[dict[str, Any]] = []
    if destination == "both":
        planned_sinks: set[str] = set()
        for source in matching:
            original_sink = str(source.get("sinkName", ""))
            if (
                not original_sink
                or original_sink == mix_sink
                or original_sink in local_copy_sinks
                or original_sink in planned_sinks
            ):
                continue
            planned_sinks.add(original_sink)
            operations.append(
                {
                    "op": "load-loopback",
                    "role": "preserve-local-playback",
                    "source": f"{mix_sink}.monitor",
                    "sink": original_sink,
                    "label": f"Local copy for {source.get('sinkLabel') or original_sink}",
                }
            )

    for source in matching:
        original_sink = str(source.get("sinkName", ""))
        if original_sink == mix_sink:
            continue
        operations.append(
            {
                "op": "move-sink-input",
                "sourceId": source["id"],
                "sinkInputId": str(source["pulseId"]),
                "fromSink": original_sink,
                "toSink": mix_sink,
                "label": source.get("label", source["id"]),
            }
        )
    return operations


def build_route_plan(
    sources: list[dict[str, Any]],
    source_ids: list[str],
    destination: str,
    mix_sink: str = DEFAULT_MIX_SINK,
    *,
    armed_playback: bool = False,
) -> dict[str, Any]:
    """Build the routing plan exercised by both tests and the live session.

    The external seam is intentionally small: selected source ids plus one
    destination mode. The implementation hides Pulse/PipeWire details such as
    app stream moves, capture loopbacks, and local-preservation loopbacks.
    """

    destination = str(destination).lower()
    if destination not in VALID_DESTINATIONS:
        raise RoutingError(f"invalid destination: {destination}")

    source_ids = enforce_desktop_all_exclusivity(sources, source_ids)
    by_id = _source_by_id(sources)
    selected: list[dict[str, Any]] = []
    missing: list[str] = []
    for source_id in source_ids:
        if source_id in by_id:
            selected.append(by_id[source_id])
        else:
            missing.append(source_id)
    if missing:
        raise RoutingError("source not available: " + ", ".join(missing))

    operations: list[dict[str, Any]] = []
    warnings: list[str] = []
    playback_sources = [source for source in selected if source.get("type") == "playback"]
    capture_sources = [source for source in selected if source.get("type") in {"capture", "monitor"}]

    if destination == "local":
        if capture_sources:
            warnings.append(
                "Local mode leaves capture sources available normally; it does not monitor microphones through local speakers."
            )
        return {
            "destination": destination,
            "mixSink": None,
            "captureSource": None,
            "streamRemote": False,
            "preserveLocalPlayback": True,
            "selectedInputs": selected,
            "operations": operations,
            "warnings": warnings,
        }

    if not selected and not armed_playback:
        raise RoutingError("select at least one input for SSH or Both mode")

    operations.append({"op": "load-null-sink", "sink": mix_sink})

    for source in playback_sources:
        pulse_id = str(source.get("pulseId", ""))
        if not pulse_id:
            raise RoutingError(f"playback source has no Pulse sink-input id: {source.get('label', source.get('id'))}")
        original_sink = str(source.get("sinkName", ""))
        operations.append(
            {
                "op": "move-sink-input",
                "sourceId": source["id"],
                "sinkInputId": pulse_id,
                "fromSink": original_sink,
                "toSink": mix_sink,
                "label": source.get("label", source["id"]),
            }
        )

    for source in capture_sources:
        source_name = str(source.get("name", ""))
        if not source_name:
            raise RoutingError(f"capture source has no Pulse source name: {source.get('label', source.get('id'))}")
        operations.append(
            {
                "op": "load-loopback",
                "role": "input-to-mix",
                "sourceId": source["id"],
                "source": source_name,
                "sink": mix_sink,
                "label": source.get("label", source["id"]),
            }
        )

    if destination == "both":
        seen_sinks: set[str] = set()
        for source in playback_sources:
            original_sink = str(source.get("sinkName", ""))
            if not original_sink or original_sink == mix_sink or original_sink in seen_sinks:
                continue
            seen_sinks.add(original_sink)
            operations.append(
                {
                    "op": "load-loopback",
                    "role": "preserve-local-playback",
                    "source": f"{mix_sink}.monitor",
                    "sink": original_sink,
                    "label": f"Local copy for {source.get('sinkLabel') or original_sink}",
                }
            )
    elif playback_sources:
        warnings.append("SSH mode moves selected playback streams away from local speakers until stopped.")

    monitor_sources = [source for source in capture_sources if source.get("type") == "monitor"]
    if monitor_sources and destination == "ssh":
        warnings.append("Output monitor sources are passive taps; SSH mode cannot suppress their existing local playback.")
    if any(source.get("type") == "capture" for source in capture_sources):
        warnings.append("Capture sources are sent to the remote mix without local speaker monitoring.")

    operations.append({"op": "stream-remote", "source": f"{mix_sink}.monitor"})

    return {
        "destination": destination,
        "mixSink": mix_sink,
        "captureSource": f"{mix_sink}.monitor",
        "streamRemote": True,
        "preserveLocalPlayback": destination == "both",
        "selectedInputs": selected,
        "operations": operations,
        "warnings": warnings,
    }

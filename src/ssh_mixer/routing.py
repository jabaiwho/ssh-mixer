from __future__ import annotations

from typing import Any

VALID_DESTINATIONS = {"local", "ssh", "both"}
DEFAULT_MIX_SINK = "ssh_mixer_mix"


class RoutingError(ValueError):
    """Raised when a requested session cannot be represented safely."""


def _source_by_id(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(source.get("id")): source for source in sources if source.get("id")}


def build_route_plan(
    sources: list[dict[str, Any]],
    source_ids: list[str],
    destination: str,
    mix_sink: str = DEFAULT_MIX_SINK,
) -> dict[str, Any]:
    """Build the routing plan exercised by both tests and the live session.

    The external seam is intentionally small: selected source ids plus one
    destination mode. The implementation hides Pulse/PipeWire details such as
    app stream moves, capture loopbacks, and local-preservation loopbacks.
    """

    destination = str(destination).lower()
    if destination not in VALID_DESTINATIONS:
        raise RoutingError(f"invalid destination: {destination}")

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

    if not selected:
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

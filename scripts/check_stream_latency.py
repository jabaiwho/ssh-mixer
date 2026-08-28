#!/usr/bin/env python3
"""Verify the real FFmpeg Opus/Ogg muxer preserves the reviewed page cadence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ssh_mixer.streaming import (  # noqa: E402
    OGG_PAGE_DURATION_MICROSECONDS,
    OPUS_FRAME_DURATION_MS,
)

SAMPLE_RATE = 48_000


def ogg_granule_positions(data: bytes) -> list[int]:
    positions: list[int] = []
    offset = 0
    while offset < len(data):
        if data[offset : offset + 4] != b"OggS" or offset + 27 > len(data):
            raise ValueError("FFmpeg output is not a complete Ogg page sequence")
        segment_count = data[offset + 26]
        table_end = offset + 27 + segment_count
        if table_end > len(data):
            raise ValueError("Ogg segment table is truncated")
        body_size = sum(data[offset + 27 : table_end])
        page_end = table_end + body_size
        if page_end > len(data):
            raise ValueError("Ogg page body is truncated")
        positions.append(
            int.from_bytes(data[offset + 6 : offset + 14], "little", signed=True)
        )
        offset = page_end
    return positions


def main() -> int:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
        "-t",
        "1.2",
        "-c:a",
        "libopus",
        "-b:a",
        "128k",
        "-application",
        "audio",
        "-frame_duration",
        str(OPUS_FRAME_DURATION_MS),
        "-f",
        "ogg",
        "-page_duration",
        str(OGG_PAGE_DURATION_MICROSECONDS),
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("FFmpeg latency check exceeded ten seconds\n")
        return 1
    if completed.returncode != 0:
        sys.stderr.write("FFmpeg could not produce the latency-check stream\n")
        return 1
    positions = ogg_granule_positions(completed.stdout)
    deltas = [
        current - previous
        for previous, current in zip(positions, positions[1:])
        if current > previous >= 0
    ]
    expected = SAMPLE_RATE * OPUS_FRAME_DURATION_MS // 1_000
    if len(deltas) < 100 or max(deltas, default=expected + 1) > expected:
        sys.stderr.write("Opus packets were batched beyond one reviewed frame per Ogg page\n")
        return 1
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "frameDurationMs": OPUS_FRAME_DURATION_MS,
                "pageDurationMicroseconds": OGG_PAGE_DURATION_MICROSECONDS,
                "maximumPageDurationSamples": max(deltas),
                "audioPageCount": len(deltas),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

OPUS_FRAME_DURATION_MS = 10
OGG_PAGE_DURATION_MICROSECONDS = OPUS_FRAME_DURATION_MS * 1_000
STREAM_REFRESH_SILENCE_AFTER_SECONDS = 15 * 60
STREAM_REFRESH_DEADLINE_SECONDS = 30 * 60
STREAM_SILENCE_THRESHOLD_DB = -50
STREAM_SILENCE_DURATION_SECONDS = 1


@dataclass
class StreamSilenceState:
    """Track only silence transitions from otherwise discarded FFmpeg output."""

    active: bool = False
    _buffer: str = field(default="", init=False, repr=False)

    def feed(self, output: str) -> bool:
        lines = (self._buffer + output).splitlines(keepends=True)
        self._buffer = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._buffer = lines.pop()[-4_096:]
        silence_started = False
        for line in lines:
            if "silencedetect" not in line:
                continue
            if "silence_start:" in line:
                self.active = True
                silence_started = True
            elif "silence_end:" in line:
                self.active = False
        return silence_started


@dataclass(frozen=True)
class StreamEpochPolicy:
    """Decide when one bounded remote playback epoch must be replaced."""

    started_at: float

    def refresh_reason(
        self, *, now: float, silence_active: bool
    ) -> Literal["silence", "deadline"] | None:
        elapsed = now - self.started_at
        if elapsed >= STREAM_REFRESH_DEADLINE_SECONDS:
            return "deadline"
        if silence_active and elapsed >= STREAM_REFRESH_SILENCE_AFTER_SECONDS:
            return "silence"
        return None


def build_encoder_command(capture_source: str, bitrate: str) -> list[str]:
    """Build the fixed low-latency Opus/Ogg encoder command for one Session."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-nostats",
        "-nostdin",
        "-f",
        "pulse",
        "-thread_queue_size",
        "512",
        "-i",
        capture_source,
        "-ac",
        "2",
        "-ar",
        "48000",
        "-af",
        (
            f"silencedetect=noise={STREAM_SILENCE_THRESHOLD_DB}dB:"
            f"d={STREAM_SILENCE_DURATION_SECONDS}"
        ),
        "-c:a",
        "libopus",
        "-b:a",
        bitrate,
        "-application",
        "audio",
        "-frame_duration",
        str(OPUS_FRAME_DURATION_MS),
        "-vn",
        "-sn",
        "-f",
        "ogg",
        "-page_duration",
        str(OGG_PAGE_DURATION_MICROSECONDS),
        "pipe:1",
    ]

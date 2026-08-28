from __future__ import annotations

OPUS_FRAME_DURATION_MS = 10
OGG_PAGE_DURATION_MICROSECONDS = OPUS_FRAME_DURATION_MS * 1_000


def build_encoder_command(capture_source: str, bitrate: str) -> list[str]:
    """Build the fixed low-latency Opus/Ogg encoder command for one Session."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
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

# Arm logical Playback Sources

PipeWire application streams exist only while an application owns an audio stream, and their numeric identifiers are temporary. SSH-mixer therefore models a Playback Source as an explicitly selected stable Source Matcher that may resolve to zero, one, or several current streams; a Session preserves the normal local default output and attaches only newly appearing streams that match an armed Playback Source. This keeps inactive choices usable without persisting temporary IDs or silently recording every application that has produced audio.

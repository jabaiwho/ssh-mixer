# Arm logical Playback Sources

PipeWire application streams exist only while an application owns an audio stream, and their numeric identifiers are temporary. SSH-mixer therefore models a Playback Source as a stable Source Matcher that may resolve to zero, one, or several current streams.

Three independent states prevent convenience from becoming routing consent:

- **Selected** arms a Source for the next explicitly started Session.
- **Pinned** keeps a Source visible whether active, selected, or inactive.
- **Recently used** is a protected local presentation catalog of at most 20 observed Playback Source Matchers. It never includes microphones, never selects audio, and can be cleared.

A Session preserves the normal local default output and attaches only newly appearing streams that match a selected Playback Source. Unselected applications therefore remain local. Temporary PipeWire/PulseAudio IDs are used only for the current runtime operation and are never persisted.

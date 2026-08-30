# SSH-mixer

SSH-mixer lets an Omarchy user intentionally route selected local audio to a trusted receiver through an SSH connection while keeping connection, capture, and lifecycle decisions visible.

## Language

**Receiver**:
A remote Windows, Linux, or macOS computer that plays audio sent by SSH-mixer.
_Avoid_: Destination, target, host

**Connection**:
A saved, verified way to reach one receiver, including its connection type, host trust, and identity policy. One receiver may have more than one connection.
_Avoid_: Provider, remote config

**Receiver Name**:
An editable local nickname used to identify a receiver in the mixer without exposing its full connection address. It does not participate in connection or trust identity; the persistent bar uses only fixed Session-status labels.
_Avoid_: Hostname, source name

**Tailscale Connection**:
A connection whose receiver identity and resolved address are verified against the user's current tailnet.
_Avoid_: Tailscale SSH

**OpenSSH Profile Connection**:
A connection that deliberately uses an existing user-owned OpenSSH profile and its compatible proxy or provider behavior.
_Avoid_: Imported host

**Direct SSH Connection**:
A connection configured explicitly in SSH-mixer without inheriting the user's OpenSSH configuration.
_Avoid_: Custom provider

**Trust Record**:
The approved SSH host-key identity for a receiver.
_Avoid_: Known host entry, fingerprint cache

**Managed Identity**:
A dedicated SSH identity created by SSH-mixer and restricted on the receiver to the receiver protocol.
_Avoid_: Plugin key, streaming key

**User-managed Identity**:
An SSH identity or authentication mechanism controlled by the user or an OpenSSH profile, whose remote permissions SSH-mixer cannot guarantee.
_Avoid_: Existing key

**Receiver Protocol**:
The fixed, versioned set of receiver operations permitted to a Managed Identity.
_Avoid_: Receiver command, remote shell

**Companion Setup**:
The user-authorized setup program run on a receiver when no usable SSH setup exists.
_Avoid_: Installer script, bootstrap script

**Bootstrap Authentication**:
A one-time user authentication used to install and verify a Managed Identity.
_Avoid_: Setup password

**Route Mode**:
Whether selected audio remains local, is sent to a receiver, or does both.
_Avoid_: Destination mode

**Source**:
Local audio that can be selected for routing.
_Avoid_: Receiver, remote PC

**Playback Source**:
A logical local application choice that may have zero, one, or several current audio streams. Once explicitly selected, it remains armed while inactive and attaches matching streams when they appear.
_Avoid_: Channel, sink input, process ID

**Capture Source**:
A direct audio input such as a microphone.
_Avoid_: Input channel

**Output Monitor**:
A passive source representing audio already being played through a local output device. The primary output monitor is presented as **Desktop (All)** and is mutually exclusive with every other Source to prevent duplicate capture and output-monitor feedback.
_Avoid_: Microphone, playback source

**Source Matcher**:
A stable description used to find a previously chosen audio source without persisting temporary PipeWire or PulseAudio identifiers.
_Avoid_: Source ID

**Pinned Source**:
A Source Matcher the user keeps visible independently of whether the Source is selected or active. Pinning never grants routing consent.
_Avoid_: Selected source, favorite route

**Recently Used Source**:
One of at most 20 stable Playback Source Matchers in a protected, clearable local presentation catalog. Recent history never includes Capture Sources and never selects audio.
_Avoid_: Automatic selection, audio history

**Mix Profile**:
A saved receiver, Route Mode, Source Matchers, privacy policy, and stream settings that can be deliberately started together.
_Avoid_: Preset, session config

**Session**:
One explicitly started period of audio routing and optional receiver streaming. Direct Playback Source selection or Quick Start begins it; removing the final Source, End Stream, or a configured lifecycle event ends it.
_Avoid_: Stream config

**Stream Cadence**:
The fixed Opus-frame, Ogg-page, and Receiver clock policy that bounds transport latency during a Session.
_Avoid_: Buffer tweak, restart interval

**Stream Epoch**:
One bounded lifetime of the remote encoder, SSH transport, and Receiver playback inside an active Session.
_Avoid_: Session restart, connection retry

**Quick Start**:
An explicit user action that starts a verified playback-only Mix Profile without reopening the full panel.
_Avoid_: Autostart

**Diagnostic Report**:
A locally generated, redacted description of a failed or unexpected SSH-mixer operation that the user can review before reporting.
_Avoid_: Telemetry, crash upload

**Pending Cleanup**:
A protected retryable record that Receiver key revocation or associated local deletion has not yet been verified complete.
_Avoid_: Removed receiver, cleanup success

**Cleanup Abandonment**:
An informed decision to delete local retry material without claiming or implying that remote Receiver access was revoked.
_Avoid_: Force remove, successful cleanup

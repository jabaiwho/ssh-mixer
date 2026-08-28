# Security model and permissions

SSH-mixer is an Omarchy shell plugin that routes explicitly selected local audio to a Receiver over OpenSSH. This document describes what the plugin can do, what its implementation deliberately permits, and what it cannot protect against.

## Unsandboxed plugin warning

**Omarchy plugins run unsandboxed as the logged-in desktop user.** The QML panel, keep-loaded lifecycle service, bar widget, and Python backend therefore have the same filesystem, process, audio-session, desktop-session, and network permissions as that user. Omarchy does not provide a permission prompt or security boundary around this plugin.

Review the checked-out source and exact commit before enabling it. A compromised plugin checkout, Python interpreter, Omarchy shell, user account, or source operating system can bypass every application-level control described below. SSH-mixer's restrictions reduce its own intended authority; they do not turn an unsandboxed plugin into a sandboxed application.

## Official and custom builds

The official build's fixed operations and verification rules define upstream behavior, not a technical restriction on MIT-licensed downstream source. A downstream owner or owner-authorized agent may change the backend, interface, Receiver, protocol, permissions, or release process and accept different risks. Official signatures and attestations apply only to exact upstream bytes; a modified distribution needs a distinct trust and release identity. See [Custom builds and forks](custom-builds.md).

## Source-side access

### Commands executed

SSH-mixer invokes fixed argument lists or versioned protocol operations. It does not accept a configurable Receiver shell command.

| Purpose | Commands that may run |
| --- | --- |
| Audio discovery and routing | `pactl`, `ffmpeg` |
| Receiver transport and trust | `ssh`, `scp`, `ssh-keyscan`, `ssh -G` |
| Managed Identity | `ssh-keygen`; optional `ssh-add` for an encrypted agent-backed identity |
| Optional Tailscale discovery | `tailscale status --json`; DNS resolution of the selected peer name |
| Privacy lifecycle | `omarchy-shell lock isLocked`, `dbus-monitor`, `loginctl`, `systemd-inhibit` |
| Verified uninstall | `omarchy-plugin-remove jabaiwho.ssh-mixer --yes`, but only after the approved cleanup transaction resolves |
| Signature verification | `ssh-keygen -Y verify` in the `ssh-mixer-release` namespace |

The Omarchy shell may open the user's browser for a locally previewed GitHub issue URL. The compatibility wrapper and CLI invoke the same Python backend.

### Files read

- the installed plugin files and versioned Receiver/Companion artifacts;
- PipeWire/PulseAudio metadata returned by `pactl`;
- the selected OpenSSH profile, its `Include` files, and effective `ssh -F ~/.ssh/config -G` output when the user chooses an OpenSSH Profile Connection;
- the path to a user-managed identity when the user explicitly chooses that weaker policy; private key bytes are handled by OpenSSH, not copied into SSH-mixer configuration;
- SSH-mixer's own protected configuration, Trust Records, Managed Identities, diagnostics, Session state, lifecycle heartbeats, migration backup, and Pending Cleanup state.

Profile inspection rejects `Match exec`. Inspection and runtime both use the explicit user profile, excluding system-wide SSH configuration unless the user's own profile deliberately includes it. ProxyCommand behavior is disclosed and requires confirmation, but an approved OpenSSH profile and its proxy remain user-owned behavior outside SSH-mixer's sandboxing control.

The launching environment may supply `SSH_MIXER_HOST`, `SSH_MIXER_USER`, `SSH_MIXER_KEY`, `SSH_MIXER_BITRATE`, and `SSH_MIXER_CONNECT_TIMEOUT` compatibility overrides. A normalized saved Connection still controls its Receiver host/user/port, but key/stream overrides can affect runtime behavior. Environment control is same-user authority, not a secret or policy boundary; inspect service/shell environment changes when diagnosing unexpected behavior.

### Files written

Unless XDG variables override the parent directories, SSH-mixer owns:

| Location | Contents |
| --- | --- |
| `~/.config/ssh-mixer/` | `0600` configuration and a temporary protected legacy-migration backup |
| `~/.local/share/ssh-mixer/keys/` | one `0700` directory and `0600` private Managed Identity per Receiver |
| `~/.local/share/ssh-mixer/trust/` | approved host-key Trust Records and generated `known_hosts` material |
| `~/.local/state/ssh-mixer/` | bounded redacted diagnostics, Session state, and protected Pending Cleanup state |
| `$XDG_RUNTIME_DIR/ssh-mixer/` | locks, worker handoff, and lifecycle/indicator heartbeats; state-directory fallback when no runtime directory is available |
| a private temporary update directory | bounded downloaded artifacts; verified cleanup is required after an update attempt |

Protected application paths reject symbolic links at security-sensitive seams and use private modes and atomic replacement. Full verified uninstall removes the SSH-mixer configuration, data, state, and runtime roots before asking Omarchy to remove the plugin. A manually created `~/.local/bin/ssh-mixer` symlink is not a secret and may be removed separately after verified uninstall.

### Audio access and changes

The backend can inspect the user's PipeWire/PulseAudio graph and read audio from explicitly selected Playback Sources, Capture Sources, or Output Monitors. A selected Capture Source can contain microphone audio. Audio is passed through process pipes to FFmpeg/OpenSSH and is not written to configuration or diagnostics.

During a Session, SSH-mixer can create a temporary null sink and loopback modules and move selected playback streams. Cleanup verifies ownership before unloading modules, moving streams back, or terminating tracked processes. This does not stop another process running as the same desktop user from accessing audio independently.

### Persistence

- Omarchy keeps `Lifecycle.qml` loaded to observe lock and login-session boundaries.
- An active-only, non-hideable bar widget indicates every Session; Capture has a distinct urgent indication.
- A deliberately started Session uses a detached per-user backend worker and persists until Stop or a configured lifecycle/failure event.
- SSH-mixer does not install a source-side system service, cron job, login hook, kernel module, or privileged daemon.
- Receiver setup is persistent because it installs a helper and a forced `authorized_keys` entry. Platform setup can also enable required receiver services or install approved packages as described below.

## Network access

SSH-mixer can:

- query the local Tailscale client when installed;
- resolve a selected Receiver name;
- retrieve SSH host keys with `ssh-keyscan`;
- establish SSH/SCP connections to the selected Receiver or the hops/proxy explicitly disclosed by an approved OpenSSH profile;
- stream Opus/Ogg audio inside the SSH transport;
- fetch only approved update metadata/artifacts from repository-scoped immutable GitHub release URLs when a production trust root and update transaction are configured; and
- open `github.com/jabaiwho/ssh-mixer/issues/new` in the browser with a report the user already previewed and may edit.

There is no telemetry, analytics endpoint, automatic issue submission, or background update download. Network metadata such as endpoints and traffic timing remains visible to the operating system, network, SSH proxy, VPN, and Receiver. Tailscale is recommended but optional and is not installed or configured by SSH-mixer.

## Receiver-side changes

All Receiver changes are shown in a plan before approval.

### Linux

Companion Setup may install `python3` and/or `ffmpeg` using one detected trusted native manager: `apt-get`, `dnf`, `pacman`, or `zypper`. The exact `sudo` command is shown first. It installs the helper under `~/.local/lib/ssh-mixer/` and adds one key-specific forced entry to `~/.ssh/authorized_keys`. Direct root setup is rejected.

### Windows

Companion Setup may use Microsoft's Windows Capability mechanism for OpenSSH Server, configure/start `sshd`, create the matching firewall rule, install `Gyan.FFmpeg` for the user from the explicit Winget source, write the PowerShell helper under the user's `.ssh` directory, and set/verify required ACLs. Administrator capability and every elevation-requiring change are disclosed. The Receiver Protocol itself rejects an elevated runtime token.

### Experimental macOS

The adapter may ask native `sudo systemsetup` to enable Remote Login, use an already installed architecture-specific Homebrew to run `brew install ffmpeg`, install the helper under `~/.local/lib/ssh-mixer/`, and add the forced key entry. It never installs Homebrew, bypasses Gatekeeper, clears quarantine metadata, or suppresses a platform warning. macOS remains `experimental: true` and `realDeviceVerified: false`.

On all platforms, a Managed Identity forced command permits only Receiver Protocol v1 operations: capabilities, structured diagnostics, play, bounded quiet test, and key-specific removal. Shells, arbitrary commands, forwarding, agents, X11, PTYs, and user startup scripts are rejected. Setup and updates verify these restrictions. Receiver cleanup preserves shared packages and the helper while another SSH-mixer Managed Identity still uses it.

## Trust and identity decisions

- **Host trust:** Unknown and changed host keys stop. The user reviews exact fingerprints before SSH-mixer writes a Trust Record. Tailscale peer verification does not silently replace OpenSSH host-key verification.
- **Managed Identity:** One plugin-owned Ed25519 key is dedicated to a Receiver and accepted only through its forced Receiver Protocol entry. The default private key is unencrypted but mode `0600`; optional encryption uses native `ssh-keygen` prompts and an existing `ssh-agent`.
- **Bootstrap Authentication:** Existing OpenSSH authentication, password prompts, key passphrases, `sudo`, UAC, or macOS approval remain in native tools. SSH-mixer does not accept or store those secrets.
- **User-managed Identity/OpenSSH profile:** SSH-mixer cannot guarantee receiver-only permissions, does not delete the user's key, labels the weaker policy persistently, and never reports that key as revoked.

## Privacy lifecycle

Screen lock defaults to stopping every Session. The optional continue-on-lock policy applies only to non-Capture playback. Capture always stops, repeated lock observation enforces the stop, and unlock/wake/login/reconnection never resumes or starts audio.

Suspend, shutdown, logout, Receiver disconnect, fatal pipeline failure, loss of lock observation, or loss of the required lifecycle/indicator heartbeat stop active routing and invoke ownership-safe cleanup. Start fails closed if privacy services are unavailable. Receiver labels are hidden by default, but the active indicator itself cannot be hidden.

The quiet test starts at `-40 dBFS`, lasts 0.5 seconds, fades, and never changes system volume. A user must confirm audibility and explicitly request each 4 dB increase; `-24 dBFS` is the maximum.

## Diagnostics and reporting

Diagnostics are local structured events, not audio recordings. Values supplied as Receiver names, users, addresses, identity paths, or application/device details are redacted at the reporting seam, and generic path/address patterns are removed. Retention is always limited to 64 KiB per log and 512 KiB total. The user may choose Minimal (one day/five Sessions), Standard (the default: seven/twenty), or Extended (thirty/fifty); every age, count, and byte limit remains active. Verbose mode applies to one Session and then expires.

Redaction is a risk-reduction control, not a mathematical guarantee for arbitrary future text. Preview and edit the complete report before opening GitHub. **Contribute a fix** opens fixed repository guidance but never forks, pushes, or submits automatically. Normal operational failures may use the issue-report flow; suspected vulnerabilities must use the private process in [SECURITY.md](../SECURITY.md).

## Update and release trust

Runtime updates require all of the following: a separately approved `release/allowed_signers` trust root, detached OpenSSH signature verification, the `ssh-mixer-release` namespace, immutable repository-scoped release URLs, exact byte sizes and SHA-256 checksums, a release-id match for every artifact version and full source commit, compatible protocol bounds, an unchanged approved plan, private bounded staging, post-install verification, explicit commit/rollback status, and staging cleanup.

The production transaction uses native Bootstrap Authentication; the Managed Identity cannot replace executable code. Routine updates retain the prior helper and exact authorized-key file until helper/platform/protocol/restriction verification succeeds. They reject dependency, package, SSH service, firewall, or Remote Login changes in favor of a separate disclosed Companion Setup plan. Windows administrator-key ACLs may still require an explicitly displayed native UAC approval.

The source pins the reviewed public production trust root and one exact Receiver release version. Runtime update installation succeeds only when matching signed immutable metadata and artifacts exist; an unpublished, missing, changed, or incompatible pinned release fails closed. GitHub attestations remain a publication gate described in [the release process](releasing.md) and do not replace runtime signature or checksum checks.

Inside an explicitly active SSH/Both Session, each remote Stream Epoch is bounded. SSH-mixer refreshes only the encoder, SSH transport, and Receiver playback at the first detected silence after 15 minutes or at a 30-minute hard limit. This deliberate playback interruption does not reselect a source, restart a stopped Session, extend Capture consent, install code, retry failure, or weaken lifecycle cleanup. The old pipeline terminates before its detached replacement starts; neither side requests a terminal, display, desktop focus, notification, or overlapping playback. Process creation and SSH negotiation have a brief unavoidable CPU/network cost, but no second pipeline remains running. Silence detection is an in-process encoder filter with a small continuous scan cost; it records transitions only and never stores audio. Replacement failure is visible and stops the Session safely.

## Threats addressed

The design aims to reduce:

- network redirection and silent host-key trust;
- arbitrary remote command execution through configuration;
- broad runtime permissions for plugin-generated SSH keys;
- accidental microphone continuation across lock boundaries;
- automatic starts and resumes;
- temporary audio-ID reuse, stale PID reuse, and unrelated resource cleanup;
- unreviewed package, privilege, migration, update, removal, or reporting actions;
- artifact substitution and mutable update URLs;
- secret-bearing diagnostics and abandoned remote access presented as revoked.

## Limitations and non-goals

SSH-mixer cannot protect against:

- compromise of the source account/OS, Omarchy shell, plugin checkout, Python/runtime dependencies, Receiver account/OS, SSH proxy, or approved package repository;
- another same-user process reading audio, files, process metadata, or desktop state;
- a user approving the wrong independently displayed host fingerprint, proxy, package, privilege change, or abandonment warning;
- availability failures, Receiver power/network loss, or traffic analysis;
- broad permissions of a user-managed key or OpenSSH profile;
- perfect rollback of every native package/service change; incomplete rollback is reported instead;
- secure erasure guarantees from SSD/filesystem deletion; verified deletion means the application paths are absent;
- untested hardware compatibility, particularly Experimental macOS; or
- a custom build inheriting official release trust or provenance for bytes that upstream did not sign and attest.

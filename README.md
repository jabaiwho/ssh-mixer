# SSH-mixer

SSH-mixer is a small Omarchy panel plus one backend command for routing selected PipeWire/PulseAudio audio to a Receiver over SSH.

Receiver details have no repository defaults and must be configured for each user's environment. Linux and Windows Receivers are supported. macOS is explicitly **Experimental** with `realDeviceVerified: false`.

> **Security:** Omarchy plugins run unsandboxed with the logged-in desktop user's filesystem, process, audio-session, desktop-session, and network authority. Review the exact source commit and the complete [security model and permission inventory](docs/security-model.md) before enabling SSH-mixer. See [SECURITY.md](SECURITY.md) for private vulnerability reporting; normal failures use locally previewed Diagnostic Reports.

The guided first-run, Connection, host-trust, identity, setup, privacy, diagnostics, update, migration, and removal workflow is documented in the [user guide](docs/user-guide.md).

## Install

SSH-mixer `v0.1.0` is distributed as reviewed source at one signed Git tag. Receiver `v1.1.0` is a separate [signed, attested, immutable production release](https://github.com/jabaiwho/ssh-mixer/releases/tag/receiver-v1.1.0), and its metadata trust root is pinned in `release/allowed_signers`. Omarchy plugins remain unsandboxed source, so review and pin the exact tag rather than enabling a moving branch.

```bash
git clone --branch v0.1.0 --depth 1 \
  https://github.com/jabaiwho/ssh-mixer \
  ~/.config/omarchy/plugins/jabaiwho.ssh-mixer
cd ~/.config/omarchy/plugins/jabaiwho.ssh-mixer
test "$(git rev-parse HEAD)" = "917f812bf2c5b4a63de6b5c59f43b904600858d9"
omarchy plugin validate ~/.config/omarchy/plugins/jabaiwho.ssh-mixer
omarchy plugin enable jabaiwho.ssh-mixer
ln -sf ~/.config/omarchy/plugins/jabaiwho.ssh-mixer/bin/ssh-mixer ~/.local/bin/ssh-mixer
```

The installed menu route is:

```text
Settings → Audio → SSH-mixer
```

Open it directly with:

```bash
omarchy-shell shell summon jabaiwho.ssh-mixer '{}'
```

## Backend usage

```bash
ssh-mixer snapshot
ssh-mixer status
ssh-mixer start --json '{"sourceIds":["cliamp"],"destination":"both"}'
ssh-mixer stop
ssh-mixer test-connection
ssh-mixer mix-profile-save --json '{"name":"Desk","connection":{"type":"direct","host":"receiver.example","user":"listener","port":22},"routeMode":"both","sourceIds":["sink-input:42"],"privacy":{"lockBehavior":"stop-all"},"stream":{"bitrate":"128k"},"quickStartEnabled":true}'
ssh-mixer mix-profile-quick-start --json '{"profileId":"profile-…","quickStartConfirmed":true}'
```

## Receivers and Connections

The panel keeps a bounded collection of verified Connections. Choose the Receiver for the next Session from the Mixer view and give each one an editable local Receiver name such as **Gaming PC**. Renaming changes presentation only; Connection identity, host trust, and Managed Identity remain unchanged. Full addresses appear only in Receiver details.

## Sources, Source Matchers, and Mix Profiles

The mixer labels **Playback Sources**, **Capture Sources**, and **Output Monitors** separately. Concrete `sink-input` and PulseAudio/PipeWire numeric IDs exist only while discovering and starting the current Session; SSH-mixer never writes them to configuration. Saved choices use stable metadata Source Matchers instead.

A selected Playback Source is one logical application choice. It remains visible and armed while inactive, may represent several current streams, and attaches matching streams that appear during the Session. SSH-mixer preserves the normal local default output so unselected applications stay local. Output Monitors still require one unambiguous device match. Capture Source matchers are retained only as recent choices: microphones are never automatically reselected.

A Mix Profile retains its Connection, Route Mode, Source Matchers, lock/privacy policy, bitrate, and connection timeout. Saving a playback-only profile can enable **Quick Start**; an inactive Playback Source starts armed and attaches later. Profiles containing Capture Sources disable Quick Start and open the mixer for fresh source selection and confirmation. Missing or ambiguous device Sources also open the mixer without starting a Session.

## Privacy lifecycle and persistent indication

While any Session is active, SSH-mixer's non-hideable active-state widget remains in the Omarchy bar and opens Session controls. Playback uses an audio-stream symbol; a Session with a selected Capture Source uses a distinct urgent microphone/recording symbol. Receiver names are absent by default and appear only after enabling **Show Receiver label** in Settings. The bar never uses a full Connection address such as a Tailscale DNS name. There is no setting that hides an active indicator.

Screen lock defaults to **Stop all on lock**. The explicit **Continue playback on lock** alternative applies only to Sessions without Capture Sources. Every Capture Session stops and cleans up on lock, and unlock never resumes it. The keep-loaded lifecycle service also stops Sessions before suspend/shutdown and as a login session closes. Receiver disconnect or fatal network loss ends the foreground pipeline and runs the same ownership-safe cleanup. Wake, unlock, login, network reconnection, source discovery, panel open, and profile load never start a Session.

Closing the panel does not stop a deliberately started Session; use the persistent bar indicator to reopen controls and Stop. Start fails closed unless fresh protected heartbeats prove both the bar indicator and keep-loaded lifecycle monitor are running; loss of lock observation also stops an active Session rather than silently dropping the privacy boundary. The lifecycle monitor invokes only fixed backend event operations. It does not install hooks, modify Omarchy lock settings, or start anything.

## Guided legacy migration

Configuration from the personal pre-public plugin is detected from schema, temporary source-id, arbitrary receiver-command, and implicit connection markers. Detection and Diagnostic Reports contain only reason codes and a one-way configuration digest—not receiver, identity, command, application, or device values. A detected legacy configuration cannot start a new Session until one explicit migration is verified; an already active Session remains untouched and migration reports that it is waiting.

The panel offers three exact-plan choices:

- **Import and secure** first requires review of the legacy receiver fingerprint, creates a protected backup, then uses the selected Linux, Windows, or Experimental macOS Companion Setup to establish and verify a dedicated receiver-only Managed Identity before committing the new configuration.
- **Keep user-managed** requires the same host-fingerprint review, then retains the legacy receiver and identity path with a persistent weaker-permissions label while replacing arbitrary commands with Receiver Protocol v1 and discarding unsafe temporary source IDs.
- **Start fresh** removes receiver, identity-path, command, and source choices and returns to the generic public defaults.

Every choice requires its unchanged plan hash. The exact prior file is kept as a protected `0600` backup throughout execution. A failure restores it byte-for-byte, retains the backup, and reports the failed stage and rollback status. A nested Companion Setup rollback cannot be promoted to migration success or masked as complete; incomplete status retains the required local and Receiver cleanup. The backup is removed only after the new schema, Receiver Protocol command, source state, and selected identity policy verify successfully.

## Verified removal and uninstall

**Remove Connection** first shows an immutable plan. After approval, Receiver Protocol v1 removes only that Managed Identity entry, verifies it is absent, and removes the helper and quiet-test state only when no other SSH-mixer Managed Identities remain. Only after that confirmation does SSH-mixer delete its owned local private key, Trust Record, matching Mix Profiles, and diagnostics. Shared/system packages are not silently removed.

An offline, unsupported, or partially cleaned Receiver remains visibly **pending — not revoked**. Its protected retry identity and Connection stay available for retry. The Panel and `removal-inspect`, `removal-plan`, and `removal-apply` CLI operations expose the same state. The platform Companion Setup also supports independent key-specific removal when this source machine is unavailable:

- Linux: `setup-v1.sh remove MANAGED_PUBLIC_KEY_BODY`
- Windows: `setup-v1.ps1 -Mode Remove -KeyBody MANAGED_PUBLIC_KEY_BODY`
- Experimental macOS: `setup-v1.sh remove MANAGED_PUBLIC_KEY_BODY`

The key body is the public base64 field from the exact `ssh-mixer-managed-*` `authorized_keys` entry, not a private key. Companion removal preserves unrelated keys and reports separately whether the shared helper was removed.

Abandonment requires a separate informed confirmation stating that remote access was **not verified revoked**. It deletes local retry material and is always reported as `abandoned-not-revoked`, never successful revocation. User-managed keys and OpenSSH-profile identities are never deleted or claimed as revoked by SSH-mixer.

**Full uninstall** lists every configured Receiver from the active Connection and Mix Profiles, applies the same verified/pending rules, verifies SSH-mixer configuration, key, trust, diagnostic, state, and runtime directories absent, and only then invokes `omarchy-plugin-remove jabaiwho.ssh-mixer --yes`. An active Session is never stopped implicitly; removal waits for an explicit Stop. Pending cleanup blocks plugin removal unless the user separately confirms abandonment.

## Linux Receiver setup

After saving and trusting a Tailscale or Direct SSH Connection, select **Plan Linux Receiver setup**. Planning probes capabilities but makes no changes. The panel then shows the exact package and user-level changes for approval before it:

1. creates one dedicated Ed25519 Managed Identity in protected local storage;
2. optionally uses native `ssh-keygen` and `ssh-agent` handling for an encrypted identity;
3. transfers checksummed Companion Setup and Receiver Protocol artifacts over trusted SSH;
4. uses the detected native package manager for an approved `ffmpeg` installation when needed;
5. installs a forced-command `authorized_keys` entry with shell, forwarding, agent, X11, PTY, and user-RC access disabled; and
6. verifies both the restrictions and rejection of an arbitrary command before saving the identity.

Bootstrap authentication and `sudo` prompts are owned by OpenSSH and the receiver terminal; SSH-mixer does not read or store passwords. Direct root setup is rejected. A failed step is rolled back where safe, and an incomplete rollback is reported explicitly rather than presented as success.

The optional test tone starts at -40 dBFS, lasts 0.5 seconds, fades in and out, and plays once. Each increase requires audible-output confirmation and another user action, advances by 4 dB, and cannot exceed -24 dBFS. It never changes receiver system volume.

## Windows Receiver setup

Choose **Plan Windows Receiver** for a Microsoft OpenSSH receiver. The no-change probe records the OpenSSH version, service, matching firewall rule, account capability/elevation, key location, Winget, and FFplay before proposing anything. The approved PowerShell Companion Setup can:

- install Microsoft's signed OpenSSH Server Windows capability and its matching firewall rule;
- install `Gyan.FFmpeg` for the current user from the explicit Winget source and verify `ffplay.exe`;
- install the checksummed PowerShell Receiver Protocol and a forced Managed Identity;
- apply and verify the documented standard-user or administrator-key ACL; and
- verify non-elevated runtime, protocol capability, arbitrary-command rejection, and forwarding rejection.

Administrator-capable accounts are disclosed and require separate confirmation. Fixed PowerShell setup runs noninteractively over SSH without a PTY; if an approved plan requires privilege but Bootstrap Authentication is not already elevated, setup stops before generating a Managed Identity. The installed Receiver Protocol refuses an elevated runtime token. Windows security prompts and warnings are never bypassed. Setup restores prior files, ACLs, packages, capabilities, and firewall state where safe; any state it cannot restore is reported as an incomplete rollback.

## Experimental macOS Receiver setup

macOS support is explicitly **Experimental** because no real-device verification has been recorded. The panel repeats this status during planning, approval, after setup, and in diagnostics; automated checks do not constitute a hardware compatibility claim. The current status and eventual real-device procedure are documented in [docs/testing/macos-experimental.md](docs/testing/macos-experimental.md).

The adapter supports known `arm64` and `x86_64` Homebrew layouts and fails closed for unknown architectures or executable locations. Its no-change probe covers macOS/OpenSSH versions, architecture, Remote Login, account privilege, Homebrew, and FFplay. After approval, the transparent POSIX Companion Setup can request native macOS approval for Remote Login, install the checksummed non-elevated Receiver helper and forced Managed Identity, and run the exact architecture-specific `brew install ffmpeg` command. SSH-mixer never installs Homebrew itself, bypasses Gatekeeper, clears quarantine metadata, or suppresses macOS security prompts.

Setup verifies the Homebrew formula and FFplay executable, receiver checksum, forced-key restrictions, non-root protocol runtime, arbitrary-command rejection, and forwarding rejection. The shared quiet test still requires audible-output confirmation before any bounded level increase. Failures use the same redacted Diagnostic Report and contribution-link flow as supported platforms; cleanup and incomplete rollback are reported explicitly.

A compatibility wrapper is included:

```bash
bin/cliamp-stream --source cliamp        # Both mode
bin/cliamp-stream --source cliamp --no-local
```

## Destination modes

- **Local**: no remote stream and no route changes.
- **SSH**: selected playback streams move to the temporary SSH-mixer sink and are not preserved locally while the session runs.
- **Both**: selected playback streams move to the temporary SSH-mixer sink and are looped back to their original output.

Changing the destination or selected inputs while a session is active applies the new choice immediately by stopping and restarting the session with the new routing.

Capture sources such as microphones are sent into the remote mix for SSH/Both, but SSH-mixer never loops microphones into local speakers automatically. Local mode means normal local capture availability. Output monitor sources are passive taps of existing output; SSH mode cannot suppress playback they monitor.

## Configuration

Protected configuration is stored at `~/.config/ssh-mixer/config.json` with mode `0600`; it may contain private application/device matching metadata but never temporary numeric source IDs. Runtime state and logs are stored under `~/.local/state/ssh-mixer/`.

Environment overrides:

- `SSH_MIXER_HOST`
- `SSH_MIXER_USER`
- `SSH_MIXER_KEY`
- `SSH_MIXER_BITRATE`
- `SSH_MIXER_CONNECT_TIMEOUT`

Required local commands: `pactl`, `ffmpeg`, `ssh`, `scp`, `ssh-keyscan`, `ssh-keygen`, `omarchy-shell`, `dbus-monitor`, `loginctl`, and `systemd-inhibit`. `ssh-add` is required only for the optional encrypted agent-backed Managed Identity, and `tailscale` only for a Tailscale Connection. The last four required desktop commands provide lock/session observation plus a short unprivileged delay window for ownership-safe cleanup before suspend or shutdown. Linux receivers use Python 3 and the installed, fixed Receiver Protocol; `ffplay` is provided by the receiver's trusted `ffmpeg` package. Windows receivers require supported Microsoft OpenSSH and PowerShell 5.1 or newer; optional FFplay installation uses the explicit Winget source.

Receiver commands are not configurable shell text. Streaming, capability checks, diagnostics, and bounded quiet testing use the versioned Receiver Protocol allowlist.

## Verified Receiver updates

SSH-mixer versions the plugin (`0.1.1`), each platform's Companion Setup (`1.1.1`), each Receiver helper (`1.1.1`), and Receiver Protocol (`1`) independently. A compatible installed helper continues working; a newer compatible helper is optional, while an incompatible protocol fails with guidance rather than silently replacing anything.

An update plan is accepted only from detached OpenSSH-signed release metadata using the `ssh-mixer-release` namespace and an explicitly reviewed `release/allowed_signers` trust root. The signed metadata binds a full source commit, immutable versioned GitHub release URLs, artifact sizes, SHA-256 checksums, protocol ranges, and the exact changes shown for approval. Metadata signatures are checked again immediately before download, and each downloaded artifact is staged privately and checked before an installer can run.

Installation requires the exact reviewed plan hash. Post-update platform, helper-version, and protocol verification is mandatory; failure invokes the platform transaction rollback and reports whether the prior version was restored. Staging cleanup is also verified. Update code never uses Windows execution-policy bypasses, disables Gatekeeper, or clears macOS quarantine warnings.

The release-specific production transaction uses native Bootstrap Authentication rather than expanding Managed Identity authority. It retains Receiver backups through post-update verification and commits or rolls back explicitly; dependency or system-service changes require a separate disclosed Companion Setup plan. The reviewed production trust root is committed, and this source pins Receiver release `1.1.1`; update operations fail closed until matching signed immutable metadata exists. See [release/README.md](release/README.md) for the manual metadata/signing process. Creating an update plan never installs it, and applying a current or changed plan still requires exact approval.

The source stream keeps 10 ms Opus frames and flushes each frame as an Ogg page. Receiver 1.1.1 makes FFplay follow its external monotonic clock for continuous correction. Because that correction did not prevent every observed long-running Windows drift event, plugin 0.1.1 also bounds each remote Stream Epoch: after 15 minutes it refreshes FFmpeg, SSH, and Receiver playback at the first second below -50 dBFS, or refreshes at a 30-minute hard limit. The Session, routing, and active indicator remain stable, but remote output may have a brief gap. Replacement is sequential and headless: it opens no terminal, window, display, notification, or overlapping playback pipeline. Starting new encoder and SSH processes has a brief unavoidable resource cost. Silence detection runs inside the existing encoder rather than as another process; its small continuous scan cost must be measured rather than described as literally zero. Receiver 1.1.0 remains Protocol-v1 compatible and receives the epoch bound without being silently replaced; its separately approved 1.1.1 update adds external-clock correction within each epoch. Audio coding stays at the configured bitrate, and the lower framing latency costs roughly 22 kbps of additional Ogg overhead.

## Troubleshooting

- Run `ssh-mixer test-connection` if Start fails before audio changes.
- Run `ssh-mixer stop` to restore moved playback streams and unload temporary PipeWire/PulseAudio modules.
- Run `pactl list short sinks` and `pactl list short modules` to check for leftover `ssh_mixer_mix` resources.
- Start application playback before refreshing if the stream is not listed yet.

## Custom builds and forks

SSH-mixer's MIT license permits owner-authorized source changes, including backend and Receiver behavior. Official safety controls are upstream defaults and contribution requirements, not technical restrictions on a downstream owner's build. Modified distributions need their own trust and release identity rather than claiming official signatures. See [Custom builds and forks](docs/custom-builds.md).

## Security, contributing, testing, and releases

- [Security model and complete permission inventory](docs/security-model.md)
- [Custom builds, forks, and downstream trust](docs/custom-builds.md)
- [Private vulnerability reporting policy](SECURITY.md)
- [Contribution and DCO requirements](CONTRIBUTING.md)
- [Linux, Windows, and future macOS real-device smoke procedures](docs/testing/smoke-tests.md)
- [Maintainer release process](docs/releasing.md)

## Removal

Use **Verified Removal → Plan full uninstall** in the panel. It lists configured Receivers, verifies key/helper cleanup, handles offline Pending Cleanup, removes sensitive local state, and invokes Omarchy plugin removal only after approval. Do not delete the plugin directory first: doing so can orphan Receiver access. See [Remove a Connection or uninstall](docs/user-guide.md#remove-a-connection-or-uninstall).

After verified uninstall, remove manually created compatibility symlinks if present:

```bash
rm -f ~/.local/bin/ssh-mixer ~/.local/bin/cliamp-stream
```

## License

MIT

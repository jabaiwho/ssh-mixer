# User guide

Read the [security model and permission inventory](security-model.md) before enabling SSH-mixer. Omarchy plugins are unsandboxed and run with the logged-in desktop user's authority.

## Supported Receivers

| Receiver | Status | Requirements |
| --- | --- | --- |
| Linux | Supported | Non-root account, OpenSSH, Python 3, FFplay/FFmpeg; Companion Setup supports apt, dnf, pacman, and zypper families |
| Windows | Supported | Microsoft OpenSSH 8.1 or newer and PowerShell 5.1 or newer; approved system changes may require Administrator confirmation |
| macOS | **Experimental** | Darwin on `arm64` or `x86_64`, OpenSSH Remote Login, and an existing architecture-standard Homebrew installation; `realDeviceVerified: false` |

The source desktop is Omarchy Linux with PipeWire/PulseAudio compatibility. Tailscale is recommended but optional.

## Install and first run

Plugin `v0.1.0` is reviewed source at one signed Git tag. Receiver `v1.1.0` is a separate [signed, attested, immutable production release](https://github.com/jabaiwho/ssh-mixer/releases/tag/receiver-v1.1.0), and the source pins its production metadata trust root. The plugin remains unsandboxed source rather than a binary package; review [SECURITY.md](../SECURITY.md) and pin the exact tag before enabling it.

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

Open **Settings → Audio → SSH-mixer**, or run:

```bash
omarchy-shell shell summon jabaiwho.ssh-mixer '{}'
```

Opening the panel, discovering sources, loading a Mix Profile, waking, unlocking, logging in, or reconnecting never starts audio. Start is always explicit.

Owners may create and operate modified source under the MIT license. The official safety policy is not an anti-modification boundary; downstream builds and owner-authorized agents follow the owner's chosen policy and use distinct release trust. See [Custom builds and forks](custom-builds.md).

On first run:

1. Review this guide, the unsandboxed-plugin warning, and the panel's privacy lifecycle controls.
2. If a personal pre-public configuration is detected, complete [legacy migration](#legacy-migration) before starting a new Session.
3. Choose and verify a Connection.
4. For a Tailscale or Direct SSH Connection, plan and approve the correct platform's Companion Setup to obtain a receiver-only Managed Identity.
5. Select current audio Sources. Capture Sources are microphones or other direct inputs and require explicit selection.
6. Optionally run the bounded quiet test.
7. Choose Local, SSH, or Both Route Mode and select **Start**.
8. Confirm the persistent bar indicator appears for the entire active Session.

## Choose a Connection

### Tailscale Connection — recommended

SSH-mixer reads `tailscale status --json`, records the selected peer identity, and checks on every connection that its name resolves to an address currently advertised for that online peer. It then performs normal OpenSSH host-key verification. SSH-mixer does not install Tailscale, configure a tailnet, or use Tailscale SSH.

Use this when both machines already belong to a trusted tailnet. Choosing Tailscale does not remove the need to review the Receiver host-key fingerprint.

### Direct SSH Connection

Enter a host, user, and port explicitly. Direct Connections use `-F /dev/null` and safety overrides, so user OpenSSH configuration, forwarding, local commands, agents, X11, and TTY behavior are not silently inherited.

Use this on a network you intentionally manage. The panel labels it as not Tailscale-verified.

### OpenSSH Profile Connection

Choose a concrete `Host` from the user's OpenSSH configuration. SSH-mixer inspects effective behavior with `ssh -F ~/.ssh/config -G`, follows `Include`, and uses that same explicit profile at runtime. This excludes system-wide SSH configuration unless your profile deliberately includes it. SSH-mixer can preserve ProxyJump or an explicitly confirmed ProxyCommand and rejects `Match exec`, wildcard-only entries, changed effective configuration, and undisclosed proxy changes.

Profile identities and authentication remain user-managed. SSH-mixer does not copy or reveal the profile's private-key paths, cannot guarantee receiver-only key permissions, and does not use this Connection for Managed Identity Companion Setup. The weaker policy remains visibly labelled.

## Review host trust

`ssh-keyscan` retrieves candidate keys but does not make them trustworthy. Compare every displayed SHA-256 fingerprint through an independent channel controlled by the Receiver owner. Examples include the Receiver console or a separately authenticated management channel. Do not approve a fingerprint merely because it appeared in the SSH-mixer panel.

Unknown keys remain untrusted. If a known key changes, SSH-mixer shows the old and new fingerprints and requires explicit replacement approval. A changed key can be a legitimate host rebuild or an attack; investigate before approval. Trust Records are private local files and are rechecked at use.

## Managed Identity and Companion Setup

For a Tailscale or Direct Connection, choose **Plan Linux Receiver**, **Plan Windows Receiver**, or **Plan macOS (Experimental)**. Planning performs a no-change capability probe and displays every file, package, service, firewall, ACL, Remote Login, or privilege change. Nothing is installed until the unchanged plan hash is approved.

The default Managed Identity is an unencrypted Ed25519 private key in a `0700` directory with a `0600` file mode. This supports deliberate background Sessions without a passphrase prompt. The optional encrypted choice delegates passphrase entry to `ssh-keygen` and requires an existing `ssh-agent`; SSH-mixer never receives the passphrase.

Bootstrap Authentication is one-time native OpenSSH authentication. Passwords, key passphrases, `sudo`, UAC, and macOS approval remain in their native prompts. After setup, SSH-mixer verifies that the generated key can execute only Receiver Protocol v1 and cannot obtain a shell, run an arbitrary command, forward ports or agents, request X11 or a PTY, or invoke user SSH startup scripts.

Review platform-specific permissions in [the security model](security-model.md#receiver-side-changes).

## Select Sources and Route Mode

- **Playback Source:** one application's current output.
- **Capture Source:** a microphone or other direct audio input.
- **Output Monitor:** a passive tap of audio already playing through an output.

Numeric PipeWire/PulseAudio IDs are runtime-only. Saved Source Matchers use stable metadata and restore only an exactly unique Playback Source or Output Monitor. Capture matchers are recent-choice hints only and never reselect a microphone.

Route Modes:

- **Local:** no remote stream and no SSH-mixer route change.
- **SSH:** selected playback is moved to the temporary mix and is not preserved locally for that Session. An Output Monitor cannot suppress the output it observes.
- **Both:** selected playback is streamed and looped back to its original output.

Capture is never looped into local speakers by SSH-mixer. Changing Source selection or Route Mode during an active Session performs an explicit stop/restart transition from the panel; it does not create an automatic future resume.

## Quiet test

The quiet test is optional and never changes system volume:

1. The first play is exactly `-40 dBFS`, 0.5 seconds, once, with fades.
2. Confirm whether it was audible.
3. Only an audible confirmation enables a separate **Increase by 4 dB** action.
4. Replay deliberately at the new level.
5. Never exceed the fixed `-24 dBFS` cap.

Technical playback success is not treated as proof that the user heard sound.

## Session privacy and lock behavior

Every active Session requires fresh protected heartbeats from both the keep-loaded lifecycle service and active-state bar widget. Playback shows an audio-stream icon. Capture shows a distinct urgent microphone/recording icon. Receiver labels are hidden by default; the active indicator cannot be hidden.

**Stop all on lock** is the default. **Continue playback on lock** can preserve only a non-Capture playback Session. Every Capture Session stops on lock and never resumes on unlock. Suspend, shutdown, logout, Receiver disconnect, fatal transport failure, lost lock observation, or lost privacy-service heartbeat also stops and cleans up.

Wake, unlock, login, source discovery, panel open, profile load, and network reconnection never start or resume a Session. Closing the panel does not stop a deliberately active Session; reopen it from the bar and choose **Stop**.

## Mix Profiles and Quick Start

A Mix Profile stores a Connection, Route Mode, Source Matchers, privacy policy, bitrate, and timeout. Loading one opens the mixer and never starts it.

Playback-only profiles may expose **Quick Start**. Quick Start itself is an explicit click and proceeds only if every source has one unique current match and no Session is already active. Profiles with Capture, missing sources, or ambiguous sources open the mixer for review instead of starting.

## Legacy migration

A detected pre-public configuration blocks new Sessions but does not interrupt an already active one. Detection reports reason codes and a one-way digest rather than the legacy values.

Choose one unchanged plan:

- **Import and secure:** approve host trust, create a protected exact backup, and use verified platform setup to replace legacy runtime access with a Managed Identity.
- **Keep user-managed:** retain the user's identity path with a permanent weaker-permissions warning, replace arbitrary command state with fixed Receiver Protocol v1, and discard temporary source IDs.
- **Start fresh:** remove legacy Receiver and source choices and return to defaults.

A failure restores the prior configuration byte-for-byte and retains the `0600` backup. The backup is retired only after post-migration verification. Migration waits rather than stopping an active Session.

## Diagnostics and normal failure reports

A Diagnostic Report is generated and redacted locally. It contains no audio and is not uploaded automatically. Retention is always byte-bounded. Choose **Minimal** (one day or five Sessions), **Standard** (the default: seven days or twenty Sessions), or **Extended** (thirty days or fifty Sessions); the age or Session limit that removes data first wins. **Verbose next Session** expires after that one Session.

1. Select **Prepare report**.
2. Decide whether to include bounded redacted operational events.
3. Read and edit the entire body.
4. Select **Report on GitHub** only for a normal failure; this opens a prefilled issue URL in the browser.
5. Submit only after another review in the browser.

Use **Clear diagnostics** to delete retained events immediately. **Contribute a fix** opens the reviewed contribution, DCO, security, test, and platform-evidence requirements before you prepare a pull request; it does not fork, push, or submit anything automatically. Do not report a suspected vulnerability in a public issue; follow [SECURITY.md](../SECURITY.md).

## Updates

Creating an update plan never installs anything. This source pins Receiver release `1.1.1`; a missing, unpublished, changed, invalid, or incompatible signed immutable release fails closed before installation. The 10 ms source cadence applies with an older compatible Protocol-v1 helper, but external-clock drift correction requires Receiver 1.1.1. Receiver 1.1.0 is not silently replaced; use manual Refresh as its recovery path until you separately approve the signed update.

When configured, **Check signed Receiver update** verifies the plugin-pinned metadata signature, checks current Receiver capabilities, and displays exact component, native-authentication, privilege, and rollback changes. The Managed Identity cannot update executable code. After unchanged plan-hash approval, SSH-mixer uses native OpenSSH authentication, verifies immutable URL scope, byte size, and SHA-256, retains protected Receiver backups, runs the signed Companion Setup, verifies platform, helper version, protocol compatibility, restrictions, and non-elevated runtime, then commits. Failure restores the prior helper and exact authorized-key file and reports incomplete rollback honestly. Private source staging is also verified removed.

A loaded SSH agent or hardware token may complete native authentication without a password prompt. Windows administrator-authorized-key ACLs may require disclosed UAC approval. Routine update planning refuses package, SSH service, firewall, Remote Login, or dependency changes and directs the user to a separate Companion Setup plan. An active Session defers the update without stopping it.

See [the release process](releasing.md) for maintainer controls, signing, provenance, rollback, and publication requirements.

## Remove a Connection or uninstall

Use **Verified Removal** in the panel. Do not delete the plugin directory first.

Connection removal asks the Receiver to revoke only that Managed Identity and verifies the result. The helper and quiet-test state are removed only when no other SSH-mixer Managed Identity remains. SSH-mixer then deletes its owned private key, Trust Record, matching Mix Profiles, and diagnostics.

Offline or partial cleanup is **pending — not revoked**. Retry through SSH-mixer when reachable, or use the platform Companion Setup's key-specific remove mode on the Receiver. New Sessions remain blocked while cleanup is pending.

Abandonment is available only after a cleanup attempt is pending. It requires a separate informed confirmation, deletes local retry credentials, and is always reported as `abandoned-not-revoked`. It cannot revoke or delete a user-managed key.

**Full uninstall** first lists all Connections found in current configuration and Mix Profiles. It resolves each cleanup, verifies local sensitive application roots absent, and only then invokes Omarchy plugin removal. Pending cleanup blocks uninstall unless separately abandoned. After verified uninstall, remove a manually created CLI symlink if present:

```bash
rm -f ~/.local/bin/ssh-mixer ~/.local/bin/cliamp-stream
```

## CLI discovery

The Panel is the normal guided interface. The CLI exposes the same structured seams:

```bash
ssh-mixer --help
ssh-mixer snapshot
ssh-mixer status
ssh-mixer stop
ssh-mixer diagnostics-preview
ssh-mixer removal-inspect
```

Mutation commands accept JSON and require exact approvals. Prefer the Panel unless integrating against the documented JSON behavior and preserving all review steps.

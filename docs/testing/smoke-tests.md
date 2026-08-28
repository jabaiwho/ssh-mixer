# Real-device smoke tests

These procedures produce sanitized evidence for release review. They do not authorize installation on someone else's machine, publication, or a support-status change.

## Evidence rules

For every run, record:

- exact SSH-mixer source commit or release version;
- source Omarchy version and whether PipeWire/PulseAudio compatibility is active;
- Receiver platform/version, CPU architecture, OpenSSH version, account class, and tested Connection type;
- Companion and Receiver helper versions plus Receiver Protocol version;
- pass/fail for each numbered step, rollback status, and cleanup status; and
- what was not tested.

Never record hostnames, usernames, IP addresses, tailnet/peer identities, public or private keys, home paths, application/device names, complete commands containing destinations, unredacted diagnostics, or audio. Use placeholders such as `SOURCE`, `RECEIVER`, and `TEST_APPLICATION`. Do not attach configuration, Trust Records, `authorized_keys`, or Session logs.

Use a disposable Receiver/account for setup, rollback, update, and removal failure tests. Use generated non-sensitive audio; do not select a microphone for a general playback smoke. Capture lifecycle tests should use a test input with no private conversation and should record only state transitions, never audio.

Before each platform run:

```bash
python3 scripts/check_repository.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
omarchy plugin validate .
python3 scripts/check_stream_latency.py
```

The latency check must report 10 ms frames, 10,000 µs Ogg pages, and no audio page longer than 480 samples at 48 kHz.

Confirm the source working tree is clean and the exact commit was reviewed. Back up Receiver state through the platform's normal administrative process. Do not bypass a native warning or prompt.

## Linux source and Linux Receiver

Test at least one supported native package family affected by the release. If package logic changed, test every affected family when devices are available and list missing families explicitly.

1. **No-change discovery:** Open the panel and confirm it does not start a Session. Verify Playback, Capture, and Output Monitor labels are distinct and no temporary numeric source ID is persisted after refresh.
2. **Connection choices:** Exercise the Connection type changed by the release. For Tailscale, confirm an offline/identity/address mismatch fails before SSH. For Direct SSH, confirm user OpenSSH configuration is not inherited. If OpenSSH Profile behavior changed, separately test Include/ProxyJump and explicit ProxyCommand review, plus `Match exec` rejection.
3. **Host trust:** On the Receiver console, obtain the host-key fingerprint through a trusted channel. Confirm unknown trust displays the same fingerprint, declining writes nothing, approval creates a private Trust Record, and a changed candidate fails until separately approved. Restore the real key after the negative test.
4. **Setup plan:** Run **Plan Linux Receiver**. Confirm the plan is no-change and accurately lists user files, exact package manager/packages, and each `sudo` command. Confirm direct root setup is rejected.
5. **Managed Identity setup:** Approve the unchanged plan. Complete only native OpenSSH/`sudo` prompts. Verify helper checksum/version, one key-specific `authorized_keys` entry, private local key modes, and no unrelated key replacement.
6. **Restriction checks:** Confirm automated verification passes for capabilities and fails for shell/arbitrary command, remote forwarding, agent forwarding, X11, PTY, and user-RC behavior. Verify the runtime account is non-root.
7. **Playback Session:** Select generated TEST_APPLICATION playback, choose SSH, Start explicitly, and confirm audio only on the Receiver. With generated audiovisual material, compare immediate synchronization with synchronization after at least 30 minutes; confirm latency remains visually stable, then deliberately refresh once and confirm no accumulated multi-second queue was hidden. Record the output transport class without its product/device name. Stop and verify moved playback and temporary `ssh_mixer_mix` resources are restored/removed.
8. **Both Session:** Repeat in Both and confirm local plus Receiver audio without creating a microphone feedback route.
9. **Source restoration:** Save a playback-only Mix Profile, restart TEST_APPLICATION so temporary IDs change, and confirm exactly one stable match restores. Create missing and ambiguous conditions and confirm nothing starts. Confirm profile load alone never starts.
10. **Capture privacy:** Select only the controlled test Capture Source. Confirm the urgent persistent indicator, hidden Receiver label default, and no local monitor loop. Lock the source and verify Capture stops and does not resume on unlock. Repeat with continue-playback policy and verify Capture still stops.
11. **Lifecycle:** For playback, test default lock stop, optional continue-playback, suspend/wake, logout in a disposable login session, Receiver disconnect, and fatal transport exit. Confirm ownership-safe cleanup and no automatic wake/unlock/reconnect resume.
12. **Indicator failure:** In a disposable source session, make the lifecycle or indicator heartbeat unavailable and confirm Start fails or the active Session stops. Restore Omarchy normally; do not patch out the check.
13. **Quiet test:** Start at `-40 dBFS`. Confirm one 0.5-second faded play, unchanged system volume, required audible confirmation, 4 dB-only increases, and the `-24 dBFS` cap.
14. **Diagnostics:** Trigger an invented host failure. Preview with and without logs, verify no audio or machine identifiers, edit the body, and stop before submitting. Confirm verbose mode expires after one Session and Clear removes events.
15. **Rollback:** On the disposable Receiver, induce one safe setup verification failure after a file change. Confirm prior files restore or an incomplete rollback is explicit. Never convert an incomplete result to success.
16. **Removal:** Add a second disposable Managed Identity to confirm shared helper behavior. Remove one Connection and verify only its key/local state disappears while the helper remains. Remove the last and verify key, helper, quiet state, local identity, Trust Record, matching Mix Profiles, and diagnostics are absent.
17. **Offline cleanup:** Repeat removal while the Receiver is offline. Confirm Pending Cleanup is `0600`, retry credentials remain, new Session start is blocked, and plugin uninstall is not invoked. Restore connectivity and verify retry. Separately verify informed abandonment only after a pending attempt and that the result is `abandoned-not-revoked`.
18. **Independent cleanup:** On the Receiver, run `setup-v1.sh remove MANAGED_PUBLIC_KEY_BODY` for a disposable key and verify unrelated keys/packages remain.
19. **Full uninstall:** With disposable state, review the complete Receiver list. Confirm Omarchy removal is called only after every cleanup and sensitive source-side SSH-mixer root verifies absent. Confirm a manually created CLI symlink can be removed separately.

## Linux source and Windows Receiver

Use a disposable supported Windows installation with Microsoft OpenSSH 8.1 or newer and PowerShell 5.1 or newer. Record whether the account is standard or Administrator-capable and whether native elevation was required; do not record its name or SID.

1. Complete Linux steps 1–3 for source discovery, Connection, and host trust.
2. Run **Plan Windows Receiver** and confirm it is no-change. Review detected OpenSSH capability/service/version, firewall rule and port, account capability/elevation, authorized-key location, Winget, and FFplay.
3. For an Administrator-capable account, confirm setup cannot proceed until capability is acknowledged and re-planned. Confirm the approval does not weaken the requirement for non-elevated Receiver runtime.
4. Approve setup. Observe native Windows security prompts without bypass. Confirm only the planned Microsoft OpenSSH capability/service/firewall changes, explicit `winget` source operation, helper, key entry, and ACL changes occur.
5. Verify the authorized-key ACL owner/protection/allow-list appropriate to the selected standard/administrator path. Confirm unrelated authorized keys remain.
6. Verify helper checksum/version, Protocol capabilities, `runtimeElevated: false`, and rejection of shell/arbitrary command, forwarding, agent, X11, PTY, and user startup behavior.
7. Run Linux steps 7–14 for SSH/Both playback, long-running synchronization, stable restoration, controlled Capture lifecycle, indicator/lifecycle failure, bounded quiet test, and diagnostics. For the latency run, compare immediate lip sync with at least 30 minutes of the same generated audiovisual material and verify a refresh does not reveal accumulated multi-second drift. Confirm the Windows Receiver never changes system volume.
8. In a disposable snapshot, induce safe failures for helper copy, ACL verification, Winget/FFplay verification, and post-install protocol verification. Confirm files/ACLs/capability/firewall/package state restore where safe and incomplete rollback is explicit where not.
9. Test key-specific removal with another managed key present, last-key helper removal, offline Pending Cleanup/retry, and user-managed abandonment semantics.
10. Run the Windows Companion independently with `setup-v1.ps1 -Mode Remove -KeyBody MANAGED_PUBLIC_KEY_BODY`. Confirm the selected key is absent, unrelated keys remain, helper removal accurately reflects sharing, and a required Administrator prompt is not bypassed.
11. Complete full uninstall and verify source sensitive state is absent before Omarchy plugin removal.

## Update smoke matrix

Run only after the release trust root, signed metadata, attestations, immutable assets, and exact platform transaction have separate approval.

For each available supported Receiver platform:

1. In an isolated fixture with the trust-root file intentionally unavailable, verify planning fails closed before any download or Receiver contact.
2. With the reviewed trust root, test malformed metadata, wrong namespace, unknown signer, bad signature, mutable/cross-repository URL, wrong size, and wrong SHA-256; no installer may run.
3. Confirm the exact plan lists component, installed/target versions, protocol range, changes, privilege behavior, and rollback expectation.
4. Change metadata after planning and confirm execution rejects it.
5. Confirm an active Session defers without interruption.
6. Apply the exact approved release, verify helper/platform/protocol/restrictions, and verify private staging absence.
7. Trigger controlled post-install verification failure and confirm prior-version rollback status.
8. Verify a compatible current/newer helper is not forcibly replaced and a downgrade is refused.

Cross-check downloaded assets against [the release process](../releasing.md), including OpenSSH signature, size, SHA-256, full source commit, immutable URL, and GitHub attestation.

## Future real-device macOS procedure

No real-device macOS evidence is currently recorded. Hosted CI does not satisfy this section. Any future run must preserve **Experimental**, `experimental: true`, and `realDeviceVerified: false` throughout evidence and UI unless a later separate product decision changes that status.

Use a disposable `arm64` or `x86_64` Mac with its architecture-standard existing Homebrew prefix. Follow [docs/testing/macos-experimental.md](macos-experimental.md) and additionally cover:

1. Tailscale/Direct Connection and independent host-fingerprint comparison.
2. Remote Login state before/after setup and rollback, including native approval without bypass.
3. Confirmation that SSH-mixer never installs Homebrew and uses only `/opt/homebrew` on `arm64` or `/usr/local` on `x86_64`.
4. Helper/key restrictions, non-root runtime, SSH/Both generated playback, controlled Capture lock behavior, persistent indication, and bounded quiet test.
5. Setup/update failure rollback without Gatekeeper bypass, quarantine clearing, or warning suppression.
6. Shared and final key-specific cleanup plus independent `setup-v1.sh remove MANAGED_PUBLIC_KEY_BODY`; preserve unrelated keys and Homebrew formulas.
7. Full source cleanup and Omarchy removal ordering.

A successful run may be attached as sanitized evidence but does not by itself remove the Experimental label. Record a failed or unavailable step honestly.

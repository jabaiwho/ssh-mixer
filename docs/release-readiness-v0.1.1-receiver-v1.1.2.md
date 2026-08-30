# Plugin 0.1.1 and Receiver 1.1.2 release readiness

This is the live follow-up record for [tracker #20](https://github.com/jabaiwho/ssh-mixer/issues/20). It records evidence and blockers without granting authority to install a Receiver helper, use signing keys, create or push tags, run attestations, publish, or announce.

## Intended release units

- Omarchy plugin: `0.1.1`, eventually tagged `v0.1.1`.
- Linux, Windows, and Experimental macOS Companion/Receiver artifacts: `1.1.2`, eventually tagged and released as `receiver-v1.1.2`.
- Receiver Protocol: unchanged at v1, minimum 1 and maximum 1.
- macOS: remains **Experimental** with `realDeviceVerified: false`.

Plugin and Receiver release decisions are independent even when they use one reviewed source commit. The installed Windows Receiver remains 1.1.1 until a separate exact plan is reviewed and approved.

## Candidate status

No final release commit is pinned. The merged implementation baseline was `db5f749e3e4e6d362934084f9d3b755b6dde0b2d`; release-tooling and documentation corrections necessarily create a later candidate. Pin only the final protected `main` commit after every preparation change and required CI pass.

Published production versions remain plugin `v0.1.0` and immutable Receiver `v1.1.0`. Source version declarations for 0.1.1 and 1.1.2 are not release evidence.

## Evidence already available

- The implementation baseline passed 214 Python tests and 5 headless QML behavior tests.
- Hosted Linux/QML, Windows, and Experimental macOS jobs passed on the implementation branch and merged `main`.
- The 31-commit public history passed the normal DCO, credential, private-default, path, and privacy audit.
- Exact merged plugin files were installed and byte-verified locally; the restarted runtime exposed the current accordion and Desktop (All) exclusivity while stopped.
- Earlier Windows 1.1.1 evidence demonstrated sustained playback and the Receiver Protocol restrictions, but it does **not** prove the changed 1.1.2 Receiver-test behavior or exact 1.1.2 transaction bytes.
- The initial 1.1.0 release remains independently signed, attested, immutable, and verified. Its evidence cannot be reused as 1.1.2 provenance.

## Source and repository gates

- [ ] Merge the follow-up history/attestation workflow correction from [issue #21](https://github.com/jabaiwho/ssh-mixer/issues/21).
- [ ] Run the complete portable checks and normal `check_public_history.py` audit from a clean checkout of the final candidate.
- [ ] Confirm hosted Linux, Windows, and Experimental macOS jobs pass on the final candidate.
- [ ] Confirm all candidate commits have DCO sign-off and no sensitive local/runtime artifacts are reachable.
- [ ] Review the final diff against issues #11–#18, tracker #20, `CONTRIBUTING.md`, the security model, and relevant ADRs.
- [ ] Confirm branch protection, required CI, linear history, restricted Actions, private vulnerability reporting, and immutable-release settings remain enabled.
- [ ] Confirm `manifest.json`, `PLUGIN_VERSION`, all Companion/Receiver versions, the pinned Receiver release, protocol bounds, and change records match the intended bytes.

## Plugin real-device gates

Tracked in [issue #22](https://github.com/jabaiwho/ssh-mixer/issues/22).

- [ ] Confirm Desktop (All) automatically replaces every other Source and produces no doubling, reverberation, application move, or preserve-local loopback in This PC, Receiver, and Both modes.
- [ ] Confirm selected-application late attachment and unselected-application isolation.
- [ ] Confirm Selected, Pinned, and Recently Used behavior, including Clear recent and no Capture history.
- [ ] Confirm keyboard section transitions, whole-section scrolling, mouse-pinned sections, outlined options, and fixed clickable `mx-streaming`/`mx-capture` indicators.
- [ ] Confirm physical-default preservation and bounded process/module counts through repeated Start, refresh, and Stop cycles.
- [ ] Record silence-triggered refresh after minute 15 and one uninterrupted or accelerated hard-deadline refresh at minute 30, including bounded gap and steady process count.
- [ ] Confirm stopped cleanup leaves no SSH-mixer modules or transport processes.

## Receiver 1.1.2 gates

- [ ] Prepare a read-only exact Windows update plan and obtain separate approval for its unchanged hash.
- [ ] Install only after a stopped Session; verify exact 1.1.2 Companion/Receiver bytes, platform, Protocol v1, forced-command restrictions, forwarding/PTY rejection, ACLs, non-elevated runtime, and staging cleanup.
- [ ] Confirm the Receiver-test slider is silent while moving, defaults to `-32 dBFS`, accepts each whole dB from `-40` through `0`, rejects malformed/out-of-range levels, warns visibly at high levels, and emits only one faded 0.5-second tone after explicit Play.
- [ ] Repeat sustained application and Desktop playback without duplicate audio or synchronization regression.
- [ ] Exercise a controlled post-install verification failure and prove exact rollback or explicit incomplete rollback.
- [ ] Exercise checksum/signature failure and prove no installer runs.
- [ ] Verify active Session update deferral without interruption.
- [ ] Record Linux 1.1.2 transaction and audio evidence on an available disposable Receiver, or explicitly record unavailable coverage.
- [ ] Keep macOS Experimental and record that no real-device claim exists unless separately reviewed evidence is supplied.

## Build, signing, and attestation gates

Tracked in [issue #23](https://github.com/jabaiwho/ssh-mixer/issues/23).

- [ ] Pin one exact protected release commit after all preparation changes.
- [ ] Review `release/receiver-v1.1.2-changes.json` for every platform/component.
- [ ] Build all six Receiver artifacts and compact metadata twice from separate clean checkouts with identical commit, timestamp, and changes; compare every byte.
- [ ] Independently verify filenames, immutable URLs, byte sizes, SHA-256 values, release ID, full source commit, and protocol bounds.
- [ ] Make the approved removable signing storage available and verify the existing Git tag and metadata signer fingerprints. Never generate an improvised replacement key.
- [ ] Obtain separate approval before locally creating signed `v0.1.1` and `receiver-v1.1.2` tags.
- [ ] Verify both tags and run `check_public_history.py --tag TAG` at the exact candidate.
- [ ] Sign the exact metadata bytes with the approved `ssh-mixer-release` namespace and independently verify against committed `release/allowed_signers`.
- [ ] Obtain separate approval before pushing each tag.
- [ ] Obtain separate approval for the exact attestation workflow commit and UTC timestamp.
- [ ] Download and independently verify every attested artifact, metadata subject, and `SHA256SUMS` against repository, workflow, commit, and digest.

## Publication and post-publication gates

- [ ] Manually compare approved scope, source commit, tags, six artifact bytes, checksums, metadata signature, attestations, compatibility, real-device evidence, rollback, cleanup, and macOS wording.
- [ ] Obtain separate approval before staging and publishing the immutable Receiver 1.1.2 release.
- [ ] From a clean environment, download every final asset and recheck size, SHA-256, metadata signature, source commit, tag, and attestation.
- [ ] Verify the signed runtime update plan and transaction against final immutable URLs, followed by no-change planning after installation.
- [ ] Create or update plugin-facing release notes and switch README/user-guide installation instructions to `v0.1.1` only when the signed tag is actually available.
- [ ] Confirm security-reporting and documentation links resolve.
- [ ] Obtain a separate decision before any announcement.

## Current blockers

- Follow-up release tooling is not yet merged.
- Plugin post-fix real-device audio and Stream Epoch evidence is incomplete.
- Windows Receiver 1.1.2 is not installed or transaction-tested.
- Linux 1.1.2 real-device availability/evidence is unresolved.
- The approved signing storage is not currently available, so tag and metadata signing are blocked.
- No final release commit, signed 0.1.1/1.1.2 tags, 1.1.2 metadata signature, 1.1.2 attestations, immutable Receiver release, or publication approval exists.

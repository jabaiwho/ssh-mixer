# Plugin 0.1.1 and Receiver 1.1.2 release readiness

This is the live follow-up record for [tracker #20](https://github.com/jabaiwho/ssh-mixer/issues/20). It records evidence and blockers without granting authority to install a Receiver helper, use signing keys, create or push tags, run attestations, publish, or announce.

## Intended release units

- Omarchy plugin: `0.1.1`, eventually tagged `v0.1.1`.
- Linux, Windows, and Experimental macOS Companion/Receiver artifacts: `1.1.2`, eventually tagged and released as `receiver-v1.1.2`.
- Receiver Protocol: unchanged at v1, minimum 1 and maximum 1.
- macOS: remains **Experimental** with `realDeviceVerified: false`.

Plugin and Receiver release decisions are independent even when they use one reviewed source commit. Windows Receiver 1.1.2 is installed and transaction-verified, but installation is not signing or publication authority.

## Candidate status

No final release commit is pinned. Real-device evidence is bound to merged and installed commit `a2269da9131d24a6fdea12a78c72707d4398c164`; this readiness update necessarily creates a later documentation-only candidate. Pin only the final protected `main` commit after review, merge approval, required CI, and a repeated clean-checkout audit.

Published production versions remain plugin `v0.1.0` and immutable Receiver `v1.1.0`. Source version declarations and an installed 1.1.2 helper are not release evidence or signed release availability.

## Evidence completed at the installed implementation baseline

- 220 Python tests, the headless QML behavior phases, repository checks, stream-latency validation, compilation/syntax/lint checks, and plugin validation passed.
- Hosted Linux/QML, Windows, and Experimental macOS jobs passed on merged `a2269da9131d24a6fdea12a78c72707d4398c164`.
- The 42-commit public history passed DCO, credential, private-default, path, and privacy audits.
- Exact merged plugin files were installed and byte-verified locally; runtime acceptance covers logical Inputs, selection-driven lifecycle, Capture consent, keyboard navigation, prominent failures, Stream Epoch replacement, and cleanup.
- Windows 1.1.2 completed a separately approved exact-plan install/verify/rollback rehearsal and a separately approved final install/verify/commit transaction under a standard account with the Receiver-only Managed Identity.
- Windows 1.1.2 Receiver-test and application/Desktop playback passed machine checks and human audio acceptance. Malformed and out-of-range quiet-test requests were rejected by the installed helper before playback.
- No disposable or configured Linux Receiver is available. Automated Linux coverage passes, but no Linux 1.1.2 real-device claim is made.
- macOS remains Experimental with `realDeviceVerified: false`; no macOS real-device claim is made.

Sanitized evidence details and protected local-result digests are recorded on [issue #22](https://github.com/jabaiwho/ssh-mixer/issues/22). Failed diagnostic-harness attempts remain recorded as harness failures and are not represented as Receiver failures.

## Source and repository gates

- [x] Merge the follow-up history/attestation workflow correction from [issue #21](https://github.com/jabaiwho/ssh-mixer/issues/21).
- [x] Run the complete portable checks and normal `check_public_history.py` audit from clean `a2269da9131d24a6fdea12a78c72707d4398c164`.
- [x] Confirm hosted Linux, Windows, and Experimental macOS jobs pass on merged `a2269da9131d24a6fdea12a78c72707d4398c164`.
- [x] Confirm all 42 implementation-baseline commits have DCO sign-off and no sensitive local/runtime artifacts are reachable.
- [x] Confirm branch protection, required CI, linear history, restricted Actions, private vulnerability reporting, and immutable-release settings remain enabled.
- [x] Confirm `manifest.json`, `PLUGIN_VERSION`, all Companion/Receiver versions, the pinned Receiver release, Protocol bounds, and change records match the intended 0.1.1/1.1.2 source bytes.
- [ ] Review the final cumulative diff against issues #11–#18, #25–#42, tracker #20, `CONTRIBUTING.md`, the security model, and relevant ADRs.
- [ ] Merge this readiness update only after explicit approval and required CI.
- [ ] Repeat portable checks, normal public-history audit, sensitive-path reachability review, hosted CI confirmation, and clean-checkout tree comparison at the final protected candidate.

## Plugin real-device gates

Tracked in [issue #22](https://github.com/jabaiwho/ssh-mixer/issues/22).

- [x] Confirm Desktop (All) automatically replaces every other Source and produces no doubling, reverberation, application move, or preserve-local loopback in This PC, Receiver, and Both modes.
- [x] Confirm selected-application late attachment and unselected-application isolation.
- [x] Confirm Selected, Pinned, and Recently Used behavior, including Clear recent and no Capture history.
- [x] Confirm keyboard section transitions, whole-section scrolling, mouse-pinned sections, outlined options, fixed clickable `mx-streaming`/`mx-capture` indicators, and prominent failures above Inputs.
- [x] Confirm physical-default preservation and bounded process/module counts through repeated Input selection, refresh, automatic selection changes, final deselection, and End Stream cycles.
- [x] Record silence-triggered refresh after minute 15 and accelerated hard-deadline refresh, including bounded gaps, stable Session identity, process bounds, and explicit retained test arithmetic.
- [x] Confirm intentional replacement-launch failure stops in visible error with zero live owned resources.
- [x] Confirm every Capture start/restart remains confirmation-bound, Stop-all lock stops Capture while locked, unlock does not resume, physical defaults restore, and synthetic Capture artifacts clean completely.
- [x] Confirm stopped cleanup leaves no SSH-mixer modules, transport processes, moved streams, synthetic Capture source/writer/timer, or selected Inputs.

## Receiver 1.1.2 gates

- [x] Prepare a read-only exact Windows update plan and obtain separate approval for its unchanged hash.
- [x] Install only after a stopped Session; verify exact 1.1.2 Companion/Receiver bytes, platform, Protocol v1, forced-command restrictions, forwarding/PTY rejection, ACLs, non-elevated runtime, identity reuse, staging cleanup, and post-commit state.
- [x] Confirm the Receiver-test slider is silent while moving, defaults to `-32 dBFS`, accepts each whole dB from `-40` through `0`, rejects malformed/out-of-range levels, warns visibly at high levels, and emits only one faded 0.5-second tone after explicit Play.
- [x] Repeat selected-application and Desktop (All) playback beyond the prior early cutoff without duplicate audio, synchronization regression, default-sink replacement, resource growth, or cleanup residue.
- [x] Exercise a controlled post-install verification rollback rehearsal and prove exact restoration to 1.1.1 before the separately approved final 1.1.2 commit.
- [x] Exercise checksum/signature failure at the public update-service seam and prove no installer runs.
- [x] Verify active-Session update deferral without interruption at the public application seam. Unpublished signed 1.1.2 metadata prevents misrepresenting this automated seam as a live production-update test.
- [x] Record Linux 1.1.2 real-device coverage as unavailable; retain passing automated Linux coverage without a real-device claim.
- [x] Keep macOS Experimental and record `realDeviceVerified: false` with no real-device claim.

## Build, signing, and attestation gates

Tracked in [issue #23](https://github.com/jabaiwho/ssh-mixer/issues/23).

- [ ] Pin one exact protected release commit after all preparation changes.
- [ ] Review `release/receiver-v1.1.2-changes.json` for every platform/component.
- [ ] Build all six Receiver artifacts and compact metadata twice from separate clean checkouts with identical commit, timestamp, and changes; compare every byte.
- [ ] Independently verify filenames, immutable URLs, byte sizes, SHA-256 values, release ID, full source commit, and Protocol bounds.
- [ ] Make the approved removable signing storage available and verify the existing Git tag and metadata signer fingerprints. Never generate an improvised replacement key.
- [ ] Obtain separate approval before locally creating signed `v0.1.1` and `receiver-v1.1.2` tags.
- [ ] Verify both tags and run `check_public_history.py --tag TAG` at the exact candidate.
- [ ] Sign the exact metadata bytes with the approved `ssh-mixer-release` namespace and independently verify against committed `release/allowed_signers`.
- [ ] Obtain separate approval before pushing each tag.
- [ ] Obtain separate approval for the exact attestation workflow commit and UTC timestamp.
- [ ] Download and independently verify every attested artifact, metadata subject, and `SHA256SUMS` against repository, workflow, commit, and digest.

## Publication and post-publication gates

- [ ] Manually compare approved scope, source commit, tags, six artifact bytes, checksums, metadata signature, attestations, compatibility, real-device evidence, rollback, cleanup, unavailable Linux coverage, and Experimental macOS wording.
- [ ] Obtain separate approval before staging and publishing the immutable Receiver 1.1.2 release.
- [ ] From a clean environment, download every final asset and recheck size, SHA-256, metadata signature, source commit, tag, and attestation.
- [ ] Verify the signed runtime update plan and transaction against final immutable URLs, followed by no-change planning after installation.
- [ ] Create or update plugin-facing release notes and switch README/user-guide installation instructions to `v0.1.1` only when the signed tag is actually available.
- [ ] Confirm security-reporting and documentation links resolve.
- [ ] Obtain a separate decision before any announcement.

## Current blockers

- This readiness update is unmerged; no final protected release commit is pinned.
- Final-candidate cumulative review, clean-checkout checks, public-history audit, hosted CI confirmation, and deterministic artifact/metadata builds remain incomplete.
- The approved signing storage is unavailable, so tag and metadata signing are blocked. No replacement key may be improvised.
- No signed 0.1.1/1.1.2 tags, 1.1.2 metadata signature, 1.1.2 attestations, immutable Receiver release, publication approval, or announcement approval exists.
- Linux 1.1.2 real-device coverage is unavailable, and macOS remains Experimental with no real-device claim.

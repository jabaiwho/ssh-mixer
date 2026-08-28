# Initial public-release readiness — completed record

This document began as the maintainer checkpoint for issue #18. Its pre-publication review narrative is retained as historical evidence; statements describing then-open gates or a private canonical repository are not current status.

The approved outcome is plugin `v0.1.0` and immutable Receiver `v1.1.0` at source commit `917f812bf2c5b4a63de6b5c59f43b904600858d9`. Hosted CI run `33139410848` passed all required Linux/QML, Windows, and Experimental macOS jobs. Attestation run `33139861588` produced provenance for the six artifacts, metadata, and `SHA256SUMS`; all eight subjects were downloaded, matched byte-for-byte, and verified against the exact workflow, source commit, and `refs/heads/main`. The metadata SHA-256 is `2dea724307b32789b4450178e0d523d6f29d5a096b2e6f1fc73d640cd19c3770`. The final public release is <https://github.com/jabaiwho/ssh-mixer/releases/tag/receiver-v1.1.0>.

## Pinned review range

- intended merge target fetched from GitHub: `origin/main` at `8ad3a92a7411030dbcb0e13a4ed2af73387fe2d3`;
- merge-base: the same commit;
- reviewed development range: `git diff 8ad3a92a7411030dbcb0e13a4ed2af73387fe2d3...feat/secure-public-v1`;
- specification: GitHub issue #2 and `docs/specs/secure-public-v1.md`;
- release ticket: GitHub issue #18;
- standards: `AGENTS.md`, `CONTRIBUTING.md`, `CONTEXT.md`, and the ADRs.

Pin the final reviewed private commit and clean public root in the private release record after the remaining local changes are committed. Never substitute a moving branch name at a signing or cutover gate.

## Review results

### Standards

No unresolved source-standard blocker remains in the reviewed tree. The final public history is not the current development ancestry: every current inherited/development commit predates or lacks the new DCO footer requirement. The initial public candidate must therefore be the one-commit, DCO-signed-off clean root described in [the release process](releasing.md#clean-public-history-candidate).

The large operation dispatch in `MixerApplication` and corresponding panel adapter is a maintainability judgement call, not a release blocker: it keeps policy behind one structured application seam, uses fixed operation names, and is covered at public application/CLI/protocol seams. It must not become dynamic code or Receiver-command dispatch.

### Specification

The review found and corrected three gaps:

1. the required shorter/longer diagnostic retention choice was missing; retention now offers bounded Minimal, Standard, and Extended policies while preserving byte caps and one-Session verbose state;
2. the **Contribute a fix** action was missing; it now opens fixed reviewed guidance and never forks, pushes, or submits automatically; and
3. OpenSSH Profile inspection/runtime could inherit system-wide configuration outside the files scanned for `Match exec`; both now use the same explicit `-F ~/.ssh/config` boundary, while deliberate user `Include` files remain supported.

The CI definition now adds Linux no-change/rejection checks and actual QML parsing. Linux/Windows remain supported and macOS remains explicitly Experimental with `realDeviceVerified: false`.

A native-authenticated production update transaction and manual pinned attestation workflow are now implemented. The Managed Identity cannot replace code; routine updates retain rollback material and refuse undisclosed dependency/system changes. Installation remains intentionally fail-closed rather than falsely complete because signed metadata, hosted/real-device transaction evidence, and attestations remain open release gates below.

### Security and privacy

The manual review covered command construction, fixed Receiver Protocol parsing, host trust, profile handling, native privilege boundaries, protected storage and symlink rejection, process/audio ownership, diagnostics, lifecycle behavior, migration, removal, update staging, downloads, and platform scripts. No dynamic `eval`, `exec`, `shell=True`, download-and-pipe installer, silent host trust, automatic report, automatic update, or microphone auto-resume was found.

Tracked-tree checks reject known private development defaults, common credential forms, unsafe links, mutable action references, release trust-root mistakes, and prohibited platform behavior. `scripts/check_public_history.py` applies the credential/privacy/path and DCO checks to every blob/commit reachable from the candidate `HEAD`. The current private ancestry intentionally fails that audit because it contains legacy personal defaults; it must remain private.

No real diagnostic log, configuration, private key, Trust Record, migration backup, Pending Cleanup file, or runtime state is tracked in the reviewed tree. The only production signer material is the reviewed public trust root. Test hostnames, addresses, users, paths, and key bodies are documentation ranges or invented placeholders.

### Supply chain and repository controls

The tracked CI workflow grants only `contents: read`, does not use `pull_request_target`, receives no repository secrets, and pins every external Action to a full commit. At this pre-public checkpoint, the canonical repository has one administrator, no deploy keys, webhooks, or release environments, default read-only workflow token permissions, and workflow PR approval disabled. Allowed Actions are restricted to `actions/checkout`, `actions/attest-build-provenance`, its full-SHA-pinned transitive `actions/attest` dependency, and `actions/upload-artifact`; repository-wide full-SHA pinning is required.

The canonical GitHub repository is still private at this checkpoint. On the current account tier, GitHub reports branch rules/protection and private vulnerability reporting as unavailable while private. GitHub also refused attestation persistence after the manual workflow built and validated every subject, reporting that attestations are unavailable for a user-owned private repository; no attestation or retained workflow artifact was created by that failed attempt. These controls and the attestation workflow must be enabled or repeated and re-queried immediately after an approved visibility change. Pre-public private status is not a claim of completed public governance or provenance.

## Local evidence

Before requesting cutover approval, record a fresh run from both the reviewed private commit and exported clean-root candidate:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/check_repository.py
python3 -m compileall -q -f bin src receiver tests scripts
python3 -m json.tool manifest.json >/dev/null
bash -n bin/cliamp-stream receiver/linux/setup-v1.sh
sh -n receiver/macos/setup-v1.sh receiver/macos/ssh-mixer-receiver-v1
shellcheck bin/cliamp-stream receiver/linux/setup-v1.sh
shellcheck -s sh receiver/macos/setup-v1.sh receiver/macos/ssh-mixer-receiver-v1
omarchy plugin validate .
git diff --check
python3 scripts/check_public_history.py --initial-release
```

The last command is expected to fail in the private development repository and must pass only in the exported public candidate. QML, Windows PowerShell, and hosted macOS jobs require the separately approved push; local Omarchy manifest validation is not a substitute.

### Real-device evidence

Evidence recorded on 2026-08-27 without stopping or replacing the pre-existing active Session:

- **Linux:** Companion and Receiver `1.1.0` / Protocol v1 were installed under a dedicated forced-command Managed Identity. The runtime was non-root; arbitrary commands, PTY use, and a real remote-forwarding request were rejected. A native-authenticated update was committed, then an exact rollback was deliberately exercised and verified. Rollback material, staging, and the temporary bootstrap credential were removed after verification.
- **Windows:** an approved Windows test system was exercised through a dedicated non-administrator Receiver account. Existing machine-scoped OpenSSH and FFplay availability were verified without requesting routine setup elevation. Companion and Receiver `1.1.0` / Protocol v1 were committed. The forced-command entry, arbitrary-command rejection, actual forwarding rejection, and `runtimeElevated: false` capability were verified through the Managed Identity. Failure-path rollback restored bootstrap authentication during development findings. A later deliberate rollback restored exact Receiver bytes, `authorized_keys` bytes, and ACL descriptor, retained working Managed Identity restrictions, and removed all transaction and staging directories. The smoke harness detected that an earlier one-off temporary-key cleanup had inherited a broader ACL; setup failed closed before applying changes, the ACL was repaired to the Receiver account plus `SYSTEM`, and the deliberate test was repeated successfully. Final in-place bootstrap removal preserved the repaired ACL, and the temporary local identity was deleted. This cleanup finding was in test orchestration, not shipped plugin code. The active Session identifier, process, and streaming state remained unchanged.
- **macOS:** no real-device evidence exists. Support remains explicitly Experimental with `realDeviceVerified: false`; no Gatekeeper or quarantine bypass was attempted or claimed.

The Windows server emitted OpenSSH's warning that its negotiated connection did not use a post-quantum key exchange. The client did not hide or override that warning; this environmental limitation does not change the classical SSH host-key and encryption checks recorded above.

### Hosted evidence

The new canonical repository was created privately and received only the audited one-root candidate. Its hosted Linux/QML/security, Windows PowerShell/probe/adapter, and Experimental macOS probe/rejection/adapter jobs passed together after the initial hosted run exposed two portability-only findings: the Windows adapter test module imported the Linux-only application runtime, and the hosted ShellCheck version elevated several macOS conditional-style diagnostics. The Windows-only application test is now skipped on Windows while adapter coverage remains, the shell conditions are explicit `if` statements, and all official Actions are pinned to reviewed full release commits. macOS hosted success does not change `realDeviceVerified: false`.

## Blockers requiring evidence or maintainer approval

- [x] Pin and commit the final reviewed private tree.
- [x] Export it into a local one-commit DCO-signed-off public candidate; compare trees and pass the complete clean-checkout suite plus `check_public_history.py --initial-release`.
- [x] Complete available Linux and Windows real-device smoke procedures without interrupting an active Session. Record unavailable coverage honestly. macOS remains Experimental and has no real-device claim.
- [x] Approve a production offline OpenSSH release-signing identity and commit only its reviewed namespace-restricted public `allowed_signers` line.
- [x] Implement and locally review the native-authenticated production update transaction and non-publishing attestation workflow; Managed Identity authority remains unchanged.
- [x] Run hosted and available real-device install, commit, rollback, privilege-disclosure, and cleanup evidence for the supported-platform transactions; keep macOS explicitly Experimental without a real-device claim.
- [x] Prepare deterministic artifacts twice, compare bytes, calculate size/SHA-256, sign metadata offline, and verify signatures independently.
- [x] Approve creation of signed `v0.1.0` and `receiver-v1.1.0` tags at the exact clean root. Run the history audit with each approved release tag.
- [x] Approve the private repository cutover. Before it, create a protected local bundle/checksum of every private ref. Do not place the bundle in the public tree.
- [x] Run required hosted Linux, Windows, and macOS CI in the new private repository and resolve every failure.
- [x] Before public announcement, restrict allowed Actions, require full-SHA pinning where GitHub supports it, configure maintainer-only branch protection and required CI, enable private vulnerability reporting, and verify manual release approval. Re-query settings after every visibility change.
- [x] Generate and independently verify GitHub provenance attestations for every published artifact and metadata file.
- [x] Obtain separate maintainer confirmations for repository cutover, visibility, tag signatures, tag pushes, immutable draft staging, and Receiver release publication. Announcement remains a separate decision.

Every applicable publication box is complete. Issue #18 may close after the post-publication documentation correction is merged and its final evidence is recorded; no announcement is implied by closure.

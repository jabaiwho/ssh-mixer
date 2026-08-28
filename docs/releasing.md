# Release process

Only the maintainer may approve, sign, publish, replace, yank, or announce an SSH-mixer release. Running a build command does not authorize publication. Never use a contributor or automation key as an improvised production trust root.

## Current release status

Plugin `v0.1.0` and Receiver `v1.1.0` were published from source commit `917f812bf2c5b4a63de6b5c59f43b904600858d9`. The Receiver release is signed, attested, immutable, and available at <https://github.com/jabaiwho/ssh-mixer/releases/tag/receiver-v1.1.0>. The production signer remains pinned in `release/allowed_signers`; hosted CI, Linux and Windows real-device transaction evidence, strict provenance verification, rollback, and post-publication byte checks are recorded in the completed [initial public-release readiness record](release-readiness.md). macOS remains Experimental with `realDeviceVerified: false`.

The gates below remain mandatory for every later release. A source checkout is not a signed Receiver artifact, automation cannot approve publication, and an existing immutable tag or asset is never replaced.

## Release units and compatibility

SSH-mixer versions these independently:

- Omarchy plugin (`manifest.json` and `PLUGIN_VERSION`);
- Linux, Windows, and Experimental macOS Companion Setup;
- each platform's Receiver helper; and
- Receiver Protocol minimum/maximum compatibility.

A helper-only release currently packages one common semantic artifact version across six files, while each installed artifact reports its own component version. A protocol-compatible installed helper need not update merely because the plugin changed. A protocol incompatibility must fail with guidance; it must not silently replace a Receiver.

Protocol changes require an explicit compatibility decision, parser/rejection tests on all platforms, update metadata bounds, setup verification changes, migration/removal review, and user documentation. Keep macOS `experimental: true` and `realDeviceVerified: false` unless a separate reviewed product decision has accepted sanitized real-device evidence.

## Clean public-history candidate

The initial public repository must not inherit private development ancestry. Export only the reviewed tree into a new local repository, create one DCO-signed-off root commit, and audit every blob reachable from that root. Do not force-push or replace the private repository.

One safe local preparation shape is:

```bash
candidate=$(mktemp -d)
git archive REVIEWED_PRIVATE_COMMIT | tar -x -C "$candidate"
git -C "$candidate" init --initial-branch=main
git -C "$candidate" add --all
git -C "$candidate" commit -s -m "feat: publish SSH-mixer v1"
python3 "$candidate/scripts/check_repository.py"
python3 "$candidate/scripts/check_public_history.py" \
  --repository "$candidate" --initial-release
```

Run all tests from that candidate, compare its complete tree byte-for-byte with the reviewed source tree, and record both tree IDs privately. `scripts/check_public_history.py` checks all history reachable from `HEAD` for DCO sign-off, credential patterns, private development defaults, and sensitive runtime/artifact paths. At the release gate, `--tag TAG` additionally requires a valid cryptographic tag signature at `HEAD`.

Keep a protected local `git bundle --all` plus checksum as a recovery copy before any repository cutover. The bundle contains private development history: store it outside the public tree with owner-only access. Renaming the existing GitHub repository to a private archive, creating the new canonical repository, pushing, changing visibility, enabling settings, and signing/publishing remain final-confirmation operations.

## Release gates

Before building:

1. Confirm the release issue and exact scope have maintainer approval.
2. Confirm the release commit is on reviewed protected history with required CI passing.
3. Run `python3 scripts/check_public_history.py --initial-release` and confirm the one clean-root commit has valid DCO sign-off.
4. Run all local validation from [CONTRIBUTING.md](../CONTRIBUTING.md) in the clean public-history candidate.
5. Review hosted Linux, Windows, and macOS jobs. Hosted macOS remains automated evidence, not real-device verification.
6. Complete applicable real-device procedures in [docs/testing/smoke-tests.md](testing/smoke-tests.md). Linux and Windows behavior or artifacts require their available real-device flow. Record unavailable coverage explicitly.
7. Review the permission inventory, security policy, user guide, migration, update, rollback, and removal behavior for the release.
8. Confirm GitHub private vulnerability reporting is enabled and the `SECURITY.md` private-report link works without exposing a draft publicly.
9. Confirm no secrets, private keys, tokens, machine identifiers, unredacted logs, personal defaults, or generated trust roots are tracked.
10. Confirm component versions are synchronized with the bytes being packaged and update release notes for every platform/kind.
11. Confirm the exact production install/update transaction adapter can verify the new version and can report rollback and staging-cleanup status. If it cannot, do not enable runtime installation for that artifact.

## Production signing identity

The first production signing identity requires a separate maintainer-reviewed decision. Generate a dedicated encrypted offline Ed25519 key on a controlled machine. Keep the private key offline and outside this repository and plugin installation. Commit only an approved public line with this exact principal, namespace restriction, and format:

```text
ssh-mixer-release namespaces="ssh-mixer-release" ssh-ed25519 PUBLIC_KEY_BODY
```

On the maintainer's Omarchy workstation, launch **SSH-mixer Release Signing Setup** from the application launcher. The durable source wizard is `scripts/setup_release_signing.sh`; it delegates passphrase entry directly to `ssh-keygen`, remembers only the non-secret key path in owner-only local state, requires review of the fingerprint, and writes only the public trust root. It never configures Git globally, signs release assets, creates tags, pushes, or publishes.

The reviewed file is `release/allowed_signers`. Its approved public fingerprint is `SHA256:EKQn+VLM6BR1gMybF35yITfzfYWNmB8N0FB2rDuqZV0`. `release/allowed_signers.example` is documentation only and is never runtime trust. Rotating or removing a signer is a security-sensitive release requiring explicit rationale and review. Do not accept a metadata signature from an uncommitted, downloaded, or release-provided replacement trust root.

## Build immutable artifacts and metadata

Start from a clean checkout at the exact full source commit. Do not build from a moving branch reference or a dirty tree.

Prepare a reviewed JSON object with at least one non-empty change string for each key:

```json
{
  "linux/companion": ["Reviewed change"],
  "linux/receiver": ["Reviewed change"],
  "windows/companion": ["Reviewed change"],
  "windows/receiver": ["Reviewed change"],
  "macos/companion": ["Experimental: reviewed change"],
  "macos/receiver": ["Experimental: reviewed change"]
}
```

Build deterministic copies and compact unsigned metadata:

```bash
commit=$(git rev-parse HEAD)
python3 scripts/build_release_metadata.py \
  --version X.Y.Z \
  --commit "$commit" \
  --published-at YYYY-MM-DDTHH:MM:SSZ \
  --changes /secure/reviewed-changes.json \
  --output /secure/release-metadata.json \
  --artifact-dir /secure/artifacts
```

The builder emits exactly these versioned categories:

- `ssh-mixer-linux-companion-vX.Y.Z.sh`
- `ssh-mixer-linux-receiver-vX.Y.Z.py`
- `ssh-mixer-windows-companion-vX.Y.Z.ps1`
- `ssh-mixer-windows-receiver-vX.Y.Z.ps1`
- `ssh-mixer-macos-companion-vX.Y.Z.sh`
- `ssh-mixer-macos-receiver-vX.Y.Z.sh`

Metadata binds each artifact to platform, kind, version, full source commit, byte size, SHA-256 digest, protocol minimum/maximum, reviewed changes, and an immutable repository-scoped URL under tag `receiver-vX.Y.Z`. Review the exact bytes, filenames, URLs, sizes, checksums, compatibility bounds, change text, plugin version, release ID, and UTC timestamp.

Rebuild from a second clean checkout of the same commit with identical arguments and compare every artifact and metadata byte (for example, with `sha256sum`). Investigate any difference before signing. The builder copies six tracked source artifacts and emits canonical compact JSON; reproducibility still depends on the exact reviewed inputs, including timestamp and change JSON.

Create and locally verify a cryptographically signed annotated tag matching the metadata URL (`receiver-vX.Y.Z`) with the maintainer's approved Git signing identity:

```bash
git tag -s receiver-vX.Y.Z -m "SSH-mixer Receiver X.Y.Z" COMMIT
git verify-tag receiver-vX.Y.Z
python3 scripts/check_public_history.py --initial-release --tag receiver-vX.Y.Z
```

Tag signing and the offline OpenSSH metadata-signing identity are separate controls; neither substitutes for the other. Do not push the tag merely because it was created. Create the GitHub release/tag once only after final approval. Never overwrite an artifact, metadata file, signature, tag, or release asset at an existing URL. A correction gets a new semantic version and new immutable URLs. Enable GitHub's immutable-release protection when available.

## Sign and independently verify metadata

Sign the exact compact metadata bytes on the controlled signing machine:

```bash
ssh-keygen -Y sign \
  -f /secure/offline/ssh-mixer-release \
  -n ssh-mixer-release \
  /secure/release-metadata.json
```

Before publication, verify with the exact reviewed repository trust root:

```bash
ssh-keygen -Y verify \
  -f release/allowed_signers \
  -I ssh-mixer-release \
  -n ssh-mixer-release \
  -s /secure/release-metadata.json.sig \
  < /secure/release-metadata.json
```

Verify every size and SHA-256 independently against both metadata and release assets. The detached OpenSSH signature and checksums are runtime enforcement. Neither TLS nor a GitHub login replaces them.

## Build provenance attestations

Every published artifact and the signed metadata must also receive GitHub artifact/build-provenance attestations bound to the same protected repository, workflow, source commit, and digest. The attestation job must use a full-commit-pinned official attestation action, least-privilege `id-token: write` and `attestations: write` permissions, and no unreviewed third-party release action.

Before publication approval, verify each attestation from a separate clean environment, for example with the maintained GitHub CLI:

```bash
gh attestation verify /secure/artifacts/ARTIFACT \
  --repo jabaiwho/ssh-mixer
```

Record the verification result and workflow run in the private release checklist without machine identifiers or credentials. Attestations provide public build provenance; they do not replace the offline OpenSSH metadata signature, SHA-256/size checks, full source commit, or manual approval. `.github/workflows/attest-release.yml` is manual, pinned, and cannot publish a GitHub release; do not run it until its exact clean-root commit and timestamp are approved. **Publication remains blocked** until its subjects and attestations are independently verified.

## Installation, verification, and rollback review

For every platform/kind, verify the release transaction in a disposable real Receiver where available:

1. Capture the prior helper/component version without sensitive machine data.
2. Build the exact update plan; confirm planning makes no changes.
3. Approve only the displayed unchanged plan hash.
4. Confirm signature verification occurs before download and again at execution.
5. Confirm the download is bounded, privately staged, and checked for exact size and SHA-256 before execution.
6. Confirm the platform transaction installs only the approved artifact.
7. Verify platform, helper version, protocol compatibility, forced-key restrictions, non-elevated runtime where required, arbitrary-command rejection, and forwarding rejection.
8. Confirm private staging is absent after success.
9. Exercise a controlled post-install verification failure. Confirm the prior version is restored or rollback is explicitly reported incomplete.
10. Exercise a checksum/signature failure and confirm no installer executes and no rollback is falsely claimed.
11. Confirm an active Session defers installation without interruption.

Do not publish an installer-capable release if rollback, post-install verification, or transaction binding is hypothetical. Metadata may describe a compatible artifact, but runtime installation must remain fail-closed until the adapter is reviewed.

## Manual publication approval

After all evidence is assembled, the maintainer performs a final manual comparison of:

- approved issue/scope and release notes;
- protected source commit and signed tag;
- six artifact bytes and digests;
- compact metadata and detached signature;
- committed signer Trust Root;
- GitHub attestations;
- protocol matrix;
- CI and real-device evidence;
- rollback and cleanup results; and
- macOS Experimental wording.

Only then may the maintainer upload the exact artifacts, metadata, signature, and attestations to the immutable release and approve publication. Automation must not infer approval from a green build, tag creation, or presence of a signing key.

## Post-publication

From a clean environment:

1. download every immutable asset by its final URL;
2. recheck size, SHA-256, OpenSSH signature, source commit, and attestation;
3. create a no-change runtime update plan and compare its displayed changes and plan hash to the release;
4. run the supported smoke matrix again where release transport could differ from local artifacts; and
5. verify documentation links and security-reporting paths.

If any check fails, stop distribution, preserve evidence privately, and issue a new corrected version. Do not replace assets in place. Security defects follow [SECURITY.md](../SECURITY.md). Normal failures use reviewed Diagnostic Reports.

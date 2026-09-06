# Receiver release metadata

The authoritative release checklist, including immutable assets, protocol compatibility, rollback, manual approval, and GitHub build-provenance attestations, is [docs/releasing.md](../docs/releasing.md).

Receiver updates are disabled unless the installed plugin has an explicitly trusted OpenSSH `allowed_signers` file and a matching signed immutable release. Receiver 1.1.2 is [published as signed, attested, immutable assets](https://github.com/jabaiwho/ssh-mixer/releases/tag/receiver-v1.1.2) from source commit `346b4d6248c8ba4de07959e0d8345f720528c81b`; its release record identifies the metadata digest and attestation run. Windows native-authenticated transaction, rollback, and real-device audio evidence are recorded; Linux 1.1.2 real-device coverage is explicitly unavailable; macOS remains Experimental with `realDeviceVerified: false`. The plugin 0.1.2 source-only release keeps these Receiver assets and Protocol v1 unchanged. The committed trust root contains only the independently reviewed public signing key; the encrypted private key remains offline. The manual attestation workflow is pinned and non-publishing, and every future Receiver release still needs its own reviewed candidate, signatures, attestations, and publication approval.

For an approved release, the maintainer:

1. launches **SSH-mixer Release Signing Setup** (backed by `scripts/setup_release_signing.sh`), chooses a dedicated encrypted offline Ed25519 key, confirms its fingerprint, and commits only its namespace-restricted public key as `release/allowed_signers` in the reviewed release;
2. uses `scripts/build_release_metadata.py --artifact-dir …` to copy the six source artifacts to exact versioned release filenames without publishing them;
3. writes a reviewed change list for every platform/component;
4. generates deterministic metadata with a full source commit and immutable GitHub release URLs;
5. reviews the metadata and SHA-256 values;
6. signs the exact metadata bytes with `ssh-keygen -Y sign -n ssh-mixer-release`;
7. verifies the signature using the committed trust root;
8. generates and independently verifies GitHub provenance attestations bound to the same commit and digests through a separately reviewed, full-commit-pinned workflow;
9. completes the applicable Linux/Windows real-device smoke procedures while retaining the explicit Experimental macOS status; and
10. manually approves and uploads the metadata, detached signature, attestations, and exact checksummed artifacts without replacing them later.

Example signing and verification:

```bash
ssh-keygen -Y sign -f /secure/offline/ssh-mixer-release -n ssh-mixer-release release-metadata.json
ssh-keygen -Y verify \
  -f release/allowed_signers \
  -I ssh-mixer-release \
  -n ssh-mixer-release \
  -s release-metadata.json.sig \
  < release-metadata.json
```

`release/allowed_signers.example` documents the format but is not trusted by runtime code. Attestations do not replace the OpenSSH signature or SHA-256/size checks. The production trust root and transaction adapter are present, but every new Receiver release still requires separate maintainer approval; establishing trust or publishing a plugin source tag does not publish Receiver artifacts.

# Contributing to SSH-mixer

Thank you for helping improve SSH-mixer. This project handles microphone audio, SSH trust, private keys, remote setup, privilege changes, updates, and cleanup. Changes are reviewed conservatively.

Read these first:

- [Security policy](SECURITY.md)
- [Security model and permissions](docs/security-model.md)
- [Domain vocabulary](CONTEXT.md)
- [Secure public v1 specification](docs/specs/secure-public-v1.md)
- [Real-device smoke procedures](docs/testing/smoke-tests.md)

Report vulnerabilities privately as described in `SECURITY.md`; do not submit a public security proof of concept.

## Before opening a change

Use a GitHub issue for non-trivial behavior so intent and security consequences can be reviewed before implementation. Keep changes scoped to one issue. The maintainer retains sole authority to merge, publish, create trust roots, sign releases, or change platform support claims.

Use the domain terms Receiver, Connection, Trust Record, Managed Identity, Receiver Protocol, Companion Setup, Route Mode, Source Matcher, Mix Profile, Session, Quick Start, Diagnostic Report, Pending Cleanup, and Cleanup Abandonment consistently.

## Developer Certificate of Origin

Every commit must carry a Developer Certificate of Origin sign-off. By signing off, the contributor certifies the contribution under the [Developer Certificate of Origin 1.1](https://developercertificate.org/).

Create signed-off commits with:

```bash
git commit -s
```

The commit message must contain a real contributor identity line:

```text
Signed-off-by: Name <email@example.com>
```

Do not add someone else's sign-off, and do not rewrite a contributor's identity without permission. A cryptographic Git commit signature is welcome but does not replace DCO sign-off.

## Design and security expectations

- Explain every security-relevant decision before an operation and require explicit approval.
- Keep Receiver commands fixed and versioned; never introduce user-configurable shell text or dynamic evaluation.
- Preserve strict host-key checking and the separation between Tailscale peer verification and OpenSSH host trust.
- Never read, log, report, or add to process arguments a password, passphrase, token, or private-key content.
- Do not add silent fallback, trust, installation, privilege escalation, update, report, cleanup abandonment, Session start, or resume.
- Keep Capture stop-on-lock and persistent active indication fail-closed.
- Reject unsafe links and use protected atomic local writes for sensitive state.
- Keep macOS explicitly Experimental and `realDeviceVerified: false` unless a separate reviewed decision cites sanitized real-device evidence.
- Never bypass Gatekeeper, clear quarantine metadata, suppress Windows/macOS warnings, or add a download-and-execute package pipeline.
- Do not commit a production release key, fabricated trust root, private development default, machine identifier, credential, or real diagnostic log.

When a safe operation cannot complete, return a structured failure, preserve retry/rollback state where applicable, and report incomplete rollback or Pending Cleanup honestly.

## Tests and local checks

Behavior tests should exercise public application, CLI, Receiver Protocol, or artifact seams rather than private implementation details. Add hostile-input and failure-path coverage for security changes.

Run the same portable checks as CI:

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
```

Windows artifact changes must also pass the hosted PowerShell parser, no-change probe, and Windows adapter tests. macOS changes must pass the hosted POSIX parser, no-change probe, rejection check, and adapter tests. Hosted CI is not a substitute for real-device evidence.

## Platform-fix expectations

A platform-specific fix must include:

1. a regression test at the affected adapter or Receiver Protocol seam;
2. tests that the failure and rollback/cleanup status remain structured and redacted;
3. syntax checks for the platform artifact;
4. a review of shared protocol compatibility and behavior on the other platforms;
5. sanitized real-device smoke evidence when the change depends on hardware, native prompts, services, ACLs, package managers, or audio output; and
6. updated user, security, smoke-test, or release documentation when permissions or observable behavior changes.

Do not weaken another platform to make one platform pass. If a fix cannot be tested on a required real device, say so in the pull request and keep the affected claim conservative. A macOS fix does not remove the Experimental label by itself.

## Pull requests and maintainer review

A pull request should contain:

- the linked issue and user-visible behavior;
- security and privacy consequences;
- exact files, commands, network destinations, and privilege changes added or removed;
- tests run and their results;
- real-device matrix and sanitized evidence, including what was not tested;
- rollback, migration, update, and removal consequences where relevant; and
- documentation changes.

At least the maintainer's review and required CI are needed before merge. Reviewers may require narrower commits, additional hostile tests, a protocol/version change, or real-device evidence. Contributors and automation must not push, merge, publish, sign, install, or apply Receiver changes on the maintainer's behalf without explicit approval.

## Documentation and diagnostics

Documentation must distinguish implemented guarantees from plans, automated checks from real-device checks, and successful cleanup from abandonment. Examples must use placeholders and must not encourage users to bypass native security prompts.

Test diagnostics with invented values only. Public reports should be produced through local preview and edited before submission. Security reports follow the private process instead.

# Custom builds and forks

SSH-mixer is MIT-licensed source. The repository owner of a downstream copy may use, modify, merge, publish, distribute, sublicense, or sell that copy under the license terms. There is no runtime anti-tamper mechanism, feature lock, telemetry gate, or requirement that a custom build preserve upstream policy.

## Upstream defaults are not downstream restrictions

The official build deliberately uses fixed Receiver Protocol operations, receiver-only Managed Identities, explicit plans and approvals, bounded diagnostics, lifecycle controls, and signed immutable updates. Repository checks and contribution guidance protect those upstream guarantees. They are acceptance requirements for changes represented as official SSH-mixer behavior—not a claim of control over a user's own build.

A downstream owner may authorize a person or coding agent to change the Python backend, QML interface, Receiver helpers, Companion Setup, protocol, routing, lifecycle behavior, dependencies, release process, or tests. The owner decides which risks are acceptable in that build. An agent working on a custom build should follow the owner's explicit policy and identify consequences that affect secrets, remote authority, capture, persistence, privilege, cleanup, or publication.

The official project does not expose a single **disable safety** switch. Such a switch would weaken ordinary installations and make official behavior difficult to reason about. Custom behavior belongs in explicit source changes that the downstream owner can inspect, test, and take responsibility for.

## Trust identity for a fork

Official signatures and attestations describe only exact upstream bytes. A fork that publishes modified plugin or Receiver code should:

1. use its own repository-scoped immutable URLs;
2. generate and review its own offline metadata signer and Git tag signer;
3. replace `release/allowed_signers` with its own namespace-restricted public trust root;
4. change the hard-coded release repository scope and build metadata identity;
5. produce provenance from its own protected workflow and source commit; and
6. label the build as downstream rather than implying upstream endorsement.

Never copy, request, or reuse an upstream private signing key. Keeping the upstream public key in a modified build trusts official upstream Receiver metadata; it does not authenticate the fork's changed artifacts.

## Receiver authority

A custom build may add protocol operations, use a user-managed SSH identity, or deliberately permit broader receiver behavior. Those choices can grant shell, forwarding, file, process, microphone, persistence, or privilege authority that the official Managed Identity rejects. Make the expanded authority visible to users of that build and provide a removal path appropriate to it.

Changing source does not automatically update an already installed forced-command entry or Receiver helper. A downstream build must migrate and verify its own receiver state rather than assuming local edits changed remote permissions.

## Support boundary

Security reports about unmodified released upstream behavior belong in the private process described by [SECURITY.md](../SECURITY.md). Questions or defects caused by downstream policy or modified bytes belong to that downstream project unless they also reproduce on an exact supported upstream release.

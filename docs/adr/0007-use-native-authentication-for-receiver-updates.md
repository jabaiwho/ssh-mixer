# Use native authentication for Receiver updates

SSH-mixer will keep routine update initiation outside the Managed Identity's Receiver Protocol authority. After local signature, immutable URL, size, checksum, and exact-plan approval, the production update transaction uses native Bootstrap Authentication to run the verified Companion Setup, retain a protected Receiver transaction until post-update verification, then commit or roll back.

A loaded OpenSSH agent, hardware token, or existing native credential may make this seamless; SSH-mixer still never receives a password or passphrase. Routine helper replacement remains user-level when the platform's authorized-key location is user-owned. Windows administrator-account key ACLs may still require a disclosed native UAC approval. Any package, service, firewall, ACL, Remote Login, or other elevated change requires a newly disclosed plan and native platform approval.

We reject self-update through the Managed Identity in v1. That would let possession of the receiver-only key trigger persistent executable replacement, increasing replay, availability, signer-compromise, rollback, and validator-bypass consequences. The key remains limited to fixed playback, capability/diagnostic, quiet-test, and self-removal operations.

Production update installation remains fail-closed until the transaction retains rollback material across post-update verification on Linux, Windows, and Experimental macOS and a separately approved release trust root is present.

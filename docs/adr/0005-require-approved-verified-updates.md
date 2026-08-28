# Require approved and verified updates

SSH-mixer may check for compatible updates but never silently installs executable code. Every Companion Setup or receiver update identifies an immutable version, explains changes, requires approval, verifies release signatures and checksums, verifies operation after installation, and rolls back on failure.

Detached release metadata uses the OpenSSH `ssh-keygen -Y` signature format with the `ssh-mixer-release` namespace and a separately reviewed pinned `allowed_signers` trust root. The repository carries only an example until the maintainer explicitly approves a release-signing identity; update operations fail closed without that production trust root.

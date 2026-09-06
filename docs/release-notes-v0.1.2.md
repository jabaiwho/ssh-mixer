# SSH-mixer plugin 0.1.2

Plugin-only patch release for automatic Tailscale Connection rename handling. Release tracking: [#48](https://github.com/jabaiwho/ssh-mixer/issues/48). Implementation: [#46](https://github.com/jabaiwho/ssh-mixer/issues/46) and [#47](https://github.com/jabaiwho/ssh-mixer/pull/47).

## Changes

- Existing and newly created Tailscale Connections follow the same online device's current hostname/MagicDNS name using its stable peer ID. The current name must resolve to an address advertised for that peer.
- Selecting, testing, or saving a Connection refreshes its saved address and matching Connections/Mix Profiles. Connection IDs, Receiver nicknames, Managed Identities, approved SSH keys, and Pending Cleanup references retain their existing identity.
- Tailscale SSH trust is scoped to the stable Connection ID, including compatibility with existing approved Trust Records. Another device reusing an old hostname cannot inherit its accepted keys.
- Saving a new Connection no longer inherits the previous Receiver's identity metadata, and save responses return the refreshed settings.

Missing/replaced peers, offline peers, mismatched DNS addresses, and changed SSH host keys remain blocked. This release does not approve trust, start audio, or change Direct SSH/OpenSSH Profile policies automatically.

## Compatibility and verification

Companion Setup, all Receiver helpers, and the pinned immutable Receiver release remain **1.1.2**. Receiver Protocol remains **v1** (minimum/maximum 1). Compatible installed helpers do not require an update for this plugin fix. No Receiver assets, metadata signatures, attestations, or trust roots are replaced by this release.

Ten rename regressions cover existing/new Connections, independent repeated renames, hostname reuse, legacy stores, and peer/DNS/trust rejection. The implementation passed the full Python/QML checks and hosted Linux, Windows, and macOS CI. Live Windows preflight with Receiver 1.1.2 confirmed the renamed Connection still works without changing its Managed Identity or approved SSH trust; playback was left stopped. Linux Receiver real-device coverage is unavailable and is not claimed. macOS remains **Experimental** with `realDeviceVerified: false`.

This plugin is reviewed source at a signed `v0.1.2` Git tag, not a new Receiver binary package. The release issue and GitHub release record carry the final source commit, signature verification, and candidate CI evidence. Installation instructions change only after that tag is publicly available and verified.

# Secure public SSH-mixer v1

## Problem Statement

SSH-mixer currently works for one personal Omarchy-to-Windows setup, but it embeds personal connection defaults and relies on mutable local state, inherited SSH behavior, temporary audio identifiers, and unrestricted remote commands. It does not provide safe first-run setup, cross-platform receiver provisioning, durable privacy indicators, lifecycle controls, structured diagnostics, or a trustworthy public release process. An Omarchy user cannot yet install it and confidently understand, approve, verify, or reverse every security-relevant action.

## Solution

Publish a clean-history SSH-mixer v1 that gives Omarchy users an informed, plugin-operated path from discovery through streaming and removal. SSH-mixer recommends verified Tailscale Connections while also supporting Direct SSH Connections and user-owned OpenSSH Profile Connections. It manages SSH host trust, dedicated receiver-only identities, cross-platform Companion Setup, stable source selection, privacy-safe Session lifecycle, quiet receiver testing, redacted diagnostics, verified updates, rollback, and complete removal.

The first release includes Windows and Linux support plus an Experimental macOS adapter. Every platform receives the same structured diagnostics and GitHub reporting flow. Security-relevant changes are explained before approval, performed by SSH-mixer after approval, verified, and rolled back when verification fails.

## User Stories

1. As an Omarchy user, I want to install SSH-mixer without embedding another user's infrastructure details, so that setup begins from my own environment.
2. As an Omarchy user, I want first-run setup in the panel, so that I do not need to edit backend files.
3. As an Omarchy user, I want SSH-mixer to discover useful connection choices locally without connecting to them, so that discovery does not create network activity unexpectedly.
4. As a Tailscale user, I want current tailnet peers shown first and marked Recommended, so that the safest common path is obvious.
5. As a Tailscale user, I want the selected peer and resolved address verified on every connection, so that a hostname cannot silently leave my tailnet.
6. As a non-Tailscale user, I want to add a Direct SSH Connection explicitly, so that SSH-mixer is not tied to one network provider.
7. As an OpenSSH user, I want to select a concrete existing profile, so that aliases, ports, agents, certificates, and compatible provider behavior can be reused.
8. As an OpenSSH user, I want SSH-mixer to identify when a profile uses a proxy, so that I understand what will execute locally.
9. As an OpenSSH user, I want ProxyCommand use to require confirmation, so that selecting a profile cannot silently execute an unexpected local proxy.
10. As a security-conscious user, I want direct connections isolated from my OpenSSH configuration, so that unrelated SSH options cannot redirect or weaken them.
11. As a user entering SSH details, I want malformed usernames, hosts, ports, and control characters rejected, so that values cannot become SSH options.
12. As a new user, I want the receiver's SSH fingerprint presented for approval, so that host trust is an informed decision.
13. As a returning user, I want changed host keys blocked, so that replacement is never silently trusted.
14. As a returning user, I want old and new host fingerprints shown when trust changes, so that I can decide whether to cancel or replace trust.
15. As a user, I want SSH-mixer to maintain its Trust Records for me, so that I do not edit known-host files manually.
16. As a user, I want one dedicated identity per receiver by default, so that compromise of one receiver does not expose every connection.
17. As a user, I want a seamless unencrypted Managed Identity by default, so that deliberate background streaming does not pause for passphrases.
18. As a security-conscious user, I want an encrypted agent-backed Managed Identity option, so that I can trade convenience for stronger at-rest protection.
19. As a user with an existing SSH profile, I want its identity used only to bootstrap a Managed Identity when possible, so that normal streaming remains constrained.
20. As a provider user who cannot install keys, I want an explicit user-managed identity fallback, so that compatible managed environments remain usable.
21. As a user choosing that fallback, I want the weaker permission guarantee clearly displayed, so that I understand SSH-mixer cannot constrain that identity.
22. As a receiver owner, I want Managed Identities limited to the Receiver Protocol, so that they cannot open shells or execute arbitrary commands.
23. As a receiver owner, I want forwarding, PTY, agent forwarding, startup scripts, and arbitrary commands disabled, so that the audio key has minimal authority.
24. As a receiver owner, I want setup to fail when key restrictions cannot be verified, so that a partial setup is not presented as secure.
25. As a user, I want bootstrap authentication handled directly by OpenSSH or my hardware token, so that SSH-mixer never receives my password.
26. As a user without existing access, I want Companion Setup on the receiver, so that SSH, dependencies, pairing, and restrictions can be configured with approvals.
27. As a user, I want Companion Setup for Linux, macOS, and Windows, so that the receiver is not limited to one operating system.
28. As a macOS user, I want the adapter labelled Experimental, so that support claims reflect its current real-device test coverage.
29. As a receiver owner, I want SSH-mixer to detect the remote platform before changing it, so that it never guesses platform commands.
30. As a receiver owner, I want unknown platforms refused safely, so that unsupported setup cannot damage the receiver.
31. As a receiver owner, I want every planned system change shown before execution, so that privilege and package changes are understandable.
32. As a receiver owner, I want the appropriate trusted package manager used, so that dependencies retain platform signature verification.
33. As a receiver owner, I want SSH-mixer never to use download-and-pipe shell installers, so that dependency installation remains auditable.
34. As a receiver owner, I want native authentication prompts for privileged changes, so that administrator passwords are never captured or stored.
35. As a receiver owner, I want runtime playback to occur without administrator privileges, so that the receiver runs with least privilege.
36. As a receiver owner, I want direct root login rejected, so that an audio stream cannot run as root.
37. As an administrator-capable desktop user, I want a warning before using my account, so that I understand the account context while retaining audio-session access.
38. As a user, I want missing dependencies verified after installation, so that setup does not advance on an unusable receiver.
39. As a user, I want a gentle receiver test sound, so that testing does not unexpectedly blast headphones or speakers.
40. As a user, I want the test to begin near -40 dBFS with fades and short duration, so that the first test is deliberately quiet.
41. As a user, I want optional small increases with a hard -24 dBFS cap, so that I can hear the test without an unrestricted loudness control.
42. As a user, I want SSH-mixer never to change receiver system volume, so that setup does not alter my established listening level.
43. As a user, I want to confirm whether I heard the test, so that silent playback failures become structured diagnostics.
44. As a user, I want Playback Sources, Capture Sources, and Output Monitors clearly distinguished, so that I understand what may be transmitted.
45. As a user, I want temporary PipeWire and PulseAudio identifiers excluded from saved choices, so that identifier reuse cannot select another application.
46. As a user, I want stable Source Matchers used for saved playback preferences, so that a uniquely matching application can be restored safely.
47. As a user, I want ambiguous source matches left unselected, so that SSH-mixer fails closed rather than guessing.
48. As a user, I want microphones excluded from automatic reselection, so that remembered settings do not silently prepare sensitive capture.
49. As a user, I want recently used microphones offered without being selected, so that deliberate reuse remains convenient.
50. As a user, I want no audio source selected by a public-install default, so that installation cannot inherit a developer-specific application choice.
51. As a user, I want Mix Profiles for common routes, so that receiver, Route Mode, playback choices, privacy, and stream settings can be reused.
52. As a user, I want playback-only Mix Profiles available through Quick Start, so that an explicit menu selection can begin streaming immediately.
53. As a user, I want microphone-containing Mix Profiles to require confirmation, so that Quick Start cannot silently activate capture.
54. As a user, I want missing or ambiguous Quick Start sources to open the mixer instead of starting, so that errors remain visible and correctable.
55. As a user, I want merely opening SSH-mixer never to start a Session, so that viewing settings has no transmission side effect.
56. As a user, I want Sessions to start only through an explicit action, so that streaming is always intentional.
57. As a user, I want closing the panel to leave a deliberately started Session active, so that background playback is supported.
58. As a user, I want an unavoidable persistent indicator while a Session is active, so that streaming cannot become invisible.
59. As a user, I want microphone Sessions marked with a distinct recording indicator, so that sensitive capture is immediately recognizable.
60. As a user, I want the compact indicator to protect receiver-name privacy by default, so that my infrastructure is not permanently exposed on the bar.
61. As a user, I want an option to show the receiver label beside the indicator, so that I can choose greater at-a-glance detail.
62. As a user, I want the indicator to open Session controls, so that stopping is immediately accessible.
63. As a user, I want screen lock to stop all streaming by default, so that unattended capture and playback transmission end.
64. As a user, I want a clear setting to continue non-microphone audio while locked, so that deliberate playback use remains possible.
65. As a user, I want Capture Sources always stopped on lock, so that microphones cannot continue while I am away.
66. As a user, I want Capture Sources to remain stopped after unlock, so that sensitive transmission never resumes automatically.
67. As a user, I want suspend, logout, receiver disconnect, and unrecoverable network loss to stop and clean up, so that stale Sessions do not persist.
68. As a user, I want wake and network reconnection never to restart a Session, so that prior consent is not reused automatically.
69. As a user, I want moved audio restored and owned modules unloaded on stop, so that local audio returns to its prior state.
70. As a user, I want process cleanup to verify process identity and start time, so that stale PIDs cannot terminate unrelated programs.
71. As a user, I want resource cleanup to verify ownership, so that reused audio module identifiers cannot affect unrelated routes.
72. As a user, I want concurrent Session operations serialized, so that start, stop, lock, and restart cannot corrupt state.
73. As a user, I want protected configuration, trust, state, key, and log files, so that sensitive metadata is not broadly readable.
74. As a user, I want detached workers to receive configuration without command-line secrets or metadata, so that process listings do not expose it.
75. As a user, I want logs to contain no audio, credentials, infrastructure identifiers, or application/device names, so that diagnostics minimize retained information.
76. As a user, I want diagnostics retained for seven days or twenty Sessions at most, so that logs cannot grow without bound.
77. As a user, I want configurable shorter or longer retention choices, so that local privacy policy remains under my control.
78. As a user, I want verbose diagnostics explicitly enabled for one Session only, so that detailed collection cannot remain active unnoticed.
79. As a user, I want to clear diagnostics immediately from the UI, so that retained information is easy to remove.
80. As a user on any supported platform, I want failures tied to a structured operation stage, so that setup and runtime problems are actionable.
81. As a user on any supported platform, I want a locally generated Diagnostic Report, so that failures can be reported consistently.
82. As a user, I want reports automatically redacted before review, so that GitHub issues do not expose my machine or tailnet.
83. As a user, I want to see and edit the entire report before submission, so that nothing is uploaded without informed approval.
84. As a user, I want Report on GitHub to open a prefilled issue without storing a GitHub token, so that reporting is simple and scoped.
85. As a user, I want logs excluded from reports unless I opt in, so that detailed data is not uploaded by default.
86. As a contributor, I want a Contribute a fix action, so that platform fixes can become reviewed pull requests.
87. As a security reporter, I want vulnerability reports directed privately, so that exploitable details are not posted publicly.
88. As a user, I want no telemetry or automatic failure upload, so that normal use creates no undisclosed reporting channel.
89. As a user, I want update availability explained without silent execution, so that I retain control over executable changes.
90. As a user, I want immutable versioned Companion Setup and receiver artifacts, so that reviewed releases cannot change underneath me.
91. As a user, I want signatures and checksums verified before installation, so that corrupted or substituted artifacts are rejected.
92. As a user, I want compatible protocol versions negotiated, so that safe older helpers do not require needless updates.
93. As a user, I want update verification and rollback, so that a failed update does not leave the receiver unusable.
94. As a user, I want transparent first-release scripts, so that setup code is directly auditable even if an operating system displays a warning.
95. As a user, I want SSH-mixer never to bypass Windows or macOS security warnings, so that convenience does not defeat platform protections.
96. As an existing SSH-mixer user, I want legacy configuration detected, so that upgrading does not silently discard my working setup.
97. As an existing user, I want to import and secure, keep user-managed, or start fresh, so that migration remains my decision.
98. As an existing user, I want migration delayed while a Session is active, so that upgrading cannot interrupt audio unexpectedly.
99. As an existing user, I want protected backup and rollback during migration, so that a failed conversion does not destroy working configuration.
100. As a user removing a receiver, I want its key revoked and local identity deleted, so that removed access does not remain usable.
101. As a user uninstalling SSH-mixer, I want reachable receivers cleaned before plugin removal, so that remote helpers and keys are not orphaned.
102. As a user with an offline receiver, I want cleanup marked pending rather than falsely successful, so that revocation is not forgotten.
103. As a receiver owner, I want Companion Setup able to remove receiver state independently, so that cleanup remains possible if the source is unavailable.
104. As a public user, I want installation through Omarchy's review-first plugin workflow, so that unsandboxed code is not silently enabled.
105. As a public user, I want a permission and threat-model document, so that I can understand files, processes, network access, audio access, and limitations before enabling.
106. As a public user, I want signed release tags, checksums, attestations, and reproducible checks, so that release integrity is inspectable.
107. As a maintainer, I want all changes to pass CI and security scanning before merge, so that the public branch has enforced quality gates.
108. As a maintainer, I want sole merge and release authority initially, so that external contributions cannot publish or merge themselves.
109. As a maintainer, I want forked workflows denied repository secrets and publication permissions, so that malicious PRs cannot steal credentials.
110. As a maintainer, I want releases separately approved after merge, so that code review does not automatically publish executables.
111. As a maintainer, I want clean public history without personal connection metadata, so that publication does not reveal private development defaults.
112. As a maintainer, I want the prior repository retained as a private archive, so that development history is preserved without becoming public.

## Implementation Decisions

1. Use the domain terms Receiver, Connection, Trust Record, Managed Identity, Receiver Protocol, Route Mode, Source Matcher, Mix Profile, Session, Quick Start, Companion Setup, and Diagnostic Report consistently.
2. Make one application request/result interface the primary external seam for the panel, CLI, and integration tests. Requests cover inspection, onboarding, configuration, trust, setup planning/execution, Session lifecycle, diagnostics, updates, migration, and removal.
3. Keep the panel and CLI thin. Security policy and orchestration belong behind the application interface rather than being duplicated in QML or shell wrappers.
4. Represent Connections explicitly as Tailscale, OpenSSH profile, or Direct SSH. Choosing a non-Tailscale connection is the explicit Tailscale opt-out; there is no silent network fallback.
5. Tailscale Connections must resolve to the selected peer's advertised tailnet address on every connection. Discovery does not depend on hostname prefixes.
6. Direct SSH and Tailscale Connections use an isolated hardened OpenSSH configuration. OpenSSH Profile Connections deliberately use the user's profile after effective-configuration inspection.
7. OpenSSH invocations end option parsing before the destination, validate all destination fields, require host-key checking, disable unintended forwarding/local commands/TTY, and avoid inherited behavior unless the connection type requires it.
8. ProxyJump is supported for OpenSSH Profile Connections. ProxyCommand requires informed confirmation and reconfirmation when its effective configuration changes.
9. Trust Records use a protected SSH-mixer-specific host-trust store. First trust and key replacement are fully managed UI decisions; replacement is never automatic.
10. Managed Identities are dedicated per Receiver. The default is an unencrypted key protected by filesystem permissions and remote protocol restriction. Encrypted agent-backed Managed Identities are optional.
11. Existing profile identities are used for Bootstrap Authentication and conversion to a Managed Identity whenever possible. Runtime fallback to user-managed permissions requires explicit confirmation and persistent labelling.
12. The Receiver Protocol is fixed and versioned. It permits capability/version checks, audio playback, bounded quiet testing, limited structured diagnostics, verified updates, and removal. It does not permit arbitrary receiver commands.
13. Managed Identity enrollment must install and verify forced-command restrictions and disable shells, arbitrary commands, forwarding, PTYs, agents, X11, and user startup scripts. Failure to verify is fatal.
14. Receiver runtime never elevates privileges and direct root login is rejected. Administrator-capable desktop accounts require confirmation because the active user's account is generally necessary for audio-session access.
15. Bootstrap Authentication is performed by OpenSSH, agents, provider tooling, or hardware devices. Passwords are never accepted by application fields, environment variables, command-line arguments, state, or logs.
16. Companion Setup ships as auditable POSIX and Windows setup scripts in immutable signed/checksummed releases. It never bypasses OS security warnings.
17. Companion Setup detects the platform and capabilities before proposing changes. Windows, Linux, and macOS use separate adapters behind one setup interface; unknown platforms fail closed.
18. Dependency installation uses approved system package managers, shows package/source/elevation details, requires approval, and verifies results. Download-and-pipe installation is prohibited.
19. Receiver and Companion versions negotiate a separate Receiver Protocol version so compatible releases can remain installed.
20. Update checks may identify newer compatible immutable releases. Installation requires change review and approval, signature/checksum verification, post-update verification, and rollback.
21. Audio discovery distinguishes Playback Sources, Capture Sources, and Output Monitors. Persistent configuration stores Source Matchers rather than temporary numeric identifiers.
22. Source Matcher resolution succeeds only on a unique stable match. Ambiguity and mismatch leave the source unselected. Capture Sources are never automatically restored.
23. Mix Profiles store a Connection, Route Mode, Source Matchers, privacy policy, and quality settings. Playback-only profiles support explicit Quick Start; Capture Sources force confirmation.
24. No Mix Profile or Session starts on panel open, login, wake, network reconnection, or application discovery.
25. The Session lifecycle is serialized and fail-closed. Screen lock stops all by default; the only alternative continues non-microphone audio. Capture never resumes automatically. Suspend, logout, disconnect, and fatal network loss stop and clean up.
26. A persistent, non-disableable indicator represents every active Session. Capture is visually distinct. Receiver labels are hidden on the bar by default but may be enabled.
27. Resource tracking records ownership evidence, process start identity, and Session identity. Cleanup never signals or unloads a resource whose identity cannot be proven.
28. Protected application directories use least-permission modes; atomic writes reject unsafe links and preserve permissions. Detached workers receive protected input rather than serialized configuration in process arguments.
29. Logs are structured, bounded, redacted, and contain no audio. Default retention is seven days or twenty Sessions, whichever removes data first. Verbose mode expires after one Session.
30. Structured operation errors and Diagnostic Reports apply to the entire product on every platform, not only Experimental adapters.
31. Diagnostic Reports are generated and redacted locally, previewed in full, editable, and submitted through a prefilled browser GitHub issue. Logs are opt-in and no GitHub token is stored.
32. Security reports use GitHub private vulnerability reporting. No telemetry, automatic uploads, or background failure reporting are permitted.
33. Quiet testing uses a generated, faded, non-looping signal beginning near -40 dBFS. User-requested increases are bounded and never exceed -24 dBFS. Receiver system volume is never changed.
34. Legacy configuration migration is guided and transactional. Import-and-secure, user-managed retention, and fresh setup are explicit choices. Migration waits for inactive Sessions and retains rollback until verification.
35. Destination and plugin removal revoke remote keys and helpers before deleting local state. Offline cleanup remains pending until retried or explicitly abandoned. Companion Setup supports independent receiver cleanup.
36. The public repository begins with clean reviewed history. The former repository remains private under an archive name, while the public canonical repository retains the expected SSH-mixer URL.
37. Public releases use signed tags, checksums, attestations, manual publication approval, protected branches, required CI, minimal workflow permissions, immutable action references, private vulnerability reporting, and DCO sign-off for external contributions.
38. Only the maintainer initially holds merge and release authority. External pull requests cannot access secrets, merge themselves, or publish artifacts.
39. Installation documentation uses Omarchy's review-first plugin management and explicitly states that Omarchy plugins run unsandboxed as the desktop user.
40. The first public release supports Windows and Linux and includes an Experimental macOS adapter. The release is not ready until automated cross-platform tests, available real-device smoke tests, security review, migration validation, removal validation, and public-release checks pass.

## Testing Decisions

1. Test observable behavior through the application request/result interface rather than internal helper functions.
2. Retain a small number of pure route/source tests only where they express domain rules more clearly than application tests.
3. Use deterministic adapters for OpenSSH, Tailscale, PipeWire/PulseAudio, files, processes, package managers, screen-lock events, clocks, releases, and GitHub reporting.
4. Test each Receiver Protocol operation through the same protocol interface used by production and verify every unknown or malformed operation is rejected.
5. Test Companion Setup as plan, approval, execution, verification, and rollback outcomes for Windows, Linux, and macOS adapters.
6. Run platform CI for setup and receiver behavior without requiring real speakers. Use generated/sinked audio and assert bounded signal properties.
7. Maintain real-device smoke procedures for Linux and Windows covering SSH enrollment, audible playback, lock, disconnect, stop, removal, and rollback.
8. Run the same structured-reporting tests for Windows, Linux, and macOS. macOS being Experimental does not reduce diagnostic requirements.
9. Add hostile-input tests for leading-dash SSH values, whitespace/control characters, malformed profile aliases, ProxyCommand changes, and unexpected receiver operations.
10. Add host-trust tests for first approval, exact match, unknown host, changed key, replacement cancellation, replacement approval, and protected trust-file modes.
11. Add identity tests for dedicated generation, encryption choice, bootstrap isolation, forced-command verification, user-managed fallback labelling, revocation, and root rejection.
12. Add lifecycle tests for panel close, explicit stop, lock policies, Capture removal, suspend, logout, disconnect, wake, reconnection, and no automatic restart.
13. Add stale-state tests that reuse PIDs, process groups, module IDs, and source IDs and prove unrelated resources are never changed.
14. Add filesystem tests for directory/file modes, safe atomic replacement, link rejection, worker input protection, and cleanup.
15. Add source tests for unique stable matching, ambiguity, changed metadata, temporary identifier reuse, Capture non-restoration, and Quick Start blocking.
16. Add diagnostic tests that seed usernames, hosts, addresses, tailnet names, key paths, SSH options, application/device names, credentials, and control sequences and prove they are absent from reports and logs.
17. Add retention tests for time, Session count, per-log size, total size, immediate clear, and one-Session verbose expiry.
18. Add update tests for compatible and incompatible protocol versions, immutable artifact selection, checksum/signature failure, user cancellation, verification failure, and rollback.
19. Add migration tests for every legacy choice, active-Session deferral, protected backups, failed conversion, rollback, and successful retirement.
20. Add removal tests for reachable, offline, partially configured, shared helper, retry, abandonment confirmation, and companion-side cleanup.
21. Validate QML syntax, manifest schema, shell scripts, Python, release metadata, documentation links, secret patterns, and action pinning in CI.
22. Require focused tests throughout implementation and a final standards/spec/security review before any clean-history public release is prepared.

## Out of Scope

- Running the Omarchy plugin on non-Omarchy source systems in v1.
- Supporting proprietary SSH clients that cannot be expressed through OpenSSH.
- Guessing behavior for unknown receiver operating systems.
- Arbitrary remote receiver commands in the published plugin.
- Silent dependency installation, privilege escalation, identity trust, updates, reports, or cleanup abandonment.
- Collecting or changing Tailscale ACLs through tailnet administrator credentials.
- Guaranteeing acoustic sound-pressure levels; SSH-mixer only bounds the generated digital test signal and never changes system volume.
- Continuing microphone Capture while locked.
- Automatic streaming at login, wake, network recovery, application discovery, or panel open.
- Automatic telemetry or automatic GitHub issue submission.
- Native Authenticode signing or Apple notarization in the first release; transparent scripts and signed/checksummed release artifacts are used instead.
- Claiming verified macOS hardware compatibility before real-device testing is available.
- Automatically merging pull requests or publishing releases.

## Further Notes

Omarchy plugins execute unsandboxed code as the desktop user. Trust therefore depends on a small auditable interface, transparent declared permissions, protected release provenance, review-first installation, and visible behavior rather than a manifest sandbox.

The current private repository and installed Session remain development inputs only. Implementation must not alter or interrupt an active Session without explicit user action. Publication and repository renaming require a separate final confirmation after implementation and review.

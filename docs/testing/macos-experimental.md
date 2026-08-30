# Experimental macOS Receiver validation

This platform-specific checklist supplements the cross-platform evidence rules and future macOS flow in [smoke-tests.md](smoke-tests.md).

## Current status

No real-device macOS Receiver validation has been recorded. The adapter remains **Experimental** and must not be described as hardware-verified based on hosted CI alone.

## Automated coverage

The macOS CI job parses the POSIX artifacts, runs the no-change capability probe on a hosted macOS runner, and exercises the deterministic adapter tests. These checks cover architecture/path selection, protocol allowlisting, bounded quiet-test policy, setup planning, rollback outcomes, diagnostics, and persistent Experimental labelling.

## Real-device procedure

A maintainer recording a real-device result must include the macOS version, architecture, bundled OpenSSH version, Homebrew prefix, and whether the account is a standard or administrator-capable user. Do not include hostnames, usernames, addresses, public keys, or other machine identifiers.

1. Review the complete plan without applying it and confirm every Remote Login, Homebrew, helper, and key change is accurate.
2. Approve setup and verify the Managed Identity cannot open a shell, run an arbitrary command, forward a port or agent, request X11, allocate a PTY, or run user SSH startup commands.
3. Verify Receiver Protocol capability and diagnostic responses report `platform: macos`, `experimental: true`, `realDeviceVerified: false`, and non-root runtime.
4. Stream generated audio and confirm local stop restores all source-side resources.
5. Confirm the Receiver-test slider is silent, defaults to -32 dBFS, and accepts whole-dB choices from -40 through 0 dBFS. In safe listening conditions, explicitly play one selected level and confirm its fades, 0.5-second duration, and unchanged system volume. Treat 0 dBFS as full-scale and potentially loud.
6. Exercise a failed dependency install, helper install, key update, and verification. Confirm rollback or an explicit incomplete-rollback Diagnostic Report.
7. Run independent Companion Setup cleanup and confirm the managed key and helper are removed without changing unrelated `authorized_keys` entries or Homebrew formulas.
8. Preview the redacted Diagnostic Report and contribution link before sharing anything.

Recording a successful run does not remove the Experimental label by itself; that requires a separate reviewed product decision.

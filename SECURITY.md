# Security policy

## Supported versions

SSH-mixer publishes the Omarchy plugin as reviewed source and Receiver helpers as signed, attested, immutable release assets. Unless a release says otherwise, only the latest published plugin and Receiver versions receive security fixes. The protected default branch receives fixes before the next release but is not itself a stable release channel.

| Version | Supported |
| --- | --- |
| Plugin `0.1.0` | Yes |
| Receiver/Companion `1.1.0` | Yes |
| Protected default branch | Development |
| Older releases, commits, and personal forks | No |

Experimental macOS is covered by this vulnerability-reporting process, but remains `experimental: true` and `realDeviceVerified: false`; a compatibility failure is normally a bug, not automatically a vulnerability.

## Report a vulnerability privately

**Do not open a public issue, discussion, pull request, or Diagnostic Report for a suspected vulnerability.** Use GitHub's private vulnerability reporting flow:

<https://github.com/jabaiwho/ssh-mixer/security/advisories/new>

In the repository UI, this is **Security → Advisories → Report a vulnerability**. If GitHub does not offer the private form to your account, do not disclose details publicly; contact the repository owner through their GitHub profile and ask for a private reporting channel without including exploit details in the initial public contact.

Include only what is needed to reproduce and assess the problem:

- affected SSH-mixer commit or released version;
- source and Receiver platform/version;
- whether the Connection is Tailscale, Direct SSH, or OpenSSH Profile;
- affected security property and realistic impact;
- minimal reproduction steps or a proof of concept;
- whether the issue is already being exploited or publicly known; and
- suggested remediation, if available.

Do **not** include private keys, passwords, passphrases, GitHub tokens, complete `authorized_keys`, unredacted diagnostics, hostnames, usernames, IP addresses, Tailscale peer identities, or recorded audio. Replace machine-specific values with stable placeholders. If sensitive evidence is essential, first agree on a private transfer method with the maintainer.

The maintainer will acknowledge the report through the private advisory, assess impact and affected versions, coordinate a fix and disclosure, and credit reporters who request credit. No fixed response or release deadline is promised; the advisory is the source of status.

## Normal operational failures

Crashes, setup failures, unsupported hardware, incomplete rollback, and other normal bugs belong in the public issue tracker only after local review:

1. Use SSH-mixer's **Prepare report** action.
2. Decide whether to include bounded redacted events.
3. Read and edit the complete report.
4. Remove any machine or personal detail that remains.
5. Open the generated GitHub issue URL and review it again before submitting.

SSH-mixer never uploads a report automatically. Do not convert a suspected security issue into a normal public report merely because the report was redacted.

## Security scope

The public security model, permissions, trust assumptions, and limitations are documented in [docs/security-model.md](docs/security-model.md). In particular, Omarchy plugins are unsandboxed and run with the logged-in desktop user's authority. Reports that demonstrate a bypass of SSH-mixer's documented controls are in scope; the mere fact that an enabled unsandboxed plugin has same-user authority is a documented platform property.

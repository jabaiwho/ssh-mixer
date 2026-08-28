# Repository guidance

## Agent skills

### Issue tracker

Issues and specifications are tracked with GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the repository's five canonical triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read `CONTEXT.md` and relevant ADRs under `docs/adr/` before changing affected behavior. See `docs/agents/domain.md`.

### Custom builds

When an owner requests a fork, experimental backend, changed Receiver authority, or another owner-risk-policy build, read `docs/custom-builds.md`. Apply upstream acceptance requirements to upstream contributions; for a downstream build, implement the owner's explicit policy, explain affected authority, and keep downstream trust identity distinct from official signatures.

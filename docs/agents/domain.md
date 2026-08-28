# Domain documentation

SSH-mixer is a single-context repository.

## Before changing behavior

Read:

- `CONTEXT.md`, when present
- Relevant ADRs under `docs/adr/`

Proceed silently when those documents do not yet exist. Create or update them
when terminology, security boundaries, or durable architectural decisions are
resolved.

## Expected layout

```text
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   └── agents/
└── src/
```

## Vocabulary

Use terms defined in `CONTEXT.md` consistently in code, tests, issues, and
documentation. Surface conflicts with existing ADRs instead of silently
overriding them.

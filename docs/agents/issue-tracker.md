# Issue tracker: GitHub

Issues and specifications for this repository live in GitHub Issues. Use the
`gh` CLI for issue operations and infer the repository from the configured
GitHub remote.

## Conventions

- Create issues with `gh issue create`.
- Read the issue body, comments, and labels before acting.
- Use heredocs for multiline issue bodies and comments.
- Reference originating issues from pull requests.
- Do not treat external pull requests as feature requests automatically.
- Do not close or merge work without the maintainer's decision.
- Never expose repository or release secrets to forked pull-request workflows.

## Pull requests as a request surface

PRs as a request surface: no.

## Publishing

When an engineering skill says to publish a specification or ticket, create a
GitHub issue. Releases and merges remain separate maintainer-approved actions.

## Large work

For multi-session work, use one tracking issue as a map and link child issues
for research, prototypes, security work, implementation slices, documentation,
and release preparation. Record blockers explicitly.

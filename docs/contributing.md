# Contributing

## Workflow

1. Read `AGENT.md`.
2. Select relevant workflow skills from `./.skills`.
3. Read only the docs and source files relevant to the task.
4. Make a narrow change.
5. Run verification.
6. Commit atomically with an English conventional-style message.

## Skill Usage

Use:

- `spec-driven-development` for new features or unclear requirements.
- `planning-and-task-breakdown` for multi-step work.
- `incremental-implementation` for multi-file implementation.
- `test-driven-development` for matching/parser/dedupe/database behavior.
- `debugging-and-error-recovery` for failures.
- `security-and-hardening` for secrets, auth, browser state, or input handling.
- `git-workflow-and-versioning` for commits and pushes.
- `documentation-and-adrs` for docs and architectural decisions.

## Coding Principles

- Keep matching deterministic.
- Keep parser, matcher, dedupe, scraper, Telegram, config, and database concerns separate.
- Prefer small functions with tests over broad abstractions.
- Use parameterized SQL.
- Avoid blocking calls in async Telegram handlers.
- Do not add dependencies without a clear reason.

## Verification

For docs-only changes:

```bash
git diff --check
```

For Python code:

```bash
python -m compileall app scripts
python -m pytest
```

For Docker/runtime changes:

```bash
make check
make build
docker compose config
```

If a command cannot run because the relevant files do not exist yet, report that clearly.

## Commit Style

Use conventional-style messages:

```text
docs: add product spec
chore: add docker runtime
feat: add deterministic matcher
fix: handle missing browser state
test: cover dedupe normalization
```

Keep commits atomic. Do not mix docs, runtime config, and application behavior unless the user explicitly asks for one combined checkpoint.

## Review Checklist

- [ ] Scope matches the request.
- [ ] No secrets are staged.
- [ ] Runtime files are ignored.
- [ ] Tests or checks were run.
- [ ] README/AGENT/docs were updated if commands or architecture changed.
- [ ] Compliance boundaries are preserved.

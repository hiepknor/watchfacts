# Contributing

## Workflow

1. Read `AGENT.md`.
2. Read only the docs and source files relevant to the task.
3. Make a narrow change.
4. Run verification.
5. Commit atomically with an English conventional-style message.

## Work Style

Use the smallest workflow that fits:

- For unclear features, update or add a focused spec before implementation.
- For matcher/parser/dedupe/database behavior, prefer regression tests first.
- For security, auth, browser state, or input handling, review `docs/security-compliance.md` before editing.
- For architecture changes, update or add an ADR.
- For docs, keep README, specs, operations, and roadmap consistent with code.

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
make check
```

For Python code:

```bash
python -m compileall app scripts
python -m pytest
make check
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

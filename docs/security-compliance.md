# Security And Compliance

## Access Boundary

This project is only for authorized use with a valid WatchFacts account.

Do not implement behavior that:

- bypasses login
- bypasses captcha
- bypasses Cloudflare
- bypasses anti-bot systems
- stores WatchFacts passwords
- extracts or logs browser credentials

## Secret Handling

Never commit:

- `.env`
- Telegram bot tokens
- OpenAI API keys
- WatchFacts credentials
- cookies
- browser storage state
- `data/watchfacts_state.json`
- `data/bot.db`
- logs containing sensitive data

Use `.env.example` for placeholders only.

## Browser State

`data/watchfacts_state.json` is sensitive because it can contain authenticated browser state.

Rules:

- Store it only on the operator machine/server.
- Do not paste it into prompts, tickets, logs, or docs.
- Do not commit it.
- Rotate/recreate it if leaked.

## Logging

Logs may include:

- startup status
- config presence checks without values
- query lifecycle
- result counts
- error category and stack traces when safe

Logs must not include:

- tokens
- OpenAI API keys
- passwords
- cookies
- authorization headers
- full URLs if they contain sensitive query params
- full browser storage state

## Telegram Safety

- Treat every Telegram message as user input.
- Validate empty messages.
- Cap outbound photo captions and text messages before sending to Telegram.
- Avoid echoing untrusted HTML as Markdown without escaping.
- Avoid sending too many messages for one query.

## Runtime Interface Safety

- Treat Telegram messages, result-page requests, and diagnostic inputs as
  untrusted user input.
- Do not expose a public search or issue-review API from `watchfacts-web`.
- Keep search orchestration inside shared application/search modules; interfaces
  must not reimplement matching or extraction.
- Diagnostic payloads may include only bounded, redacted raw context. They must
  not return full HTML, `.env`, browser state, cookies, or unbounded listings.
- Use only runtime-generated `result_id`, `stable_listing_id`, or explicit rank
  for follow-up actions.
- Use `offset` / `next_offset` for pagination instead of hidden prompt state.

## Result Page Action Safety

Generated result pages are public token URLs while they are valid. Treat every
browser action POST as untrusted, even if the page was opened by the operator.

Rules:

- Require token validation, page TTL validation, `action_nonce` validation, and
  rate limiting before any result-page action side effect.
- Keep in-process rate-limit state bounded and expiring. Reject malformed tokens
  before allocating rate-limit state; do not key public pre-validation state by
  attacker-controlled token text.
- Browser code must never receive `.env` values, cookies, browser state, or
  database paths.
- Store only sanitized result-page payloads in action sidecars. Do not store raw
  WatchFacts HTML, full browser responses, cookies, CSRF tokens, or
  `data/watchfacts_state.json`.
- Result page action errors must be safe for public display and must not include
  stack traces or config details.
- Anyone with a live result page can use the embedded nonce. Keep result page TTL
  and action rate limits meaningful.
- Report issue actions may record displayed listing fields and optional operator
  notes only. They must not claim access to hidden WatchFacts data that was not
  present in the result page payload.

## Web Scraping Boundary

The scraper should act like an authenticated browser controlled by the operator.

Allowed:

- Load saved browser state.
- Read saved browser-state cookies into memory for HTTPX requests to the same WatchFacts host.
- Navigate to the configured WatchFacts page.
- Parse page HTML visible to the logged-in account.
- Submit the visible WatchFacts search form with the CSRF token returned to the authenticated session.

Not allowed:

- Circumvent authentication.
- Automate credential stuffing.
- Defeat bot protection.
- Extract hidden authentication material.
- Log, expose, or persist cookies, CSRF tokens, or browser storage state outside the operator-created session file.
- Include cookies, CSRF tokens, or response bodies in health/metrics payloads.

## OpenAI Data Boundary

OpenAI-assisted refinement may only receive the smallest safe data needed for a
specific suggestion:

- original user query
- deterministic shown listing text
- bounded raw listing snippet already available in issue/search context
- safe reason codes, gate results, prompt version, and model name

OpenAI must never receive:

- `.env`
- Telegram bot tokens
- `OPENAI_API_KEY`
- WatchFacts cookies
- browser storage state
- `data/watchfacts_state.json`
- WatchFacts passwords or credentials
- full page HTML unless a future ADR explicitly approves it
- deployment logs containing secrets

Model output is not authoritative. A suggestion can affect user-facing output
only after local schema, substring, query-match, separator-boundary, length, and
confidence gates pass.

## Dependency Changes

Ask before adding dependencies that:

- introduce external services
- add AI behavior beyond the OpenAI controlled refiner
- process secrets
- change browser automation strategy
- change data storage strategy

## Pre-Commit Secret Check

Before committing:

```bash
git diff --staged | grep -Ei 'password|secret|api_key|token|credential|cookie' || true
```

Review every match. Placeholder examples are acceptable; real secrets are not.

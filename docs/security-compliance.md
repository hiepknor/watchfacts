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

## Hermes And MCP Safety

- Treat Hermes prompts and Telegram messages routed through Hermes as untrusted user input.
- Expose only explicit WatchFacts tools in Hermes config.
- Keep MCP tool names and schemas stable so Hermes does not need prompt hacks to call them.
- Do not expose `.env`, browser state, raw cookies, OpenWA API keys, or Telegram tokens through MCP payloads.
- Do not let Hermes invent seller contact, source links, prices, product images, result ids, or OpenWA links.
- Use `result_id` from `search` for follow-up tools such as `create_chat_draft` and `report_issue`.
- Use `offset` / `next_offset` for pagination instead of hidden Telegram callback state.
- `watchfacts_prefill.json` may contain operating instructions, but must not contain secrets.

## OpenWA Safety

- Store `OPENWA_API_KEY` only in `.env` or deployment secret storage.
- Use the internal OpenWA API URL for server-to-server calls.
- Return only safe dashboard/draft links intended for operators.
- Do not create chat drafts without a prior WatchFacts `result_id` from the current search cache.
- Do not invent or normalize seller phone numbers outside the data returned by WatchFacts/runtime.

## Web Scraping Boundary

The scraper should act like an authenticated browser controlled by the operator.

Allowed:

- Load saved browser state.
- Navigate to the configured WatchFacts page.
- Parse page HTML visible to the logged-in account.

Not allowed:

- Circumvent authentication.
- Automate credential stuffing.
- Defeat bot protection.
- Extract hidden authentication material.

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

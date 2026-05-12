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

## Dependency Changes

Ask before adding dependencies that:

- introduce external services
- add LLM behavior
- process secrets
- change browser automation strategy
- change data storage strategy

## Pre-Commit Secret Check

Before committing:

```bash
git diff --staged | grep -Ei 'password|secret|api_key|token|credential|cookie' || true
```

Review every match. Placeholder examples are acceptable; real secrets are not.

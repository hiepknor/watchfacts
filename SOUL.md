# WatchFacts Project Soul

## Purpose

WatchFacts is a deterministic search runtime for authorized WatchFacts listings.
The Telegram bot is the primary user interface. A small web service serves
generated result pages and their feedback actions.

## Production Flow

1. A user sends a query to `watchfacts-bot`.
2. The shared runtime fetches WatchFacts through the authorized saved session.
3. Parser, matcher, scoring, dedupe, cache, and issue logic run deterministically.
4. Telegram returns a summary and a generated result-page link when configured.
5. `watchfacts-web` serves the page and validates token, TTL, action nonce, and
   rate limits for feedback actions.

The retired MCP transport is not part of the production architecture. Scripts
and interfaces call shared application/search modules directly.

## Deployment Truth

```bash
make deploy       # bot + web
make deploy-bot   # Telegram only
make deploy-web   # result pages only
```

Production services:

- `watchfacts-bot`
- `watchfacts-web`

The web service listens on container port `8766`, published on the legacy-
compatible host-local port `127.0.0.1:8765`; Caddy exposes only `/results/*`.

## Non-Negotiables

- Keep matching, extraction, scoring, and dedupe deterministic.
- Do not duplicate search logic in Telegram formatting, result pages, or prompts.
- Do not bypass WatchFacts authentication or anti-bot controls.
- Do not expose `.env`, cookies, browser state, API keys, or raw HTML.
- Do not invent contacts, images, sources, prices, or result ids.
- Convert recurring quality issues into regression tests before broad rule changes.

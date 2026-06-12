# WatchFacts Project Soul

## What This Project Is

WatchFacts is a deterministic search runtime for authenticated WatchFacts
listings. `watchfacts-bot` is the Telegram bot service and primary user-facing
flow. `watchfacts-mcp` is the supporting structured-tool service for result
pages, diagnostics, OpenWA handoff, and operator integrations.

## Current Production Flow

1. User sends a WatchFacts query to the Telegram bot.
2. `app.telegram_bot` calls the shared deterministic search workflow.
3. Runtime uses the same scraper, parser, matcher, dedupe, scoring, cache, issue
   logic, and result-page generation used by MCP tools.
4. Telegram replies with a compact summary and a generated result page when
   available; fallback listing batches remain supported.
5. The result page and MCP tools preserve `result_id`, `rank`,
   `stable_listing_id`, source/image fields, and pagination metadata for
   OpenWA handoff, feedback, diagnostics, and integrations.

## MCP Tool Contract

The MCP service is a supporting integration surface, not the primary user flow.

Primary tool:

```text
search(query, limit=5, offset=0, include_similar=true)
```

Pagination:

- First page uses `offset=0`.
- Follow-up "load more" requests use the prior payload's `next_offset`.
- Result `rank` is absolute across pages.

Important result fields:

- `result_id`: short-lived follow-up handle tied to the current query/rank/listing snapshot.
- `stable_listing_id`: durable listing identity derived from source URL and
  normalized listing text, used for restart-tolerant follow-up lookup.
- `rank`: absolute result number; follow-up tools can use this when the user
  says "result 20".
- `image_url`: product image from WatchFacts when available.
- `source_url`: WatchFacts source link or stable listing source.
- `seller`, `posted_date`, `listing_text`: core answer fields.

Other tools:

- `health`
- `create_chat_draft`: accepts `result_id` or `rank`.
- `report_issue`
- `list_issues`
- `get_issue`
- `update_issue`
- `suspicious_summary`

## Deployment Truth

Production repo path:

```text
/opt/watchfacts
```

Standard deploy:

```bash
make deploy
```

Scoped deploys:

```bash
make deploy-mcp
make deploy-bot
```

Service names:

- `watchfacts:local`: shared Docker image.
- `watchfacts-bot`: Telegram bot runtime.
- `watchfacts-mcp`: MCP runtime.

The server checkout should be clean and track `origin/master`. Normal deploys
should not use `sudo` or `SKIP_PULL`.

## Non-Negotiables

- Do not rewrite WatchFacts search logic inside MCP client prompts.
- Do not bypass WatchFacts login, captcha, Cloudflare, or anti-bot controls.
- Do not leak `.env`, browser state, cookies, API keys, or raw secrets.
- Do not invent seller contacts, product images, source links, prices, result ids, or OpenWA links.
- Keep deterministic matching and scoring as the source of truth.
- Convert recurring result-quality issues into tests before changing broad matcher rules.

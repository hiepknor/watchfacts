# WatchFacts Project Soul

## What This Project Is

WatchFacts is a deterministic search runtime for authenticated WatchFacts
listings. It is now primarily used by Hermes through MCP. The Telegram bot is
still present, but it is a legacy channel rather than the center of new business
workflows.

## Current Production Flow

1. User asks Hermes for a WatchFacts search.
2. Hermes calls `watchfacts-mcp` at `http://watchfacts-mcp:8765/mcp`.
3. MCP tool `search` calls `app.tool_runtime.watchfacts_search_payload`.
4. Runtime uses the same scraper, parser, matcher, dedupe, scoring, cache, and
   issue logic as the old Telegram bot.
5. Hermes replies in Vietnamese with ranked results, seller/date/source details,
   product image when available, and next-page guidance when `has_more=true`.
6. For seller handoff, Hermes calls `create_chat_draft(query, result_id)`.

## MCP Tool Contract

Primary tool:

```text
search(query, limit=5, offset=0, include_similar=true)
```

Pagination:

- First page uses `offset=0`.
- Follow-up "xem thêm" uses the prior payload's `next_offset`.
- Result `rank` is absolute across pages.

Important result fields:

- `result_id`: stable handle for follow-up tools.
- `rank`: absolute result number; follow-up tools can use this when the user
  says "kết quả 20".
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
/opt/watchfacts-bot
```

Standard deploy:

```bash
make deploy-hermes-mcp
```

The server checkout should be clean and track `origin/master`. Normal deploys
should not use `sudo` or `SKIP_PULL`.

Hermes config/prefill are outside this repository:

```text
/opt/hermes-agent/data/config.yaml
/opt/hermes-agent/data/watchfacts_prefill.json
```

## Non-Negotiables

- Do not rewrite WatchFacts search logic inside Hermes prompts.
- Do not bypass WatchFacts login, captcha, Cloudflare, or anti-bot controls.
- Do not leak `.env`, browser state, cookies, API keys, or raw secrets.
- Do not invent seller contacts, product images, source links, prices, result ids, or OpenWA links.
- Keep deterministic matching and scoring as the source of truth.
- Convert recurring result-quality issues into tests before changing broad matcher rules.

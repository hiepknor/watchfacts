# Upgrade Plan after Dedicated Subdomain (watchfacts.onio.cc)

## Quick Current-State Checklist

- The split is in place: private MCP plus public result page has been moved to a
  dedicated subdomain.
- Security route behavior is correct: only `/results/*` is public, `/mcp*` returns
  404.
- Frequency limiting currently runs at app layer (`app/mcp_server.py`), and the
  current Caddy build still lacks a rate-limit module.
- A basic smoke/deploy loop exists via `make deploy-hermes-mcp` with safe Caddy
  rollback.

## Upgrade Priorities (high to low)

1. Edge Security (must-have)

- Add a dedicated reverse-proxy health endpoint: `GET /results/health` must always
  return 200 with a light payload.
- Standardize logging: align Caddy + app logs by timezone and JSON schema; add
  `X-Request-ID` from Caddy to app if traceability is needed.
- Audit result-page CSP for all real static sources used, preventing repeated
  console errors.

2. Abuse Protection (high)

- Keep the current app-layer rate limit; externalize settings via env
  (`RESULT_PAGE_RATE_LIMIT_ENABLED`, `RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS`,
  `RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS`,
  `RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS`) so runtime tuning does not require code
  changes.
- Prefer enabling rate limiting at Cloudflare/WAF when managed outside Caddy.
- If Caddy-side rate limiting is required, build/deploy a Caddy with
  `http.handlers.rate_limit` and apply it on `@watchfacts_results`.

3. Runtime Stability (high)

- Optimize deploy loop: expose only required endpoints in `mcp_server`, check health
  and portability, and auto-alert when `watchfacts_state.json` is near expiry.
- Add periodic cleanup policy for `data/result_pages` instead of only on ad-hoc read/delete.
- Set container resource limits (CPU/memory) for `watchfacts-mcp` if missing.

4. Result Quality (medium)

- Continue hardening loop: benchmark hard cases, issue loop, and regression fixtures
  after every matcher change.
- Add KPI tracking by query class (alias, variant, description, brand) to detect
  drift after updates.
- Monitor result page `404/410` rates by IP to distinguish expired-state errors from
  parse failures.

5. Product Expansion (medium)

- Split subdomain operations docs into local/dev/staging/prod variants.
- Standardize incident playbooks for three high-impact failures:
  - app-side rate burst 429
  - Caddy reload failure
  - template/result page rendering failure
- Add a post-deploy checklist to quickly validate `/results` and `/mcp` after each
  proxy change.

## Suggested Rollout Plan

Phase 1 (1-2 days):
- Add `Caddy health route`, standardize CSP, and publish health runbook.

Phase 2 (3-5 days):
- Move rate-limit controls to env variables, add lightweight abuse metrics (count +
  retry-after).

Phase 3 (1-2 weeks):
- Add Caddy+app observability layers (alerts/logging), container resource limits,
  and result-page job cleanup.

Phase 4 (non-urgent):
- Upgrade to Caddy with rate-limit plugin, or move rate-limit policy to edge
  (Cloudflare).

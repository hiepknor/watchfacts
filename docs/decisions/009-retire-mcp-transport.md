# ADR-009: Retire MCP Transport

Status: accepted

Amended by ADR-010: OpenWA draft handoff was subsequently removed; only the
feedback action remains.

Date: 2026-10-03

## Context

Telegram is the only required search interface. The MCP service added an unused
network protocol and dependency, but also hosted generated result pages and
their OpenWA/feedback actions at that time. Removing the service directly would therefore
break the primary Telegram result-page flow.

## Decision

- Remove FastMCP, MCP tools, client scripts, dependency, Compose service, and
  deploy targets.
- Keep search behavior in shared Python application/search modules.
- Serve `/results/*` and `/healthz` from `watchfacts-web` using
  Starlette/Uvicorn on container port 8766, mapped to the legacy-compatible
  host-local port 8765.
- Run smoke, benchmark, and prewarm scripts directly against the shared runtime.
- Keep Telegram owner commands as the issue-review interface.
- Keep `/mcp` explicitly blocked at the public reverse proxy.

## Consequences

- Fewer dependencies and one less integration protocol.
- Result-page URLs and action sidecars remain compatible.
- Diagnostics no longer test a network serialization boundary; runtime contract
  tests continue to validate structured payloads directly.
- External MCP clients are intentionally unsupported after this decision.

## Rollback

Rollback the application and Caddy configuration to the prior release. Do not
run both old and new services on the same host port.

# ADR-010: Retire OpenWA Handoff

Status: accepted

Date: 2026-10-03

## Context

OpenWA draft creation was available from Telegram callbacks, generated result
pages, and the shared structured runtime. It required extra configuration,
credentials, Docker networking, browser controls, and maintenance that are no
longer part of the desired WatchFacts workflow.

## Decision

- Remove the OpenWA integration, application use case, configuration, and tests.
- Remove Telegram draft callbacks and result-page draft controls.
- Remove the `/results/{token}/actions/openwa-draft` route.
- Remove the OpenWA Compose override and deployment options.
- Keep deterministic search, generated result pages, and nonce-protected issue
  reporting unchanged.

## Consequences

- WatchFacts has no OpenWA runtime dependency or OpenWA secrets.
- Result-page actions are limited to quality feedback.
- Existing links to the retired draft endpoint return `404`.
- Result-page schema version 2 retires stored pages containing the old action;
  they return `410` and are removed on access.
- Historical OpenWA design documents remain as decision history and are
  superseded by this ADR.

## Rollback

Reintroducing a handoff requires a new decision and a fresh integration review;
do not restore old credentials or endpoints implicitly.

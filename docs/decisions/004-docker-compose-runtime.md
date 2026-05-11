# ADR-004: Use Docker Compose And Makefile For Runtime

## Status

Accepted

## Date

2026-05-12

## Context

The bot needs a repeatable runtime with Python dependencies, Playwright browser dependencies, persistent data, and simple operator commands. Operators may run it locally or on a small Linux server.

## Decision

Use:

- `Dockerfile` for Python 3.11, project dependencies, and Playwright Chromium.
- `docker-compose.yml` for service runtime, `.env`, `data/`, and `logs/`.
- `Makefile` for stable commands such as `make init`, `make build`, `make up`, and `make logs`.

## Alternatives Considered

### Host-Only Python Virtualenv

- Pros: Simple during development.
- Cons: Browser dependencies vary by host and server.
- Kept as a development option but not the primary deployment wrapper.

### Raw Docker Commands Only

- Pros: Fewer files.
- Cons: Less ergonomic and easier for operators to mistype.
- Rejected in favor of Compose plus Makefile.

### Process Manager Without Containers

- Pros: Lightweight on a server.
- Cons: Requires more host setup and manual dependency management.
- Deferred until there is a need.

## Consequences

- `make build` can verify the runtime image.
- `make up` expects `app/main.py` and valid `.env`.
- `data/` and `logs/` persist on the host.
- Image build is larger because Playwright Chromium and browser dependencies are included.

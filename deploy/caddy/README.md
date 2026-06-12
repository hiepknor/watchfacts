## Watchfacts Caddy Deploy (Subdomain)

Purpose:

- Expose only the MCP server `/results/*` route publicly (Result Page).
- Keep `/mcp` internal so trusted MCP clients can call it through the private
  host or Docker network.

Implementation:

1. Copy `Caddyfile.watchfacts-subdomain` into the Caddy host config:

  - `deploy/caddy/Caddyfile.watchfacts-subdomain`
  - or paste directly into `/etc/caddy/Caddyfile` if this is a shared config file.

2. Reload Caddy:

```bash
sudo bash deploy/caddy/reload-caddy-safe.sh /etc/caddy/Caddyfile
```

3. In `watchfacts` `.env`, set:

```env
RESULT_PAGE_PUBLIC_BASE_URL=https://watchfacts.onio.cc/results
```

4. Deploy as usual:

```bash
make deploy-mcp
```

Quick checks:

```bash
curl -I https://watchfacts.onio.cc/results/health-check
curl -I https://watchfacts.onio.cc/mcp
```

Expected:

- `/results/{token}` returns a valid HTML template.
- `/mcp` returns 404 (ensuring it is not public).

### Operations Notes

- Requests for `/results/*` are logged separately through Caddy `stdout` (visible
  via `journalctl -u caddy`) using logger match `@watchfacts_results`.

- Warning: the current Caddy build on the server does not include the
  `rate_limit` module by default.
  - Application-level rate limiting is currently applied in `app/mcp_server.py`:
    - max 60 request/60 seconds per IP
    - 120-second block when threshold is exceeded
  - A Caddy rate-limit plugin can be enabled later.

  Verify the current Caddy build:

  ```bash
  caddy list-modules | grep -E 'http.handlers.ratelimit|rate_limit'
  ```

  If the module is not present, keep the app-level limit in place.

- `reload-caddy-safe.sh` script:
  - Backup current config to `/etc/caddy/caddyfile-backups/` before reload
  - Validate config before reload
  - Automatically rollback to the backup file if reload fails

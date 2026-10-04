## WatchFacts Result-Page Proxy

The public subdomain exposes generated result pages only. The retired `/mcp`
path remains explicitly blocked.

1. Copy `Caddyfile.watchfacts-subdomain` into the Caddy configuration.
2. Set `RESULT_PAGE_PUBLIC_BASE_URL=https://watchfacts.onio.cc/results`.
3. Deploy `watchfacts-web`:

```bash
make deploy-web
```

4. Validate:

```bash
curl https://watchfacts.onio.cc/results/health
curl -i https://watchfacts.onio.cc/mcp
```

Expected results:

- `/results/health` returns backend health through Caddy.
- `/results/health` fails when `watchfacts-web` is unavailable.
- `/mcp` returns HTTP 404.
- `/results/{token}` is proxied to `127.0.0.1:8765`.

Application-level result-page throttling remains in `app/runtime/web_server.py`.
Use `reload-caddy-safe.sh` to back up, validate, reload, and automatically roll
back Caddy configuration when a reload fails.

## Watchfacts Caddy deploy (subdomain)

Mục đích:

- Expose chỉ route `/results/*` của MCP server cho public (Result Page).
- Giữ `/mcp` ở chế độ internal để Hermes gọi trực tiếp qua Docker network.

Áp dụng:

1. Copy `Caddyfile.watchfacts-subdomain` vào Caddy host config:

   - `deploy/caddy/Caddyfile.watchfacts-subdomain`
   - hoặc paste trực tiếp vào `/etc/caddy/Caddyfile` nếu đây là file cấu hình chung.

2. Reload Caddy:

```bash
sudo systemctl reload caddy
```

3. Trong `.env` của `watchfacts-bot` set:

```env
RESULT_PAGE_PUBLIC_BASE_URL=https://watchfacts.onio.cc/results
```

4. Deploy như thường lệ:

```bash
make deploy-hermes-mcp
```

Kiểm tra nhanh:

```bash
curl -I https://watchfacts.onio.cc/results/health-check
curl -I https://watchfacts.onio.cc/mcp
```

Kỳ vọng:

- `/results/{token}` trả về template HTML hợp lệ.
- `/mcp` trả về 404 (đảm bảo không public).

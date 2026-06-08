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
sudo bash deploy/caddy/reload-caddy-safe.sh /etc/caddy/Caddyfile
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

### Ghi chú vận hành

- Log request riêng cho `/results/*` được ghi tại:
  - `/var/log/caddy/watchfacts-results.log`

- Cảnh báo: bản Caddy hiện tại trong server chưa cài thêm `rate_limit` module mặc định.
  - Rate limit đang áp dụng trong app ở `app/mcp_server.py`:
    - tối đa 60 request/60 giây cho mỗi IP
    - block 120 giây khi vượt ngưỡng
  - Có thể nâng lên Caddy rate-limit plugin sau này.

- Script `reload-caddy-safe.sh`:
  - Backup cấu hình trước khi reload vào `/etc/caddy/caddyfile-backups/`
  - Validate cấu hình trước khi reload
  - Tự rollback về file backup nếu reload không thành công

# Upgrade Plan after Dedicated Subdomain (watchfacts.onio.cc)

## Nhận diện nhanh hiện trạng

- Cơ sở đã ổn: MCP/private + result page public đã tách qua subdomain.
- Route an toàn đã đúng: `/results/*` mới public, `/mcp*` trả 404.
- Giới hạn truy cập tần suất hiện đang chạy ở app layer (`app/mcp_server.py`), còn Caddy chưa có module rate-limit trong build hiện tại.
- Có cơ chế smoke/deploy cơ bản với `make deploy-hermes-mcp` và rollback Caddy an toàn.

## Mục tiêu nâng cấp (ưu tiên cao -> thấp)

1. Độ an toàn biên (must-have)

- Bổ sung health endpoint riêng cho reverse proxy: `GET /results/health` luôn 200 + payload nhẹ.
- Chuẩn hóa cấu hình logging: gom log Caddy + app theo cùng timezone/json schema; thêm `X-Request-ID` từ Caddy tới app nếu cần trace.
- Rà thêm CSP toàn trang kết quả cho các nguồn tĩnh thực tế đang dùng, tránh lỗi console lặp lại.

2. Bảo vệ và chống abuse (high)

- Giữ rate limit app layer hiện tại; tách cấu hình bằng env (`RESULT_PAGE_RATE_LIMIT_ENABLED`, `RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS`, `RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS`, `RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS`) để điều chỉnh runtime không sửa code.
- Ưu tiên bật rate-limit ở Cloudflare/WAF nếu được quản lý bên ngoài Caddy.
- Nếu cần rate-limit tại Caddy thật sự, build/deploy Caddy bản có `http.handlers.rate_limit` rồi đặt trên match `@watchfacts_results`.

3. Độ ổn định runtime (high)

- Tối ưu deploy loop: `mcp_server` chỉ expose cần thiết, kiểm tra health/portability, và cảnh báo tự động khi `watchfacts_state.json` hết hạn.
- Thiết lập policy quay vòng `data/result_pages` định kỳ thay vì chỉ khi gọi đọc/xóa lẻ.
- Kiểm soát tài nguyên container (CPU/memory limits) cho `watchfacts-mcp` nếu chưa có.

4. Chất lượng kết quả (medium)

- Tiếp tục harden loop: benchmark hard-case, issue loop, và regression fixtures sau mỗi lần chỉnh matcher.
- Bổ sung KPI theo query class (alias, variant, mô tả, thương hiệu) để track drift sau update.
- Theo dõi tỷ lệ 404/410 của result page theo IP để tách lỗi do hết hạn với lỗi parse.

5. Mở rộng sản phẩm (medium)

- Tách docs vận hành cho subdomain theo 3 môi trường (local/dev/staging/prod).
- Chuẩn hóa incident playbook cho 3 lỗi trọng yếu:
  - rate burst 429 tại app
  - Caddy reload fail
  - template/result page render lỗi
- Thêm checklist post-deploy kiểm tra nhanh `/results` và `/mcp` sau mỗi thay đổi proxy.

## Lộ trình triển khai gợi ý

Phase 1 (1-2 ngày):
- `Caddy health route`, chuẩn hóa CSP, tài liệu runbook health.

Phase 2 (3-5 ngày):
- Đưa tham số rate-limit lên env, bổ sung theo dõi abuse metrics nhẹ (count + retry-after).

Phase 3 (1-2 tuần):
- Nâng lớp giám sát log/alert (Caddy+app), resource limit + job cleanup result pages.

Phase 4 (không khẩn cấp):
- Cài Caddy có rate-limit plugin hoặc chuyển policy rate-limit vào edge (Cloudflare).

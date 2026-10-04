from pathlib import Path


def test_result_routes_use_legacy_compatible_host_port() -> None:
    caddyfile = Path("deploy/caddy/Caddyfile.watchfacts-subdomain").read_text()

    assert "reverse_proxy 127.0.0.1:8765" in caddyfile
    assert "reverse_proxy 127.0.0.1:8766" not in caddyfile


def test_public_result_health_is_proxied_to_web_backend() -> None:
    caddyfile = Path("deploy/caddy/Caddyfile.watchfacts-subdomain").read_text()
    health = caddyfile.split("handle /results/health {", 1)[1].split("}", 1)[0]

    assert "rewrite * /healthz" in health
    assert "reverse_proxy 127.0.0.1:8765" in health
    assert "respond" not in health

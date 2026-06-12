from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_search_settings


SAFE_RUNTIME_KEYS = (
    "search_cache_ttl_seconds",
    "search_max_concurrent_searches",
    "watchfacts_form_cache_ttl_seconds",
    "watchfacts_http_client_enabled",
    "watchfacts_http_connect_timeout_seconds",
    "watchfacts_http_pool_timeout_seconds",
    "watchfacts_http_keepalive_expiry_seconds",
    "watchfacts_http_read_timeout_seconds",
    "watchfacts_http_search_read_timeout_seconds",
    "watchfacts_http_warmup_on_health",
    "result_page_max_results",
    "hybrid_ai_mode",
)


def safe_runtime_config_payload() -> dict[str, object]:
    settings = load_search_settings()
    return {key: getattr(settings, key) for key in SAFE_RUNTIME_KEYS}


def render_text(payload: dict[str, object]) -> str:
    return "\n".join(f"{key}={payload[key]}" for key in SAFE_RUNTIME_KEYS)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print safe effective WatchFacts runtime config values."
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = safe_runtime_config_payload()
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv


DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_MODEL = "gemma-4-E2B-it-Q8_0.gguf"
DEFAULT_TIMEOUT_SECONDS = 30


def _read_timeout() -> int:
    raw_value = os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        timeout = int(raw_value)
    except ValueError as exc:
        raise SystemExit("LOCAL_LLM_TIMEOUT_SECONDS must be a positive integer") from exc
    if timeout <= 0:
        raise SystemExit("LOCAL_LLM_TIMEOUT_SECONDS must be a positive integer")
    return timeout


def main() -> int:
    load_dotenv()

    base_url = os.getenv("LOCAL_LLM_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    model = os.getenv("LOCAL_LLM_MODEL", DEFAULT_MODEL).strip()
    timeout = _read_timeout()

    if not base_url:
        raise SystemExit("LOCAL_LLM_BASE_URL must not be empty")
    if not model:
        raise SystemExit("LOCAL_LLM_MODEL must not be empty")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You return concise JSON only.",
            },
            {
                "role": "user",
                "content": (
                    "Extract the watch reference from this listing as JSON with keys "
                    'reference and confidence: "Patek Philippe 5712/1R 2026 full set HKD 2.05m"'
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 512,
    }
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"Local LLM smoke test failed: {exc}", file=sys.stderr)
        return 1

    message = data["choices"][0]["message"]
    content = message.get("content") or message.get("reasoning_content", "")
    print(content.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

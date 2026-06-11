from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from app.search_result import SearchResult, source_result_id, stable_listing_id


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_TEXT_CHARS = 4000
MAX_SHORT_TEXT_CHARS = 512
MAX_URL_CHARS = 2048
MAX_LISTING_PREVIEW_CHARS = {
    "comfortable": 220,
    "dense": 150,
}
SENSITIVE_TEXT_RE = re.compile(
    r"\b(?:cookie|authorization|bearer|api[_-]?key|token|password|secret)\b\s*[:=]\s*\S+",
    re.IGNORECASE,
)
SENSITIVE_PATH_RE = re.compile(
    r"(?:data/)?(?:\.env|watchfacts_state\.json)",
    re.IGNORECASE,
)
RESULT_PAGE_TEMPLATE_PLACEHOLDER = "__WATCHFACTS_RESULTS_PAYLOAD__"
RESULT_PAGE_CSS_PLACEHOLDER = "__WATCHFACTS_RESULTS_CSS__"
RESULT_PAGE_VENDOR_JS_PLACEHOLDER = "__WATCHFACTS_RESULTS_VENDOR_JS__"
RESULT_PAGE_JS_PLACEHOLDER = "__WATCHFACTS_RESULTS_JS__"
RESULT_PAGE_TEMPLATE_PATH = Path(__file__).with_name("templates") / "result_page.html"
RESULT_PAGE_STATIC_DIR = Path(__file__).with_name("static")
RESULT_PAGE_CSS_PATH = RESULT_PAGE_STATIC_DIR / "result_page.css"
RESULT_PAGE_VENDOR_JS_PATH = RESULT_PAGE_STATIC_DIR / "vendor" / "petite-vue.iife.js"
RESULT_PAGE_JS_PATH = RESULT_PAGE_STATIC_DIR / "result_page.js"
RESULT_PAGE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ResultPageConfig:
    public_base_url: str
    ttl_seconds: int
    max_results: int
    storage_dir: Path
    watchfacts_url: str

    @classmethod
    def from_settings(cls, settings) -> "ResultPageConfig":
        return cls(
            public_base_url=settings.result_page_public_base_url,
            ttl_seconds=settings.result_page_ttl_seconds,
            max_results=settings.result_page_max_results,
            storage_dir=settings.result_page_storage_dir,
            watchfacts_url=settings.watchfacts_url,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.public_base_url)


@dataclass(frozen=True)
class GeneratedResultPage:
    url: str
    expires_at: str
    result_count: int
    schema_version: int = RESULT_PAGE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "url": self.url,
            "expires_at": self.expires_at,
            "result_count": self.result_count,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class ResultPageRead:
    status_code: int
    html: str | None = None


@dataclass(frozen=True)
class ResultPageActionRead:
    status_code: int
    payload: dict[str, Any] | None = None
    action_nonce: str | None = None
    error: str | None = None


def generate_result_page(
    query: str,
    results: list[SearchResult],
    *,
    config: ResultPageConfig | None = None,
    settings=None,
    offset: int = 0,
    limit: int | None = None,
    total_count: int | None = None,
    next_offset: int | None = None,
    now: datetime | None = None,
) -> GeneratedResultPage | None:
    active_config = config or ResultPageConfig.from_settings(settings)
    if not active_config.enabled:
        return None

    created_at = _utc_now(now)
    expires_at = created_at.timestamp() + active_config.ttl_seconds
    token = _new_token(active_config.storage_dir)
    action_nonce = secrets.token_urlsafe(24)
    page_results = _result_payloads(query, results, active_config)
    payload = {
        "query": _clean_text(query, MAX_SHORT_TEXT_CHARS),
        "created_at": _format_timestamp(created_at),
        "expires_at": _format_timestamp(
            datetime.fromtimestamp(expires_at, tz=timezone.utc)
        ),
        "total_count": total_count if total_count is not None else len(results),
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "result_count": len(page_results),
        "result_page_schema_version": RESULT_PAGE_SCHEMA_VERSION,
        "actions": {
            "action_nonce": action_nonce,
            "openwa_draft_url": f"{active_config.public_base_url.rstrip('/')}/{token}/actions/openwa-draft",
            "report_url": f"{active_config.public_base_url.rstrip('/')}/{token}/actions/report",
        },
        "results": page_results,
    }

    cleanup_expired_result_pages(active_config, now=created_at)
    active_config.storage_dir.mkdir(parents=True, exist_ok=True)
    html = render_result_page_template(payload)
    page_path = _page_path(active_config, token)
    sidecar_path = _sidecar_path(active_config, token)
    page_path.write_text(html, encoding="utf-8")
    sidecar_path.write_text(
        json.dumps(
            {
                "action_nonce": action_nonce,
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    timestamp = created_at.timestamp()
    os.utime(page_path, (timestamp, timestamp))
    os.utime(sidecar_path, (timestamp, timestamp))
    return GeneratedResultPage(
        url=f"{active_config.public_base_url.rstrip('/')}/{token}",
        expires_at=payload["expires_at"],
        result_count=len(page_results),
    )


def render_result_page_template(payload: dict[str, Any] | None = None) -> str:
    payload_json = "null" if payload is None else _script_safe_json(payload)
    return _load_result_page_template().replace(
        RESULT_PAGE_TEMPLATE_PLACEHOLDER,
        payload_json,
    )


@lru_cache(maxsize=1)
def _load_result_page_template() -> str:
    template = RESULT_PAGE_TEMPLATE_PATH.read_text(encoding="utf-8")
    css = RESULT_PAGE_CSS_PATH.read_text(encoding="utf-8")
    vendor_js = RESULT_PAGE_VENDOR_JS_PATH.read_text(encoding="utf-8")
    js = RESULT_PAGE_JS_PATH.read_text(encoding="utf-8")
    if RESULT_PAGE_TEMPLATE_PLACEHOLDER not in js:
        raise RuntimeError("Result page script is missing the payload placeholder")
    template = template.replace(RESULT_PAGE_CSS_PLACEHOLDER, css)
    template = template.replace(RESULT_PAGE_VENDOR_JS_PLACEHOLDER, vendor_js)
    template = template.replace(RESULT_PAGE_JS_PLACEHOLDER, js)
    missing = [
        placeholder
        for placeholder in (
            RESULT_PAGE_CSS_PLACEHOLDER,
            RESULT_PAGE_VENDOR_JS_PLACEHOLDER,
            RESULT_PAGE_JS_PLACEHOLDER,
        )
        if placeholder in template
    ]
    if missing:
        raise RuntimeError(
            "Result page template has unresolved placeholders: "
            + ", ".join(missing)
        )
    if RESULT_PAGE_TEMPLATE_PLACEHOLDER not in template:
        raise RuntimeError("Result page template is missing the payload placeholder")
    return template


def read_result_page_html(
    token: str,
    *,
    config: ResultPageConfig | None = None,
    settings=None,
    now: datetime | None = None,
) -> ResultPageRead:
    active_config = config or ResultPageConfig.from_settings(settings)
    if not TOKEN_RE.fullmatch(token):
        return ResultPageRead(status_code=404)

    page_path = _page_path(active_config, token)
    if not page_path.exists() or not page_path.is_file():
        cleanup_expired_result_pages(active_config, now=now)
        return ResultPageRead(status_code=404)

    if _is_expired(page_path, active_config, now=_utc_now(now)):
        _unlink_page_files(active_config, token)
        cleanup_expired_result_pages(active_config, now=now)
        return ResultPageRead(status_code=410)

    cleanup_expired_result_pages(active_config, now=now)
    return ResultPageRead(
        status_code=200,
        html=page_path.read_text(encoding="utf-8"),
    )


def read_result_page_action_payload(
    token: str,
    *,
    config: ResultPageConfig | None = None,
    settings=None,
    now: datetime | None = None,
) -> ResultPageActionRead:
    active_config = config or ResultPageConfig.from_settings(settings)
    if not TOKEN_RE.fullmatch(token):
        return ResultPageActionRead(status_code=404, error="invalid_token")

    page_path = _page_path(active_config, token)
    sidecar_path = _sidecar_path(active_config, token)
    if not page_path.exists() or not page_path.is_file():
        cleanup_expired_result_pages(active_config, now=now)
        return ResultPageActionRead(status_code=404, error="not_found")
    if not sidecar_path.exists() or not sidecar_path.is_file():
        cleanup_expired_result_pages(active_config, now=now)
        return ResultPageActionRead(status_code=404, error="missing_sidecar")

    if _is_expired(page_path, active_config, now=_utc_now(now)):
        _unlink_page_files(active_config, token)
        cleanup_expired_result_pages(active_config, now=now)
        return ResultPageActionRead(status_code=410, error="expired")

    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ResultPageActionRead(status_code=404, error="invalid_sidecar")

    if not isinstance(sidecar, dict):
        return ResultPageActionRead(status_code=404, error="invalid_sidecar")
    action_nonce = sidecar.get("action_nonce")
    payload = sidecar.get("payload")
    if not isinstance(action_nonce, str) or not action_nonce:
        return ResultPageActionRead(status_code=404, error="invalid_sidecar")
    if not isinstance(payload, dict):
        return ResultPageActionRead(status_code=404, error="invalid_sidecar")

    cleanup_expired_result_pages(active_config, now=now)
    return ResultPageActionRead(
        status_code=200,
        payload=payload,
        action_nonce=action_nonce,
    )


def cleanup_expired_result_pages(
    config: ResultPageConfig,
    *,
    now: datetime | None = None,
) -> int:
    if not config.storage_dir.exists():
        return 0
    current = _utc_now(now)
    removed = 0
    for path in config.storage_dir.glob("*.html"):
        if path.is_file() and _is_expired(path, config, now=current):
            _unlink_page_files(config, path.stem)
            removed += 1
    for path in config.storage_dir.glob("*.json"):
        if not path.is_file():
            continue
        page_path = _page_path(config, path.stem)
        if not page_path.exists() or _is_expired(path, config, now=current):
            _unlink_quietly(path)
    return removed


def _result_payloads(
    query: str,
    results: list[SearchResult],
    config: ResultPageConfig,
) -> list[dict[str, Any]]:
    bounded_results = results[: config.max_results]
    return [
        _result_payload(
            query,
            rank,
            result,
            config=config,
        )
        for rank, result in enumerate(bounded_results, start=1)
    ]


def _result_payload(
    query: str,
    rank: int,
    result: SearchResult,
    *,
    config: ResultPageConfig,
) -> dict[str, Any]:
    result_id = source_result_id(query, rank, result)
    listing_text = _clean_text(result.listing_text, MAX_TEXT_CHARS)
    return {
        "rank": rank,
        "result_id": result_id,
        "stable_listing_id": stable_listing_id(result),
        "source_result_id": result_id,
        "listing_text": listing_text,
        "listing_text_preview": {
            density: _listing_preview_text(listing_text, density)
            for density in ("comfortable", "dense")
        },
        "seller": _clean_optional_text(result.seller),
        "posted_date": _clean_optional_text(result.posted_date),
        "image_url": _normalize_url(result.image_url, config.watchfacts_url),
        "source_url": _normalize_url(result.source_url, config.watchfacts_url),
        "seller_phone": _clean_optional_text(result.seller_phone, max_chars=64),
        "similar_results": [
            _similar_result_payload(similar, config=config)
            for similar in result.similar_results
        ],
    }


def _similar_result_payload(
    result: SearchResult,
    *,
    config: ResultPageConfig,
) -> dict[str, Any]:
    listing_text = _clean_text(result.listing_text, MAX_TEXT_CHARS)
    return {
        "listing_text": listing_text,
        "listing_text_preview": {
            density: _listing_preview_text(listing_text, density)
            for density in ("comfortable", "dense")
        },
        "seller": _clean_optional_text(result.seller),
        "posted_date": _clean_optional_text(result.posted_date),
        "image_url": _normalize_url(result.image_url, config.watchfacts_url),
        "source_url": _normalize_url(result.source_url, config.watchfacts_url),
        "seller_phone": _clean_optional_text(result.seller_phone, max_chars=64),
    }


def _listing_preview_text(value: str, density: str) -> str:
    limit = MAX_LISTING_PREVIEW_CHARS.get(density, MAX_LISTING_PREVIEW_CHARS["comfortable"])
    return _clean_text(value, limit)


def _clean_optional_text(value: str | None, *, max_chars: int = MAX_SHORT_TEXT_CHARS) -> str | None:
    if value is None:
        return None
    cleaned = _clean_text(value, max_chars)
    return cleaned if cleaned else None


def _clean_text(value: str, max_chars: int) -> str:
    redacted = SENSITIVE_PATH_RE.sub("[redacted]", value)
    redacted = SENSITIVE_TEXT_RE.sub("[redacted]", redacted)
    normalized = " ".join(redacted.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "..."


def _normalize_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    raw_url = value.strip()
    if len(raw_url) > MAX_URL_CHARS:
        return None
    parsed = urlparse(raw_url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None
    absolute = raw_url if parsed.scheme else urljoin(base_url, raw_url)
    absolute_parsed = urlparse(absolute)
    if absolute_parsed.scheme not in {"http", "https"} or not absolute_parsed.netloc:
        return None
    return absolute[:MAX_URL_CHARS]


def _script_safe_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _new_token(storage_dir: Path) -> str:
    while True:
        token = secrets.token_urlsafe(24)
        if (
            not _page_path_for_dir(storage_dir, token).exists()
            and not _sidecar_path_for_dir(storage_dir, token).exists()
        ):
            return token


def _page_path(config: ResultPageConfig, token: str) -> Path:
    return _page_path_for_dir(config.storage_dir, token)


def _sidecar_path(config: ResultPageConfig, token: str) -> Path:
    return _sidecar_path_for_dir(config.storage_dir, token)


def _page_path_for_dir(storage_dir: Path, token: str) -> Path:
    return storage_dir / f"{token}.html"


def _sidecar_path_for_dir(storage_dir: Path, token: str) -> Path:
    return storage_dir / f"{token}.json"


def _is_expired(path: Path, config: ResultPageConfig, *, now: datetime) -> bool:
    return path.stat().st_mtime + config.ttl_seconds < now.timestamp()


def _unlink_page_files(config: ResultPageConfig, token: str) -> None:
    _unlink_quietly(_page_path(config, token))
    _unlink_quietly(_sidecar_path(config, token))


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

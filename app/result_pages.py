from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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

    def to_payload(self) -> dict[str, object]:
        return {
            "url": self.url,
            "expires_at": self.expires_at,
            "result_count": self.result_count,
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
    return _HTML_TEMPLATE.replace("__WATCHFACTS_RESULTS_PAYLOAD__", payload_json)


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


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WatchFacts Results</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7f6;
      --surface: #ffffff;
      --surface-raised: #fbfcfc;
      --surface-soft: #edf3f2;
      --text: #111817;
      --muted: #5d6865;
      --subtle: #7d8985;
      --border: #d6dfdc;
      --border-strong: #b5c3bf;
      --accent: #0f766e;
      --accent-strong: #0a4f49;
      --accent-soft: #e1f2f0;
      --listing-accent: #0f766e;
      --listing-strong: #0a4f49;
      --listing-soft: #e8f4f2;
      --listing-border: #b8d9d4;
      --title-box: minmax(0, 1fr);
      --meta-box: auto;
      --warning: #8a5a00;
      --danger: #b42318;
      --shadow: 0 1px 2px rgba(17, 24, 23, 0.06);
      --radius: 8px;
      --workspace-main: min(1080px, calc(100% - 2rem));
      --workspace-main-wide: min(1120px, calc(100% - 2rem));
      --workspace-main-tablet: min(920px, calc(100% - 1.5rem));
      --workspace-main-mobile: min(calc(100% - 1rem), 44rem);
      --result-media-desktop: clamp(8.5rem, 18vw, 11.5rem);
      --result-media-tablet: clamp(8rem, 20vw, 10rem);
      --result-media-mobile: clamp(6rem, 29vw, 7.5rem);
      --result-media-narrow: clamp(5.5rem, 28vw, 6.5rem);
      --result-card-min-height: 10.25rem;
      --result-card-mobile-height: 8.75rem;
      --dense-media: 5.25rem;
      --dense-media-mobile: 4.65rem;
      --dense-card-height: 7.25rem;
      --dense-card-mobile-height: 6.75rem;
      --modal-width: min(62rem, calc(100vw - 2rem));
      --modal-width-wide: min(66rem, calc(100vw - 2rem));
      --modal-width-tablet: min(46rem, calc(100vw - 1.5rem));
    }

    * { box-sizing: border-box; }

    html {
      min-width: 0;
      scrollbar-gutter: stable;
    }

    body {
      margin: 0;
      min-width: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }

    body.modal-open {
      overflow: hidden;
      padding-right: var(--modal-scrollbar-compensation, 0px);
    }

    button, input, select {
      font: inherit;
    }

    button, .source-link {
      min-height: 2.25rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0.45rem 0.65rem;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.35rem;
      max-width: 100%;
      white-space: nowrap;
    }

    button:hover, .source-link:hover {
      border-color: var(--accent);
      color: var(--accent-strong);
      background: var(--surface-raised);
    }

    button:focus-visible, .source-link:focus-visible, input:focus-visible, select:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }

    button[disabled] {
      cursor: not-allowed;
      color: var(--subtle);
      background: var(--surface-soft);
    }

    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }

    .wrap {
      width: min(1180px, calc(100% - 2rem));
      margin: 0 auto;
    }

    .page-header {
      border-bottom: 1px solid var(--border);
      background: var(--surface);
      box-shadow: 0 1px 0 rgba(17, 24, 23, 0.03);
    }

    .header-inner {
      padding: 0.95rem 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(17rem, auto);
      gap: 1.25rem;
      align-items: center;
    }

    .header-main {
      min-width: 0;
      display: grid;
      gap: 0.45rem;
      border-left: 3px solid var(--listing-accent);
      padding-left: 0.72rem;
    }

    .eyebrow {
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    h1 {
      margin: 0;
      font-size: clamp(1.45rem, 2.4vw, 2rem);
      line-height: 1.05;
      letter-spacing: 0;
    }

    .query {
      display: block;
      overflow-wrap: anywhere;
    }

    .header-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.2rem 0.7rem;
      color: var(--muted);
      font-size: 0.8rem;
    }

    .meta-chip {
      min-width: 0;
      display: inline-flex;
      align-items: center;
      gap: 0.32rem;
      overflow-wrap: anywhere;
    }

    .meta-chip + .meta-chip {
      border-left: 1px solid var(--border);
      padding-left: 0.7rem;
    }

    .meta-label, .fact-label {
      color: var(--subtle);
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
      white-space: nowrap;
    }

    .meta-value, .fact-value {
      min-width: 0;
      color: var(--text);
      overflow-wrap: anywhere;
    }

    .summary-panel {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.55rem;
      min-width: 17rem;
    }

    .summary-item {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface-raised);
      padding: 0.68rem 0.78rem;
      box-shadow: 0 1px 2px rgba(17, 24, 23, 0.04);
      position: relative;
      overflow: hidden;
    }

    .summary-item::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 3px;
      background: var(--listing-accent);
    }

    .summary-status::before {
      background: var(--accent-strong);
    }

    .summary-label {
      display: block;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    .summary-value {
      display: block;
      margin-top: 0.15rem;
      color: var(--text);
      font-size: 1.32rem;
      font-weight: 750;
      line-height: 1.15;
      overflow-wrap: anywhere;
    }

    .summary-value.status-value {
      font-size: 0.95rem;
      line-height: 1.2;
      color: var(--accent-strong);
    }

    .commandbar {
      position: sticky;
      top: 0;
      z-index: 4;
      border-bottom: 1px solid var(--border);
      background: rgba(244, 247, 246, 0.96);
      backdrop-filter: blur(10px);
    }

    .toolbar-inner {
      padding: 0.7rem 0;
      display: grid;
      grid-template-columns: minmax(14rem, 1fr) minmax(9rem, 11rem) auto;
      gap: 0.55rem;
      align-items: center;
    }

    .search-control, .sort-control {
      min-width: 0;
    }

    input[type="search"], select {
      min-height: 2.35rem;
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0.45rem 0.65rem;
    }

    input[type="search"]::placeholder {
      color: var(--subtle);
    }

    .toolbar-actions {
      min-width: 0;
      display: flex;
      gap: 0.4rem;
      flex-wrap: wrap;
      justify-content: flex-end;
      align-items: center;
      min-height: 2.35rem;
    }

    .density-toggle {
      display: inline-flex;
      border: 1px solid var(--border);
      border-radius: 7px;
      overflow: hidden;
      background: var(--surface);
      min-width: 11rem;
      flex: 0 1 auto;
    }

    .density-toggle button {
      border: 0;
      border-radius: 0;
      min-height: 2.25rem;
      padding: 0.42rem 0.6rem;
      min-width: 5.5rem;
      white-space: nowrap;
    }

    .density-toggle button + button {
      border-left: 1px solid var(--border);
    }

    .density-toggle button[aria-pressed="true"],
    .density-toggle button.is-active {
      background: var(--accent);
      color: #ffffff;
      font-weight: 760;
    }

    .tool-button {
      min-width: 0;
      min-height: 2.25rem;
      flex: 0 1 auto;
      padding: 0.42rem 0.6rem;
      white-space: nowrap;
    }

    main {
      padding: 0.9rem 0 2rem;
    }

    .results-head {
      min-height: 1.55rem;
      margin-bottom: 0.65rem;
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      align-items: center;
      color: var(--muted);
      font-size: 0.88rem;
    }

    .status {
      min-width: 0;
      overflow-wrap: anywhere;
    }

    .view-note {
      flex: 0 0 auto;
      color: var(--subtle);
    }

    .results {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(13.5rem, 1fr));
      gap: 0.75rem;
      align-items: stretch;
    }

    .result-card {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: auto minmax(0, 1fr);
      grid-template-areas:
        "media"
        "body";
      align-items: stretch;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      padding: 0;
      overflow: hidden;
      height: 100%;
      box-shadow: 0 1px 2px rgba(17, 24, 23, 0.04);
    }

    .result-card:hover {
      border-color: var(--listing-border);
      box-shadow: 0 4px 14px rgba(17, 24, 23, 0.07);
    }

    .result-media {
      grid-area: media;
      min-width: 0;
      width: 100%;
      position: relative;
    }

    .thumb {
      width: 100%;
      aspect-ratio: 1;
      border: 0;
      border-bottom: 1px solid var(--border);
      border-radius: 0;
      overflow: hidden;
      background: #f6f8f8;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 0.78rem;
      text-align: center;
      padding: 0.5rem;
    }

    .no-image .thumb {
      border-bottom-style: dashed;
      background: var(--surface-raised);
      font-size: 0.8rem;
      line-height: 1.2;
      padding: 1rem;
    }

    .has-image .thumb {
      padding: 0;
      position: relative;
    }

    .thumb img {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }

    .result-lead {
      min-width: 0;
      display: block;
    }

    .result-body {
      grid-area: body;
      min-width: 0;
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
      grid-template-rows: var(--title-box) var(--meta-box);
      gap: 0.55rem;
      align-content: stretch;
      padding: 0.65rem 0.7rem 0.7rem;
    }

    .rank-badge {
      position: absolute;
      top: 0.45rem;
      left: 0.45rem;
      border: 1px solid rgba(255, 255, 255, 0.72);
      border-radius: 999px;
      background: var(--listing-accent);
      color: #ffffff;
      font-size: 0.72rem;
      font-weight: 800;
      line-height: 1;
      padding: 0.28rem 0.42rem;
      white-space: nowrap;
      box-shadow: 0 1px 3px rgba(17, 24, 23, 0.16);
    }

    .listing-title {
      margin: 0;
    }

    .listing-display {
      min-width: 0;
      display: grid;
      gap: 0.28rem;
    }

    .listing-display-card {
      grid-template-rows: auto auto auto;
    }

    .listing-display-card .listing-line {
      grid-template-columns: minmax(0, 1fr);
      gap: 0;
    }

    .listing-display-card .listing-icon {
      display: none;
    }

    .listing-line {
      min-width: 0;
      margin: 0;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 0.42rem;
      align-items: start;
    }

    .listing-icon {
      display: inline-flex;
      align-items: center;
      min-width: 0;
      width: auto;
      color: var(--subtle);
      font-size: 0.68rem;
      font-weight: 820;
      letter-spacing: 0.02em;
      line-height: 1.2;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .listing-value {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .listing-line-title .listing-value {
      display: -webkit-box;
      -webkit-line-clamp: 4;
      -webkit-box-orient: vertical;
      overflow: hidden;
      color: var(--text);
      font-size: 0.92rem;
      font-weight: 760;
      line-height: 1.34;
      letter-spacing: 0;
      overflow-wrap: anywhere;
      text-overflow: ellipsis;
    }

    .listing-line-meta {
      color: var(--muted);
      font-size: 0.76rem;
      line-height: 1.25;
    }

    .listing-line-seller .listing-value {
      color: var(--text);
      font-weight: 700;
    }

    .listing-line-meta .listing-value {
      white-space: nowrap;
    }

    .result-actions {
      min-width: 0;
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 0.3rem;
      align-items: stretch;
      margin-top: 0.05rem;
      align-self: end;
    }

    .result-actions-primary {
      min-width: 0;
      width: 100%;
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem;
      align-items: stretch;
    }

    .action-button, .source-link, .details-toggle {
      min-height: 1.85rem;
      min-width: 0;
      border-radius: 4px;
      padding: 0.24rem 0.5rem;
      font-size: 0.76rem;
    }

    .copy-label {
      display: inline-block;
      font-size: 0;
      line-height: 0;
    }

    .copy-label::after {
      content: attr(data-full);
      font-size: 0.76rem;
      line-height: 1.2;
    }

    .result-actions-primary .action-button,
    .result-actions-primary .source-link {
      width: auto;
      flex: 0 1 auto;
    }

    .details-toggle {
      background: var(--surface-raised);
      color: var(--muted);
      flex: 0 0 auto;
    }

    .source-link {
      color: var(--listing-strong);
      border-color: var(--listing-border);
      background: var(--listing-soft);
      font-weight: 650;
      flex: 1 1 auto;
    }

    .result-details {
      min-width: 0;
      display: grid;
      gap: 0;
    }

    .modal-section {
      min-width: 0;
      padding: 0.9rem 1rem;
      border-bottom: 1px solid var(--surface-soft);
    }

    .modal-listing-section {
      background: var(--surface);
    }

    .modal-meta-section {
      min-width: 0;
      padding: 0.8rem 1rem;
      border-bottom: 1px solid var(--surface-soft);
      background: var(--surface-raised);
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
      gap: 0.55rem;
      align-items: stretch;
    }

    .modal-actions-section {
      position: static;
      min-width: 0;
      padding: 0.7rem 1rem;
      border-top: 1px solid var(--border);
      background: linear-gradient(180deg, var(--surface), var(--surface-raised));
      box-shadow: 0 -6px 18px rgba(17, 24, 23, 0.05);
    }

    .modal-similar-section {
      border-bottom: 0;
    }

    .listing-display-detail {
      border: 1px solid var(--listing-border);
      border-left: 3px solid var(--listing-accent);
      border-radius: 8px;
      background: var(--surface);
      padding: 0.78rem 0.85rem;
      display: grid;
      gap: 0.52rem;
      box-shadow: 0 1px 0 rgba(17, 24, 23, 0.03);
    }

    .listing-display-detail .listing-line-title {
      grid-template-columns: minmax(4.55rem, auto) minmax(0, 1fr);
      gap: 0.48rem;
      align-items: start;
      border-bottom: 1px solid var(--surface-soft);
      padding-bottom: 0.58rem;
    }

    .listing-display-detail .listing-line-title .listing-icon {
      width: auto;
      min-height: 1.35rem;
      border: 1px solid var(--listing-border);
      border-radius: 999px;
      background: var(--listing-soft);
      color: var(--listing-strong);
      display: inline-flex;
      justify-content: center;
      padding: 0.24rem 0.42rem;
      font-size: 0.66rem;
      line-height: 1.1;
      margin-top: 0.08rem;
    }

    .listing-display-detail .listing-line-title .listing-value {
      height: auto;
      -webkit-line-clamp: unset;
      overflow: visible;
      font-size: 1rem;
      font-weight: 800;
      line-height: 1.38;
      white-space: pre-wrap;
    }

    .listing-display-detail .listing-line-meta {
      height: auto;
      min-width: 0;
      grid-template-columns: minmax(4.55rem, auto) minmax(0, 1fr);
      gap: 0.48rem;
      align-items: start;
    }

    .listing-display-detail .listing-line-meta .listing-icon {
      width: auto;
      color: var(--subtle);
      font-size: 0.66rem;
      line-height: 1.2;
      padding-top: 0.12rem;
    }

    .listing-display-detail .listing-line-meta .listing-value {
      white-space: normal;
      font-size: 0.86rem;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }

    .listing-display-similar {
      gap: 0.2rem;
    }

    .listing-display-similar .listing-line {
      grid-template-columns: minmax(3.7rem, auto) minmax(0, 1fr);
      gap: 0.34rem;
    }

    .listing-display-similar .listing-icon {
      width: auto;
      font-size: 0.62rem;
      padding-top: 0.1rem;
    }

    .listing-display-similar .listing-line-title .listing-value {
      height: auto;
      -webkit-line-clamp: 2;
      color: var(--text);
      font-size: 0.86rem;
      font-weight: 700;
      line-height: 1.34;
    }

    .listing-display-similar .listing-line-meta {
      height: auto;
      font-size: 0.76rem;
    }

    .result-id-wrap {
      width: 100%;
      max-width: 100%;
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      padding: 0.58rem 0.62rem;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 0.18rem 0.5rem;
      align-items: center;
      color: var(--muted);
    }

    .result-id-icon {
      flex: 0 0 auto;
      color: var(--subtle);
      font-size: 0.68rem;
      font-weight: 820;
      letter-spacing: 0.02em;
      line-height: 1.2;
      text-transform: uppercase;
      grid-column: 1 / -1;
    }

    .result-id {
      min-width: 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.76rem;
      line-height: 1.25;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .mini-copy {
      min-height: 1.5rem;
      padding: 0.15rem 0.35rem;
      font-size: 0.72rem;
      flex: 0 0 auto;
    }

    .result-details-meta {
      min-width: 0;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
      gap: 0.35rem;
    }

    .modal-hero {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(9.5rem, 0.58fr) minmax(0, 1fr);
      gap: 0.7rem;
      align-items: stretch;
    }

    .modal-media-card {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 10px;
      background:
        linear-gradient(145deg, color-mix(in srgb, var(--surface-raised) 86%, var(--listing-soft)), var(--surface));
      padding: 0.55rem;
      display: grid;
      gap: 0.45rem;
      align-content: start;
    }

    .modal-media-card .thumb {
      width: 100%;
      min-height: 0;
      aspect-ratio: 4 / 3;
      border-radius: 8px;
      border: 1px solid var(--border);
      padding: 0;
      position: relative;
      overflow: hidden;
      align-self: start;
    }

    .modal-quick-facts {
      display: flex;
      flex-wrap: wrap;
      gap: 0.28rem;
      align-content: start;
    }

    .modal-fact {
      max-width: 100%;
      min-height: 1.45rem;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--surface);
      color: var(--muted);
      padding: 0.18rem 0.44rem;
      display: inline-flex;
      gap: 0.22rem;
      align-items: center;
      font-size: 0.72rem;
      font-weight: 760;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }

    .modal-fact strong {
      color: var(--text);
      font-weight: 820;
    }

    .modal-fact-label {
      color: var(--muted);
    }

    .modal-listing-copy {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--surface);
      padding: 0.65rem;
      display: grid;
      gap: 0.5rem;
      align-content: start;
    }

    .modal-section-label,
    .action-card-title {
      color: var(--subtle);
      font-size: 0.69rem;
      font-weight: 840;
      letter-spacing: 0.04em;
      line-height: 1.2;
      text-transform: uppercase;
    }

    .action-card-description {
      margin: 0;
      color: var(--muted);
      font-size: 0.76rem;
      line-height: 1.35;
    }

    .detail-chip {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      padding: 0.58rem 0.62rem;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 0.18rem 0.5rem;
      align-items: center;
    }

    .detail-label {
      grid-column: 1 / -1;
      color: var(--subtle);
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
      display: flex;
      gap: 0.25rem;
      align-items: center;
    }

    .detail-value {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--text);
      font-size: 0.8rem;
      line-height: 1.25;
    }

    .result-actions-secondary {
      min-width: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
    }

    .result-actions-secondary .action-button {
      min-height: 1.85rem;
    }

    .modal-actions {
      display: grid;
      grid-template-columns: minmax(13rem, 0.86fr) minmax(16rem, 1.14fr);
      gap: 0.55rem;
      align-items: start;
    }

    .modal-actions .action-button {
      min-height: 2.15rem;
      background: var(--surface);
    }

    .modal-primary-action,
    .report-form,
    .modal-utility-actions {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--surface);
      padding: 0.65rem;
    }

    .modal-primary-action {
      display: grid;
      gap: 0.42rem;
      align-content: start;
    }

    .modal-primary-action .action-button {
      width: 100%;
      align-self: start;
      border-color: var(--listing-border);
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--listing-soft) 78%, white), var(--listing-soft));
      color: var(--listing-strong);
      font-weight: 750;
    }

    .modal-action-status {
      min-height: 1.1rem;
      color: var(--muted);
      font-size: 0.76rem;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }

    .modal-action-status.error {
      color: var(--danger);
    }

    .modal-action-status.success {
      color: var(--accent-strong);
    }

    .draft-link {
      color: var(--accent-strong);
      font-weight: 750;
    }

    .report-form {
      display: grid;
      gap: 0.45rem;
    }

    .report-form label {
      display: grid;
      gap: 0.2rem;
      color: var(--muted);
      font-size: 0.74rem;
      font-weight: 750;
    }

    .report-form textarea {
      min-height: 4.25rem;
      resize: vertical;
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0.45rem 0.55rem;
      font: inherit;
      font-size: 0.8rem;
    }

    .report-form select {
      min-height: 2.15rem;
    }

    .modal-utility-actions {
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      justify-content: flex-end;
      align-items: center;
    }

    .similar-panel {
      border-top: 1px solid var(--surface-soft);
      padding-top: 0.45rem;
    }

    .similar-panel[hidden] {
      display: none;
    }

    .similar-title {
      margin: 0 0 0.35rem;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    .similar-item {
      padding: 0.45rem 0;
      border-top: 1px solid var(--surface-soft);
      color: var(--muted);
      overflow-wrap: anywhere;
    }

    .similar-item:first-of-type {
      border-top: 0;
      padding-top: 0;
    }

    .similar-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem 0.5rem;
      font-size: 0.78rem;
      color: var(--muted);
    }

    .results.density-dense {
      grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
      gap: 0.6rem;
    }

    .results.density-dense .result-card {
      grid-template-columns: minmax(0, 1fr);
      padding: 0;
    }

    .results.density-dense .result-media,
    .results.density-dense .thumb {
      width: 100%;
      aspect-ratio: 1;
      font-size: 0.72rem;
    }

    .results.density-dense .result-lead {
      display: block;
    }

    .results.density-dense .rank-badge {
      padding: 0.26rem 0.36rem;
      font-size: 0.74rem;
    }

    .results.density-dense .listing-line-title .listing-value {
      font-size: 0.84rem;
      line-height: 1.26;
      -webkit-line-clamp: 4;
    }

    .results.density-dense .listing-line-meta {
      font-size: 0.74rem;
    }

    .results.density-dense .result-actions {
      margin-top: 0;
    }

    .results.density-dense .result-body {
      gap: 0.42rem;
      padding: 0.48rem 0.5rem 0.52rem;
    }

    .results.density-dense .result-id-wrap {
      padding: 0.22rem 0.34rem;
    }

    .results.density-dense .action-button,
    .results.density-dense .source-link,
    .results.density-dense .details-toggle {
      min-height: 1.62rem;
      padding: 0.18rem 0.38rem;
      font-size: 0.74rem;
    }

    @media (min-width: 841px) {
      .results.density-dense {
        grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
      }

      .results.density-dense .result-card {
        grid-template-columns: 5.25rem minmax(0, 1fr);
        grid-template-rows: auto;
        grid-template-areas: "media body";
        min-height: 7rem;
      }

      .results.density-dense .thumb {
        min-height: 100%;
        border-right: 1px solid var(--border);
        border-bottom: 0;
      }

      .results.density-dense .result-body {
        min-height: 0;
        padding: 0.55rem 0.6rem;
        gap: 0.4rem;
      }

      .results.density-dense .listing-display-card {
        gap: 0.2rem;
      }

      .results.density-dense .listing-line-title .listing-value {
        -webkit-line-clamp: 3;
      }
    }

    .empty {
      border: 1px dashed var(--border-strong);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 2rem 1.25rem;
      text-align: center;
      color: var(--muted);
      box-shadow: var(--shadow);
    }

    .empty-title {
      margin: 0 0 0.35rem;
      color: var(--text);
      font-size: 1rem;
      letter-spacing: 0;
    }

    .empty-message {
      margin: 0 auto;
      max-width: 34rem;
      overflow-wrap: anywhere;
    }

    .result-modal[hidden] {
      display: none;
    }

    .result-modal {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: grid;
      place-items: center;
      padding: 1rem;
    }

    .modal-backdrop {
      position: absolute;
      inset: 0;
      background: rgba(17, 24, 23, 0.56);
    }

    .modal-panel {
      position: relative;
      z-index: 1;
      width: min(48rem, 100%);
      max-height: min(44rem, calc(100vh - 2rem));
      border: 1px solid var(--border);
      border-radius: 12px;
      background: var(--surface);
      box-shadow: 0 18px 48px rgba(17, 24, 23, 0.22);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      overflow: hidden;
    }

    .modal-header {
      min-width: 0;
      border-bottom: 1px solid var(--border);
      background: var(--surface-raised);
      padding: 0.95rem 1.05rem;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 0.75rem;
      align-items: start;
    }

    .modal-heading {
      min-width: 0;
      display: grid;
      gap: 0.22rem;
    }

    @media (min-width: 1100px) {
      .modal-panel {
        width: min(58rem, calc(100vw - 2rem));
        max-height: calc(100vh - 2rem);
      }

      .modal-hero {
        grid-template-columns: minmax(11.5rem, 0.48fr) minmax(0, 1fr);
      }

      .modal-actions {
        grid-template-columns: minmax(14rem, 0.75fr) minmax(18rem, 1.25fr);
      }
    }

    .modal-kicker {
      margin: 0;
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 700;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }

    .modal-title {
      margin: 0;
      color: var(--text);
      font-size: 1.08rem;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .modal-close {
      min-height: 2rem;
      border-color: var(--border-strong);
      padding: 0.26rem 0.62rem;
      color: var(--muted);
      background: var(--surface);
    }

    .modal-body {
      min-width: 0;
      overflow: auto;
      padding: 0;
      background: var(--surface);
    }

    .toast {
      position: fixed;
      right: 1rem;
      bottom: 1rem;
      max-width: min(24rem, calc(100vw - 2rem));
      border-radius: 7px;
      background: var(--text);
      color: #ffffff;
      padding: 0.65rem 0.8rem;
      opacity: 0;
      transform: translateY(0.5rem);
      transition: opacity 150ms ease, transform 150ms ease;
      pointer-events: none;
      z-index: 8;
    }

    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }

    @media (max-width: 840px) {
      .header-inner {
        grid-template-columns: 1fr;
        align-items: stretch;
      }

      .summary-panel {
        min-width: 0;
      }

      .toolbar-inner {
        grid-template-columns: 1fr;
        grid-template-areas:
          "search"
          "sort"
          "actions";
      }

      .search-control { grid-area: search; }
      .sort-control { grid-area: sort; }
      .toolbar-actions {
        grid-area: actions;
        width: 100%;
        min-width: 0;
        justify-content: flex-start;
        align-items: stretch;
        gap: 0.35rem;
        overflow: visible;
        flex-wrap: wrap;
      }

      .toolbar-actions .density-toggle {
        min-width: 0;
        width: 100%;
      }

      .toolbar-actions .density-toggle button {
        min-width: 0;
        width: 100%;
      }

      .toolbar-actions .tool-button {
        min-width: 0;
        width: 100%;
      }

      .search-control,
      .sort-control,
      .toolbar-actions {
        width: 100%;
      }

      .sort-control {
        max-width: 100%;
      }

      .result-actions,
      .result-actions-primary {
        width: 100%;
        grid-template-columns: 1fr;
      }

      .result-actions .action-button,
      .result-actions .source-link,
      .result-actions .details-toggle {
        width: 100%;
      }

      .result-actions-primary .action-button,
      .result-actions-primary .source-link {
        width: 100%;
      }

      .results.density-dense .result-actions {
        grid-template-columns: 1fr;
      }

      .results.density-dense .result-actions-primary .action-button,
      .results.density-dense .result-actions-primary .source-link,
      .results.density-dense .result-actions .details-toggle {
        width: 100%;
        flex: 1 1 0;
      }

      .results.density-dense .result-actions-primary {
        width: 100%;
      }

      .copy-label::after {
        content: attr(data-short);
      }

      .toolbar-actions .density-toggle button,
      .toolbar-actions .tool-button,
      .action-button,
      .result-actions .action-button,
      .result-actions .source-link,
      .result-actions .details-toggle,
      .result-actions-primary .action-button,
      .result-actions-primary .source-link,
      .results.density-dense .result-actions-primary .action-button,
      .results.density-dense .result-actions-primary .source-link,
      .results.density-dense .result-actions .details-toggle {
        white-space: normal;
      }
    }

    @media (max-width: 760px) {
      .wrap {
        width: min(calc(100% - 1rem), 44rem);
      }

      .header-inner {
        padding: 0.8rem 0;
      }

      .header-meta {
        font-size: 0.8rem;
      }

      .summary-panel {
        grid-template-columns: 1fr 1fr;
      }

      .toolbar-inner {
        gap: 0.45rem;
      }

      .toolbar-actions {
        justify-content: flex-end;
        gap: 0.3rem;
      }

      .results.density-dense .result-actions {
        grid-template-columns: 1fr;
        gap: 0.28rem;
      }

      .results.density-dense .result-actions-primary {
        width: 100%;
        gap: 0.25rem;
      }

      .results.density-dense .result-actions-primary .action-button,
      .results.density-dense .result-actions-primary .source-link,
      .results.density-dense .result-actions .details-toggle {
        width: 100%;
        flex: 1 1 0;
      }

      .density-toggle button, .tool-button {
        min-height: 2.15rem;
        padding: 0.38rem 0.48rem;
      }

      .results-head {
        align-items: flex-start;
        flex-direction: column;
        gap: 0.2rem;
      }

      .results,
      .results.density-dense {
        grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr));
        gap: 0.6rem;
      }

    .result-card,
    .results.density-dense .result-card {
        grid-template-columns: minmax(0, 1fr);
        grid-template-areas:
          "media"
          "body";
        padding: 0;
      }

    .results.density-dense .result-card {
        padding: 0;
      }

      .result-media,
      .results.density-dense .result-media,
      .thumb,
      .results.density-dense .thumb {
        width: 100%;
      }

      .result-actions {
        width: 100%;
        grid-template-columns: 1fr;
        align-items: stretch;
      }

      .result-actions-primary {
        width: 100%;
        gap: 0.25rem;
      }

      .result-actions-primary .action-button,
      .result-actions-primary .source-link,
      .result-actions .details-toggle {
        width: 100%;
        flex: 1 1 0;
      }

      .listing-line-meta .listing-value {
        white-space: normal;
      }

      .modal-hero {
        grid-template-columns: minmax(0, 0.78fr) minmax(0, 1fr);
        gap: 0.55rem;
      }

      .result-details-meta {
        grid-template-columns: minmax(0, 1fr);
      }

      .detail-value {
        white-space: normal;
        overflow-wrap: anywhere;
      }

    }

    @media (max-width: 520px) {
      .wrap {
        width: min(calc(100% - 0.75rem), 44rem);
      }

      h1 {
        font-size: 1.22rem;
      }

      .summary-value {
        font-size: 1rem;
      }

      .summary-item {
        padding: 0.55rem;
      }

      .toolbar-inner {
        display: flex;
        flex-wrap: wrap;
      }

      .search-control {
        flex: 1 1 0;
      }

      .sort-control {
        flex: 0 0 7.5rem;
      }

      .toolbar-actions {
        flex: 1 0 100%;
        min-width: 0;
        flex-wrap: wrap;
        justify-content: flex-start;
        overflow: visible;
      }

      .density-toggle {
        flex: 1 0 100%;
      }

      .tool-button {
        flex: 1 1 calc(50% - 0.3rem);
      }

      .result-actions-primary {
        gap: 0.25rem;
      }

      .results {
        grid-template-columns: 1fr;
        gap: 0.55rem;
      }

      .results.density-dense {
        grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
        gap: 0.55rem;
      }

      .action-button, .source-link, .details-toggle {
        padding-left: 0.4rem;
        padding-right: 0.4rem;
      }

      .result-modal {
        align-items: end;
        padding: 0.5rem;
      }

      .modal-panel {
        width: 100%;
        max-height: calc(100vh - 1rem);
        border-radius: 12px;
      }

      .modal-header {
        padding: 0.75rem;
      }

      .modal-title {
        font-size: 1rem;
      }

      .modal-kicker {
        font-size: 0.72rem;
      }

      .modal-body {
        padding: 0;
      }

      .modal-section,
      .modal-meta-section,
      .modal-actions-section {
        padding-left: 0.75rem;
        padding-right: 0.75rem;
      }

      .modal-actions-section {
        padding-top: 0.55rem;
        padding-bottom: 0.55rem;
      }

      .modal-meta-section {
        grid-template-columns: 1fr;
        gap: 0.4rem;
      }

      .result-id-wrap,
      .detail-chip {
        padding: 0.48rem 0.52rem;
      }

      .modal-hero {
        grid-template-columns: 1fr;
        gap: 0.5rem;
      }

      .modal-media-card {
        grid-template-columns: minmax(6.25rem, 0.42fr) minmax(0, 1fr);
        align-items: start;
      }

      .modal-media-card,
      .modal-listing-copy,
      .modal-primary-action,
      .report-form,
      .modal-utility-actions {
        border-radius: 9px;
      }

      .modal-media-card .thumb {
        aspect-ratio: 1;
      }

      .modal-listing-copy {
        padding: 0.58rem;
        gap: 0.4rem;
      }

      .modal-primary-action,
      .report-form,
      .modal-utility-actions {
        padding: 0.5rem;
      }

      .modal-primary-action,
      .report-form {
        gap: 0.3rem;
      }

      .modal-section-label,
      .action-card-title {
        font-size: 0.66rem;
      }

      .listing-display-detail {
        padding: 0.62rem;
        gap: 0.42rem;
      }

      .listing-display-detail .listing-line-title {
        grid-template-columns: 1.22rem minmax(0, 1fr);
        gap: 0.36rem;
        padding-bottom: 0.45rem;
      }

      .listing-display-detail .listing-line-title .listing-icon {
        width: 1.08rem;
        min-height: 1.08rem;
        border-radius: 4px;
        font-size: 0.66rem;
        margin-top: 0.03rem;
      }

      .listing-display-detail .listing-line-title .listing-value {
        font-size: 0.88rem;
        font-weight: 780;
        line-height: 1.28;
      }

      .listing-display-detail .listing-line-meta {
        grid-template-columns: 1.22rem minmax(0, 1fr);
        gap: 0.36rem;
      }

      .listing-display-detail .listing-line-meta .listing-icon {
        width: 1.08rem;
        font-size: 0.72rem;
      }

      .listing-display-detail .listing-line-meta .listing-value {
        font-size: 0.8rem;
        line-height: 1.26;
      }

      .report-form textarea {
        min-height: 2rem;
        padding-top: 0.38rem;
        padding-bottom: 0.38rem;
      }

      .report-form select {
        min-height: 2rem;
      }

      .modal-action-status {
        min-height: 0.9rem;
        font-size: 0.72rem;
      }

      .modal-actions {
        display: grid;
        grid-template-columns: 1fr;
        gap: 0.35rem;
      }

      .modal-primary-action,
      .report-form,
      .modal-utility-actions {
        grid-column: 1 / -1;
      }

      .modal-actions .action-button {
        width: 100%;
      }

      .header-inner {
        gap: 0.55rem;
        padding: 0.65rem 0;
      }

      .header-main {
        gap: 0.35rem;
        padding-left: 0.58rem;
      }

      .eyebrow {
        font-size: 0.7rem;
      }

      .header-meta {
        gap: 0.2rem 0.5rem;
        font-size: 0.72rem;
      }

      .meta-chip + .meta-chip {
        border-left: 0;
        padding-left: 0;
      }

      .meta-label {
        display: inline;
        font-size: 0.66rem;
      }

      .summary-panel {
        gap: 0.35rem;
      }

      .summary-item {
        padding: 0.4rem 0.5rem;
        display: flex;
        gap: 0.35rem;
        align-items: baseline;
      }

      .summary-label {
        font-size: 0.68rem;
      }

      .summary-value,
      .summary-value.status-value {
        margin-top: 0;
        font-size: 0.88rem;
      }
    }

    @media (min-width: 741px) and (max-width: 820px) {
      .results {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .results.density-dense {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }

      .result-card,
      .results.density-dense .result-card {
        grid-template-columns: minmax(0, 1fr);
      }

      .toolbar-inner {
        grid-template-columns: 1fr;
        grid-template-areas:
          "search"
          "sort"
          "actions";
      }

      .search-control,
      .sort-control,
      .toolbar-actions {
        min-width: 0;
      }

      .toolbar-actions {
        width: 100%;
        justify-content: flex-start;
        align-items: stretch;
        flex-wrap: wrap;
        overflow: visible;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0.4rem;
      }

      .toolbar-actions .density-toggle {
        grid-column: span 2;
      }

      .toolbar-actions .density-toggle,
      .toolbar-actions .tool-button {
        min-width: 0;
        width: 100%;
      }

      .result-actions,
      .result-actions-primary {
        width: 100%;
        grid-template-columns: 1fr;
      }

      .result-actions-primary .action-button,
      .result-actions-primary .source-link,
      .result-actions .details-toggle {
        width: 100%;
      }

      .listing-line-title .listing-value,
      .results.density-dense .listing-line-title .listing-value {
        -webkit-line-clamp: 4;
        line-height: 1.36;
      }

      .result-details-meta {
        grid-template-columns: minmax(0, 1fr);
      }

      .detail-value {
        white-space: normal;
        overflow-wrap: anywhere;
      }


      .search-control { grid-area: search; }
      .sort-control { grid-area: sort; }
      .toolbar-actions {
        grid-area: actions;
      }
    }

    @media (max-width: 360px) {
      .results,
      .results.density-dense {
        grid-template-columns: 1fr;
      }
    }

    @media (min-width: 521px) and (max-width: 840px) {
      .toolbar-inner {
        grid-template-columns: minmax(14rem, 1fr) minmax(8rem, 10rem);
        grid-template-areas:
          "search sort"
          "actions actions";
        gap: 0.5rem;
      }

      .toolbar-actions {
        display: grid;
        grid-template-columns: minmax(11rem, 1.2fr) repeat(4, minmax(0, 1fr));
        align-items: stretch;
        gap: 0.4rem;
      }

      .toolbar-actions .density-toggle {
        grid-column: auto;
        width: 100%;
      }

      .toolbar-actions .tool-button {
        width: 100%;
      }

      .result-actions,
      .results.density-dense .result-actions {
        grid-template-columns: 1fr;
        gap: 0.32rem;
      }

      .result-actions-primary,
      .results.density-dense .result-actions-primary {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.32rem;
        width: 100%;
      }

      .result-actions-primary .action-button,
      .result-actions-primary .source-link,
      .results.density-dense .result-actions-primary .action-button,
      .results.density-dense .result-actions-primary .source-link {
        width: 100%;
      }

      .result-actions .details-toggle,
      .results.density-dense .result-actions .details-toggle {
        width: 100%;
      }
    }

    @media (max-width: 520px) {
      .page-header {
        position: relative;
        z-index: 5;
      }

      .toolbar-inner {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(7rem, 0.42fr);
        grid-template-areas:
          "search sort"
          "actions actions";
        gap: 0.42rem;
        padding: 0.52rem 0;
      }

      .search-control {
        grid-area: search;
        flex: initial;
      }

      .sort-control {
        grid-area: sort;
        flex: initial;
      }

      .toolbar-actions {
        grid-area: actions;
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.35rem;
      }

      .toolbar-actions .density-toggle {
        grid-column: 1 / -1;
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        min-width: 0;
        width: 100%;
      }

      .toolbar-actions .tool-button {
        width: 100%;
        min-height: 2rem;
        padding: 0.32rem 0.35rem;
        font-size: 0.74rem;
      }

      main {
        padding-top: 0.65rem;
      }

      .results,
      .results.density-dense {
        grid-template-columns: 1fr;
        gap: 0.55rem;
      }

      .result-card,
      .results.density-dense .result-card {
        grid-template-columns: clamp(5.75rem, 28vw, 7rem) minmax(0, 1fr);
        grid-template-rows: auto;
        grid-template-areas: "media body";
        min-height: 8.5rem;
      }

      .result-media,
      .results.density-dense .result-media {
        min-height: 100%;
      }

      .thumb,
      .results.density-dense .thumb {
        height: 100%;
        min-height: 8.5rem;
        aspect-ratio: auto;
        border-right: 1px solid var(--border);
        border-bottom: 0;
      }

      .no-image .thumb {
        min-height: 8.5rem;
        padding: 0.55rem;
      }

      .result-body,
      .results.density-dense .result-body {
        min-height: 0;
        gap: 0.38rem;
        padding: 0.5rem 0.55rem;
      }

      .listing-display-card,
      .results.density-dense .listing-display-card {
        gap: 0.18rem;
      }

      .listing-line-title .listing-value,
      .results.density-dense .listing-line-title .listing-value {
        -webkit-line-clamp: 3;
        font-size: 0.84rem;
        line-height: 1.28;
      }

      .listing-line-meta,
      .results.density-dense .listing-line-meta {
        font-size: 0.72rem;
        line-height: 1.22;
      }

      .result-actions,
      .results.density-dense .result-actions {
        gap: 0.25rem;
      }

      .action-button,
      .source-link,
      .details-toggle,
      .results.density-dense .action-button,
      .results.density-dense .source-link,
      .results.density-dense .details-toggle {
        min-height: 1.72rem;
        padding: 0.2rem 0.34rem;
        font-size: 0.72rem;
      }
    }

    /* Professional results workspace consolidated layer. */
    body {
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.09), transparent 24rem),
        linear-gradient(180deg, #f7faf9 0%, #eef4f2 100%);
    }

    .page-header {
      background:
        linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(238, 246, 244, 0.94));
    }

    .header-inner {
      padding: 1.1rem 0;
      grid-template-columns: minmax(0, 1fr) minmax(18rem, 22rem);
    }

    .header-main {
      border-left-color: var(--accent);
      gap: 0.5rem;
    }

    .eyebrow {
      color: var(--accent-strong);
      letter-spacing: 0.04em;
    }

    .summary-panel {
      align-self: stretch;
    }

    .summary-item {
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.82);
      box-shadow: 0 8px 24px rgba(17, 24, 23, 0.06);
    }

    .commandbar {
      background: rgba(247, 250, 249, 0.92);
    }

    .toolbar-inner {
      grid-template-columns: minmax(18rem, 1fr) minmax(10rem, 12rem) auto;
    }

    input[type="search"], select, button, .source-link {
      border-radius: 9px;
    }

    main.wrap {
      width: var(--workspace-main);
      padding-top: 1rem;
    }

    .results {
      grid-template-columns: 1fr;
      gap: 0.7rem;
    }

    .result-card,
    .results.density-dense .result-card {
      grid-template-columns: var(--result-media-desktop) minmax(0, 1fr);
      grid-template-rows: auto;
      grid-template-areas: "media body";
      min-height: var(--result-card-min-height);
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.94);
      box-shadow: 0 10px 28px rgba(17, 24, 23, 0.055);
    }

    .result-card:hover {
      transform: translateY(-1px);
      box-shadow: 0 16px 36px rgba(17, 24, 23, 0.09);
    }

    .result-media,
    .results.density-dense .result-media {
      min-height: 100%;
    }

    .thumb,
    .results.density-dense .thumb {
      height: 100%;
      min-height: var(--result-card-min-height);
      aspect-ratio: auto;
      border-right: 1px solid var(--border);
      border-bottom: 0;
      background:
        linear-gradient(135deg, rgba(15, 118, 110, 0.08), rgba(255, 255, 255, 0.68));
    }

    .no-image .thumb {
      min-height: var(--result-card-min-height);
    }

    .result-body,
    .results.density-dense .result-body {
      padding: 0.85rem 0.95rem;
      gap: 0.7rem;
      grid-template-rows: minmax(0, 1fr) auto;
    }

    .listing-display-card {
      gap: 0.34rem;
    }

    .listing-line-title .listing-value,
    .results.density-dense .listing-line-title .listing-value {
      -webkit-line-clamp: 3;
      font-size: 1rem;
      line-height: 1.34;
      letter-spacing: -0.01em;
    }

    .listing-line-meta,
    .results.density-dense .listing-line-meta {
      font-size: 0.8rem;
    }

    .result-actions,
    .results.density-dense .result-actions {
      grid-template-columns: minmax(0, 1fr) minmax(5.25rem, auto);
      gap: 0.45rem;
      align-items: end;
    }

    .result-actions-primary,
    .results.density-dense .result-actions-primary {
      display: flex;
      gap: 0.4rem;
    }

    .action-button, .source-link, .details-toggle,
    .results.density-dense .action-button,
    .results.density-dense .source-link,
    .results.density-dense .details-toggle {
      min-height: 2.1rem;
      border-radius: 8px;
      padding: 0.38rem 0.62rem;
      font-size: 0.8rem;
      font-weight: 700;
    }

    .details-toggle {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }

    .details-toggle:hover {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
      color: #ffffff;
    }

    .source-link {
      background: #f4fbfa;
    }

    .rank-badge {
      top: 0.65rem;
      left: 0.65rem;
      padding: 0.34rem 0.52rem;
    }

    .results.density-dense {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.55rem;
    }

    .results.density-dense .result-card {
      grid-template-columns: var(--dense-media) minmax(0, 1fr);
      min-height: var(--dense-card-height);
      border-radius: 12px;
    }

    .results.density-dense .result-media {
      min-height: 100%;
    }

    .results.density-dense .thumb,
    .results.density-dense .no-image .thumb {
      min-height: var(--dense-card-height);
      height: 100%;
      border-right: 1px solid var(--border);
      border-bottom: 0;
    }

    .results.density-dense .rank-badge {
      top: 0.42rem;
      left: 0.42rem;
      padding: 0.24rem 0.34rem;
      font-size: 0.68rem;
    }

    .results.density-dense .result-body {
      padding: 0.48rem 0.52rem;
      gap: 0.32rem;
    }

    .results.density-dense .listing-display-card {
      gap: 0.12rem;
    }

    .results.density-dense .listing-line-title .listing-value {
      -webkit-line-clamp: 2;
      font-size: 0.82rem;
      line-height: 1.22;
    }

    .results.density-dense .listing-line-meta {
      font-size: 0.7rem;
      line-height: 1.18;
    }

    .results.density-dense .result-actions {
      grid-template-columns: minmax(0, 1fr) minmax(4.4rem, auto);
      gap: 0.28rem;
    }

    .results.density-dense .result-actions-primary {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.28rem;
    }

    .results.density-dense .action-button,
    .results.density-dense .source-link,
    .results.density-dense .details-toggle {
      min-height: 1.65rem;
      padding: 0.18rem 0.34rem;
      font-size: 0.7rem;
      border-radius: 7px;
    }

    .modal-panel {
      width: var(--modal-width);
      max-height: calc(100vh - 2rem);
      border-radius: 18px;
      box-shadow: 0 24px 72px rgba(17, 24, 23, 0.28);
    }

    .modal-header {
      padding: 1rem 1.15rem;
      background:
        linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(239, 247, 245, 0.98));
    }

    .modal-title {
      font-size: 1.16rem;
    }

    .modal-section {
      padding: 1rem 1.15rem;
    }

    .modal-hero {
      grid-template-columns: minmax(12rem, 0.42fr) minmax(0, 1fr);
      gap: 0.85rem;
    }

    .modal-media-card,
    .modal-listing-copy,
    .modal-primary-action,
    .report-form,
    .modal-utility-actions {
      border-radius: 14px;
      box-shadow: 0 8px 24px rgba(17, 24, 23, 0.045);
    }

    .modal-media-card .thumb {
      min-height: 0;
      max-height: 18rem;
      aspect-ratio: 4 / 3;
      border-radius: 11px;
    }

    .modal-quick-facts {
      display: grid;
      grid-template-columns: 1fr;
      gap: 0.32rem;
    }

    .modal-fact {
      justify-content: space-between;
      border-radius: 10px;
      padding: 0.38rem 0.5rem;
      font-size: 0.74rem;
    }

    .modal-listing-copy {
      padding: 0.8rem;
    }

    .listing-display-detail {
      border-radius: 12px;
      padding: 0.9rem;
      gap: 0.6rem;
    }

    .listing-display-detail .listing-line-title .listing-value {
      font-size: 1.04rem;
      line-height: 1.42;
    }

    .modal-actions-section {
      padding: 0.85rem 1.15rem;
      background: #f8fbfa;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
    }

    .modal-actions {
      grid-template-columns: minmax(14rem, 0.8fr) minmax(18rem, 1.2fr);
      gap: 0.75rem;
    }

    .modal-primary-action .action-button {
      min-height: 2.55rem;
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }

    .modal-primary-action .action-button:hover {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
      color: #ffffff;
    }

    .modal-meta-section {
      padding: 0.85rem 1.15rem;
      background: #f5f8f7;
      grid-template-columns: minmax(0, 1fr) minmax(16rem, 0.8fr);
    }

    .result-id-wrap,
    .detail-chip {
      border-radius: 12px;
      background: #ffffff;
    }

    @media (min-width: 1180px) {
      main.wrap {
        width: var(--workspace-main-wide);
      }

      .results.density-dense {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .modal-panel {
        width: var(--modal-width-wide);
      }
    }

    @media (min-width: 761px) and (max-width: 1024px) {
      main.wrap {
        width: var(--workspace-main-tablet);
      }

      .results,
      .results.density-dense {
        grid-template-columns: 1fr;
      }

      .results.density-dense {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .result-card,
      .results.density-dense .result-card {
        grid-template-columns: var(--result-media-tablet) minmax(0, 1fr);
      }

      .results.density-dense .result-card {
        grid-template-columns: var(--dense-media) minmax(0, 1fr);
        min-height: var(--dense-card-height);
      }

      .modal-panel {
        width: var(--modal-width-tablet);
      }

      .modal-hero {
        grid-template-columns: minmax(10rem, 0.48fr) minmax(0, 1fr);
      }

      .modal-actions,
      .modal-meta-section {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 760px) {
      main.wrap {
        width: var(--workspace-main-mobile);
      }

      .header-inner {
        grid-template-columns: 1fr;
      }

      .toolbar-inner {
        grid-template-columns: 1fr;
      }

      .results,
      .results.density-dense {
        grid-template-columns: 1fr;
      }

      .result-card,
      .results.density-dense .result-card {
        grid-template-columns: var(--result-media-mobile) minmax(0, 1fr);
        min-height: var(--result-card-mobile-height);
        border-radius: 12px;
      }

      .results.density-dense .result-card {
        grid-template-columns: var(--dense-media-mobile) minmax(0, 1fr);
        min-height: var(--dense-card-mobile-height);
      }

      .thumb,
      .results.density-dense .thumb,
      .no-image .thumb {
        min-height: var(--result-card-mobile-height);
      }

      .results.density-dense .thumb,
      .results.density-dense .no-image .thumb {
        min-height: var(--dense-card-mobile-height);
      }

      .result-body,
      .results.density-dense .result-body {
        padding: 0.55rem 0.6rem;
        gap: 0.42rem;
      }

      .results.density-dense .result-body {
        padding: 0.42rem 0.48rem;
        gap: 0.24rem;
      }

      .listing-line-title .listing-value,
      .results.density-dense .listing-line-title .listing-value {
        -webkit-line-clamp: 3;
        font-size: 0.86rem;
        line-height: 1.28;
      }

      .results.density-dense .listing-line-title .listing-value {
        -webkit-line-clamp: 2;
        font-size: 0.78rem;
        line-height: 1.2;
      }

      .listing-display-detail .listing-line-title,
      .listing-display-detail .listing-line-meta {
        grid-template-columns: minmax(4.35rem, auto) minmax(0, 1fr);
      }

      .listing-display-detail .listing-line-title .listing-icon,
      .listing-display-detail .listing-line-meta .listing-icon {
        width: auto;
      }

      .listing-display-detail .listing-line-title .listing-icon {
        min-height: 1.25rem;
        padding: 0.2rem 0.38rem;
        font-size: 0.62rem;
      }

      .result-actions,
      .results.density-dense .result-actions {
        grid-template-columns: 1fr;
        gap: 0.3rem;
      }

      .results.density-dense .result-actions {
        grid-template-columns: minmax(0, 1fr) minmax(4.35rem, auto);
        gap: 0.24rem;
      }

      .result-actions-primary,
      .results.density-dense .result-actions-primary {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .results.density-dense .result-actions-primary {
        grid-template-columns: 1fr;
      }

      .result-actions .details-toggle,
      .results.density-dense .result-actions .details-toggle {
        width: 100%;
      }

      .results.density-dense .source-link,
      .results.density-dense .result-actions-primary .action-button[disabled] {
        display: none;
      }

      .modal-panel {
        border-radius: 16px;
      }

      .modal-hero,
      .modal-actions,
      .modal-meta-section {
        grid-template-columns: 1fr;
      }

      .modal-media-card {
        grid-template-columns: minmax(6rem, 0.42fr) minmax(0, 1fr);
      }

      .modal-media-card .thumb {
        aspect-ratio: 1;
        max-height: 8rem;
      }

      .modal-quick-facts {
        align-content: start;
      }
    }

    @media (max-width: 420px) {
      .result-card,
      .results.density-dense .result-card {
        grid-template-columns: var(--result-media-narrow) minmax(0, 1fr);
      }

      .results.density-dense .result-card {
        grid-template-columns: var(--dense-media-mobile) minmax(0, 1fr);
      }

      .toolbar-actions {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .toolbar-actions .density-toggle {
        grid-column: 1 / -1;
      }

      .result-actions-primary,
      .results.density-dense .result-actions-primary {
        grid-template-columns: 1fr;
      }

      .result-actions,
      .results.density-dense .result-actions {
        grid-template-columns: minmax(0, 1fr) minmax(4.75rem, auto);
        align-items: stretch;
      }

      .result-actions-primary .source-link,
      .result-actions-primary .action-button[disabled],
      .results.density-dense .result-actions-primary .source-link,
      .results.density-dense .result-actions-primary .action-button[disabled] {
        display: none;
      }

      .modal-section,
      .modal-meta-section,
      .modal-actions-section {
        padding-left: 0.65rem;
        padding-right: 0.65rem;
      }
    }

    @media print {
      .commandbar, .result-actions, .result-modal, .toast {
        display: none;
      }

      body {
        background: #ffffff;
      }

      .page-header, .result-card {
        box-shadow: none;
      }

      .result-card {
        break-inside: avoid;
      }
    }
  </style>
</head>
<body>
  <header class="page-header">
    <div class="wrap header-inner">
      <div class="header-main">
        <div class="eyebrow">WatchFacts search</div>
        <h1><span class="sr-only">Query </span><span class="query" id="queryText">No result payload</span></h1>
        <div class="header-meta" id="resultsMeta" aria-label="Result page metadata">
          <span class="meta-chip"><span class="meta-label">Generated</span><span class="meta-value" id="createdAt">Unknown</span></span>
          <span class="meta-chip"><span class="meta-label">Expires</span><span class="meta-value" id="expiresAt">Unknown</span></span>
          <span class="meta-chip"><span class="meta-label">Page</span><span class="meta-value" id="pageRange">Unavailable</span></span>
        </div>
      </div>
      <div class="summary-panel" aria-label="Result summary">
        <div class="summary-item summary-total">
          <span class="summary-label">Total</span>
          <span class="summary-value" id="resultCount">0</span>
        </div>
        <div class="summary-item summary-status">
          <span class="summary-label">Status</span>
          <span class="summary-value status-value" id="pageStatus">Unavailable</span>
        </div>
      </div>
    </div>
  </header>

  <section class="commandbar" aria-label="Result controls">
    <div class="wrap toolbar-inner">
      <div class="search-control">
        <label class="sr-only" for="filterInput">Filter results</label>
        <input id="filterInput" type="search" autocomplete="off" placeholder="Filter model, seller, phone, source">
      </div>
      <div class="sort-control">
        <label class="sr-only" for="sortSelect">Sort results</label>
        <select id="sortSelect" aria-label="Sort results">
          <option value="rank">Rank</option>
          <option value="posted_desc">Posted date</option>
          <option value="seller">Seller</option>
        </select>
      </div>
      <div class="toolbar-actions">
        <div class="density-toggle" role="group" aria-label="View density">
          <button type="button" id="densityComfortable" aria-pressed="true">Comfort</button>
          <button type="button" id="densityDense" aria-pressed="false">Dense</button>
        </div>
        <button type="button" class="tool-button" id="copyPageLink">Link</button>
        <button type="button" class="tool-button" id="exportJson">JSON</button>
        <button type="button" class="tool-button" id="exportCsv">CSV</button>
        <button type="button" class="tool-button" id="printPage">Print</button>
      </div>
    </div>
  </section>

  <main class="wrap">
    <div class="results-head">
      <div class="status" id="statusText" role="status" aria-live="polite"></div>
      <div class="view-note" id="viewNote"></div>
    </div>
    <section class="results" id="resultsList" aria-label="WatchFacts results"></section>
  </main>

  <div class="result-modal" id="resultModal" role="dialog" aria-modal="true" aria-labelledby="resultModalTitle" hidden>
    <div class="modal-backdrop" id="resultModalBackdrop"></div>
    <div class="modal-panel" id="resultModalPanel" role="document">
      <div class="modal-header">
        <div class="modal-heading">
          <p class="modal-kicker" id="resultModalKicker"></p>
          <h2 class="modal-title" id="resultModalTitle">Result details</h2>
        </div>
        <button type="button" class="modal-close" id="resultModalClose">Close</button>
      </div>
      <div class="modal-body" id="resultModalBody"></div>
    </div>
  </div>

  <div class="toast" id="toast" role="status" aria-live="polite"></div>

  <script>
    let results = null;
    results = __WATCHFACTS_RESULTS_PAYLOAD__;

    const state = {
      filter: "",
      sort: "rank",
      density: "comfortable"
    };

    const els = {
      query: document.getElementById("queryText"),
      createdAt: document.getElementById("createdAt"),
      expiresAt: document.getElementById("expiresAt"),
      pageRange: document.getElementById("pageRange"),
      resultCount: document.getElementById("resultCount"),
      pageStatus: document.getElementById("pageStatus"),
      filter: document.getElementById("filterInput"),
      sort: document.getElementById("sortSelect"),
      list: document.getElementById("resultsList"),
      status: document.getElementById("statusText"),
      viewNote: document.getElementById("viewNote"),
      toast: document.getElementById("toast"),
      densityComfortable: document.getElementById("densityComfortable"),
      densityDense: document.getElementById("densityDense"),
      copyPageLink: document.getElementById("copyPageLink"),
      exportJson: document.getElementById("exportJson"),
      exportCsv: document.getElementById("exportCsv"),
      printPage: document.getElementById("printPage"),
      modal: document.getElementById("resultModal"),
      modalBackdrop: document.getElementById("resultModalBackdrop"),
      modalPanel: document.getElementById("resultModalPanel"),
      modalKicker: document.getElementById("resultModalKicker"),
      modalTitle: document.getElementById("resultModalTitle"),
      modalBody: document.getElementById("resultModalBody"),
      modalClose: document.getElementById("resultModalClose")
    };

    let lastModalTrigger = null;

    function text(value, fallback = "") {
      if (value === null || value === undefined || value === "") return fallback;
      return String(value);
    }

    function numberValue(value, fallback = 0) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    function truncate(value, maxLength) {
      const raw = text(value).replace(/\\s+/g, " ").trim();
      if (raw.length <= maxLength) return raw;
      return raw.slice(0, maxLength - 3).trimEnd() + "...";
    }

    function allResults() {
      return results && Array.isArray(results.results) ? results.results : [];
    }

    function resultLabel(count) {
      const numeric = numberValue(count);
      return numeric === 1 ? "1 result" : String(numeric) + " results";
    }

    function formatDate(value) {
      const raw = text(value, "unknown");
      const parsed = Date.parse(raw);
      if (Number.isNaN(parsed)) return raw;
      return new Date(parsed).toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit"
      });
    }

    function pageRangeText() {
      if (!results) return "Unavailable";
      const offset = Math.max(numberValue(results.offset, 0), 0);
      const count = allResults().length;
      const total = numberValue(results.total_count, count);
      if (!count) return "0 of " + total;
      return String(offset + 1) + "-" + String(offset + count) + " of " + total;
    }

    function statusText() {
      if (!results) return "Unavailable";
      if (results.next_offset !== null && results.next_offset !== undefined) return "More available";
      const count = allResults().length;
      const total = numberValue(results.total_count, count);
      return count < total ? "Partial page" : "Complete";
    }

    function showToast(message) {
      els.toast.textContent = message;
      els.toast.classList.add("show");
      window.setTimeout(() => els.toast.classList.remove("show"), 1600);
    }

    async function copyText(value, label) {
      const content = text(value);
      if (!content) return;
      try {
        await navigator.clipboard.writeText(content);
      } catch (error) {
        const field = document.createElement("textarea");
        field.value = content;
        field.setAttribute("readonly", "");
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.appendChild(field);
        field.select();
        document.execCommand("copy");
        field.remove();
      }
      showToast(label + " copied");
    }

    function download(filename, mimeType, content) {
      const blob = new Blob([content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function csvEscape(value) {
      const raw = text(value);
      return '"' + raw.replaceAll('"', '""') + '"';
    }

    function exportCsv() {
      const rows = [["rank", "result_id", "listing_text", "seller", "posted_date", "seller_phone", "source_url"]];
      for (const item of currentResults()) {
        rows.push([
          item.rank,
          item.result_id,
          item.listing_text,
          item.seller,
          item.posted_date,
          item.seller_phone,
          item.source_url
        ]);
      }
      download("watchfacts-results.csv", "text/csv", rows.map(row => row.map(csvEscape).join(",")).join("\\n"));
    }

    function exportJson() {
      download("watchfacts-results.json", "application/json", JSON.stringify(results || { results: [] }, null, 2));
    }

    function resultText(item) {
      const similarText = Array.isArray(item.similar_results)
        ? item.similar_results.map(similar => text(similar.listing_text)).join(" ")
        : "";
      return [
        item.rank,
        item.result_id,
        item.listing_text,
        item.seller,
        item.posted_date,
        item.seller_phone,
        item.source_url,
        similarText
      ].map(value => text(value).toLowerCase()).join(" ");
    }

    function postedTime(value) {
      const normalized = text(value).split("·")[0].split(" - ")[0].trim();
      const parsed = Date.parse(normalized);
      return Number.isNaN(parsed) ? 0 : parsed;
    }

    function currentResults() {
      const filter = state.filter.trim().toLowerCase();
      let items = allResults().filter(item => !filter || resultText(item).includes(filter));
      if (state.sort === "posted_desc") {
        items = items.slice().sort((a, b) => postedTime(b.posted_date) - postedTime(a.posted_date) || numberValue(a.rank) - numberValue(b.rank));
      } else if (state.sort === "seller") {
        items = items.slice().sort((a, b) => text(a.seller).localeCompare(text(b.seller)) || numberValue(a.rank) - numberValue(b.rank));
      } else {
        items = items.slice().sort((a, b) => numberValue(a.rank) - numberValue(b.rank));
      }
      return items;
    }

    function createNode(tag, className, value) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (value !== undefined) node.textContent = value;
      return node;
    }

    function makeButton(label, title, onClick, className = "action-button") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = label;
      button.title = title;
      button.setAttribute("aria-label", title);
      button.addEventListener("click", onClick);
      return button;
    }

    function focusWithoutScroll(node) {
      if (!node || typeof node.focus !== "function") return;
      try {
        node.focus({ preventScroll: true });
      } catch (error) {
        node.focus();
      }
    }

    function modalScrollbarCompensation() {
      const width = window.innerWidth - document.documentElement.clientWidth;
      return width > 0 ? width : 0;
    }

    function needsModalScrollbarCompensation() {
      return !(window.CSS && CSS.supports && CSS.supports("scrollbar-gutter", "stable"));
    }

    function lockModalScroll() {
      document.documentElement.style.setProperty(
        "--modal-scrollbar-compensation",
        String(needsModalScrollbarCompensation() ? modalScrollbarCompensation() : 0) + "px"
      );
      document.body.classList.add("modal-open");
    }

    function unlockModalScroll() {
      document.body.classList.remove("modal-open");
      document.documentElement.style.removeProperty("--modal-scrollbar-compensation");
    }

    function hostName(value) {
      const raw = text(value);
      if (!raw) return "";
      try {
        return new URL(raw).hostname.replace(/^www\\./, "");
      } catch (error) {
        return raw;
      }
    }

    function listingDisplayText(item) {
      if (!item) return "";
      const density = state.density === "dense" ? "dense" : "comfortable";
      const preview = item && item.listing_text_preview;
      if (preview && typeof preview === "object") {
        const previewValue = text(preview[density] || preview.comfortable);
        if (previewValue) return previewValue;
      }
      if (density === "dense") {
        return truncate(item.listing_text, 150);
      }
      return truncate(item.listing_text, 220);
    }

    function copyField(value, fallback = "-") {
      const normalized = text(value).replace(/\\s+/g, " ").trim();
      return normalized || fallback;
    }

    function padDatePart(value) {
      return String(value).padStart(2, "0");
    }

    function formatCopyDate(value) {
      const raw = copyField(value);
      if (raw === "-") return raw;
      const normalized = raw.split("·", 1)[0].split(" - ", 1)[0].trim();
      const iso = normalized.match(/^(\\d{4})-(\\d{2})-(\\d{2})/);
      if (iso) return iso[3] + "/" + iso[2] + "/" + iso[1];

      const monthMatch = normalized.match(/^([A-Za-z]+)\\s+(\\d{1,2}),\\s*(\\d{4})$/);
      const monthNumber = monthMatch && {
        january: 1, jan: 1,
        february: 2, feb: 2,
        march: 3, mar: 3,
        april: 4, apr: 4,
        may: 5,
        june: 6, jun: 6,
        july: 7, jul: 7,
        august: 8, aug: 8,
        september: 9, sep: 9, sept: 9,
        october: 10, oct: 10,
        november: 11, nov: 11,
        december: 12, dec: 12
      }[monthMatch[1].toLowerCase()];
      if (monthNumber) {
        return padDatePart(monthMatch[2]) + "/" + padDatePart(monthNumber) + "/" + monthMatch[3];
      }
      return raw;
    }

    function compactListingItem(item) {
      const compactText = listingDisplayText(item);
      if (!compactText) return item;
      const currentText = copyField(item && item.listing_text, "No listing text");
      if (compactText === currentText) return item;
      return Object.assign({}, item || {}, { listing_text: compactText });
    }

    function formattedListingFields(item) {
      return [
        {
          label: "Listing",
          icon: "Listing: ",
          copyPrefix: "Listing: ",
          className: "listing-line-title",
          value: copyField(item.listing_text, "No listing text")
        },
        {
          label: "Seller",
          icon: "Seller: ",
          copyPrefix: "Seller: ",
          className: "listing-line-meta listing-line-seller",
          value: copyField(item.seller)
        },
        {
          label: "Posted",
          icon: "Date: ",
          copyPrefix: "Date: ",
          className: "listing-line-meta listing-line-date",
          value: formatCopyDate(item.posted_date)
        }
      ];
    }

    function formatListingCopy(item) {
      return formattedListingFields(item).map((field) => field.copyPrefix + field.value).join("\\n");
    }

    function createFormattedListingDisplay(item, className = "", titleTag = "h2") {
      const compactClass = className && (className.includes("listing-display-card") || className.includes("listing-display-similar"));
      const sourceItem = compactClass ? compactListingItem(item) : item;
      const displayClass = ["listing-display", className].filter(Boolean).join(" ");
      const display = createNode("div", displayClass);
      for (const field of formattedListingFields(sourceItem)) {
        const tagName = field.className.includes("listing-line-title") ? titleTag : "p";
        const row = document.createElement(tagName);
        row.className = "listing-line " + field.className + (tagName === "h2" ? " listing-title" : "");
        row.title = field.label + ": " + field.value;
        row.setAttribute("aria-label", row.title);
        const icon = createNode("span", "listing-icon", field.icon);
        icon.setAttribute("aria-hidden", "true");
        row.append(icon, createNode("span", "listing-value", field.value));
        display.appendChild(row);
      }
      return display;
    }

    function makeFormattedCopyButton(item) {
      const button = makeButton("", "Copy formatted listing text", () => copyText(formatListingCopy(item), "Listing text"));
      const label = createNode("span", "copy-label", "Copy Text");
      label.dataset.full = "Copy Text";
      label.dataset.short = "Copy";
      label.setAttribute("aria-hidden", "true");
      button.appendChild(label);
      return button;
    }

    function hasImage(item) {
      return Boolean(text(item && item.image_url).trim());
    }

    function similarCount(item) {
      return Array.isArray(item && item.similar_results) ? item.similar_results.length : 0;
    }

    function detailMeta(item) {
      const values = [];
      if (item.seller_phone) values.push({ label: "Phone: ", value: text(item.seller_phone) });
      const source = hostName(item.source_url);
      if (source) values.push({ label: "Source: ", value: source, copyValue: text(item.source_url), copyLabel: "Source URL" });
      return values;
    }

    function createDetailsMeta(item) {
      const meta = createNode("div", "result-details-meta");
      for (const value of detailMeta(item)) {
        const chip = createNode("div", "detail-chip");
        chip.setAttribute("aria-label", value.label + value.value);
        const label = createNode("span", "detail-label");
        label.appendChild(createNode("span", "", value.label));
        const content = createNode("span", "detail-value", value.value);
        content.title = value.value;
        chip.append(label, content);
        if (value.copyValue) {
          chip.appendChild(makeButton("Copy", "Copy " + value.copyLabel, () => copyText(value.copyValue, value.copyLabel), "mini-copy"));
        }
        meta.appendChild(chip);
      }
      return meta;
    }

    function createModalFact(label, value) {
      const fact = createNode("span", "modal-fact");
      fact.setAttribute("aria-label", label + ": " + value);
      fact.append(createNode("span", "modal-fact-label", label), createNode("strong", "", value));
      return fact;
    }

    function createModalQuickFacts(item) {
      const facts = createNode("div", "modal-quick-facts");
      facts.appendChild(createModalFact("Seller", copyField(item.seller)));
      facts.appendChild(createModalFact("Posted", formatCopyDate(item.posted_date)));
      if (similarCount(item)) facts.appendChild(createModalFact("Similar", String(similarCount(item))));
      facts.appendChild(createModalFact("Media", hasImage(item) ? "Image" : "No image"));
      return facts;
    }

    function createThumb(item) {
      const thumb = createNode("div", "thumb");
      if (hasImage(item)) {
        const img = document.createElement("img");
        img.src = item.image_url;
        img.alt = "Listing image for result " + text(item.rank, "");
        img.loading = "lazy";
        img.addEventListener("error", () => {
          img.remove();
          thumb.textContent = "No image";
        });
        thumb.appendChild(img);
      } else {
        thumb.textContent = "No image";
      }
      return thumb;
    }

    function populateSimilarPanel(item, panel) {
      if (panel.dataset.loaded === "true") return;
      panel.replaceChildren();
      const similarTitle = createNode("h3", "similar-title", "Similar listings");
      panel.appendChild(similarTitle);
      for (const similarItem of item.similar_results) {
        const row = createNode("div", "similar-item");
        row.appendChild(createFormattedListingDisplay(similarItem, "listing-display-similar", "div"));
        const rowMeta = createNode("div", "similar-meta");
        if (similarItem.seller_phone) rowMeta.appendChild(createNode("span", "", "Phone: " + similarItem.seller_phone));
        if (similarItem.source_url) rowMeta.appendChild(createNode("span", "", "Source: " + hostName(similarItem.source_url)));
        if (rowMeta.childElementCount) row.appendChild(rowMeta);
        panel.appendChild(row);
      }
      panel.dataset.loaded = "true";
    }

    function resultActionConfig() {
      const actions = results && results.actions;
      return actions && typeof actions === "object" ? actions : {};
    }

    async function postResultAction(url, item, extra = {}) {
      const actions = resultActionConfig();
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({
          action_nonce: text(actions.action_nonce),
          result_id: text(item.result_id)
        }, extra))
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      if (!response.ok || !payload || payload.ok === false) {
        const message = payload && payload.message ? payload.message : "Action failed.";
        throw new Error(message);
      }
      return payload;
    }

    function createActionStatus() {
      const status = createNode("div", "modal-action-status");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      return status;
    }

    function setActionStatus(status, message, className = "") {
      status.className = "modal-action-status" + (className ? " " + className : "");
      status.textContent = message;
    }

    function createOpenWaDraftAction(item) {
      const actions = resultActionConfig();
      const container = createNode("div", "modal-primary-action");
      container.appendChild(createNode("div", "action-card-title", "OpenWA handoff"));
      container.appendChild(createNode("p", "action-card-description", "Create a seller chat draft from this exact result_id."));
      const status = createActionStatus();
      if (!actions.openwa_draft_url || !actions.action_nonce) {
        container.appendChild(makeButton("Copy OpenWA", "Copy prompt to create an OpenWA chat draft", () => copyText(openWaPrompt(item), "OpenWA prompt")));
        container.appendChild(status);
        setActionStatus(status, "Draft creation is not available on this page.");
        return container;
      }

      const button = makeButton("Create OpenWA draft", "Create an OpenWA chat draft", async () => {
        button.disabled = true;
        button.textContent = "Creating...";
        setActionStatus(status, "Creating draft...");
        try {
          const payload = await postResultAction(actions.openwa_draft_url, item);
          button.textContent = "Draft created";
          setActionStatus(status, "Draft created.", "success");
          if (payload.dashboard_url) {
            const link = document.createElement("a");
            link.className = "draft-link";
            link.href = payload.dashboard_url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "Open draft";
            status.append(" ");
            status.appendChild(link);
          }
        } catch (error) {
          button.disabled = false;
          button.textContent = "Create OpenWA draft";
          setActionStatus(status, error.message || "OpenWA draft failed.", "error");
        }
      });
      container.append(button, status);
      return container;
    }

    function createReportIssueForm(item) {
      const actions = resultActionConfig();
      const form = createNode("form", "report-form");
      form.appendChild(createNode("div", "action-card-title", "Quality feedback"));
      form.appendChild(createNode("p", "action-card-description", "Report missing details or a wrong match for review."));
      const status = createActionStatus();
      if (!actions.report_url || !actions.action_nonce) {
        form.appendChild(makeButton("Copy Report", "Copy prompt to report this result", () => copyText(reportPrompt(item), "Report prompt")));
        form.appendChild(status);
        setActionStatus(status, "Issue reporting is not available on this page.");
        return form;
      }

      const reasonLabel = createNode("label", "", "Issue reason");
      const reason = document.createElement("select");
      reason.name = "reason";
      reason.required = true;
      for (const option of [
        ["wrong_result", "Wrong result"],
        ["missing_info", "Missing info"],
        ["other", "Other"]
      ]) {
        const node = document.createElement("option");
        node.value = option[0];
        node.textContent = option[1];
        reason.appendChild(node);
      }
      reasonLabel.appendChild(reason);

      const notesLabel = createNode("label", "", "Notes");
      const notes = document.createElement("textarea");
      notes.name = "notes";
      notes.maxLength = 1000;
      notes.placeholder = "Optional context for review";
      notesLabel.appendChild(notes);

      const submit = makeButton("Submit report", "Submit result issue report", () => {}, "action-button");
      submit.type = "submit";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        submit.disabled = true;
        reason.disabled = true;
        notes.disabled = true;
        submit.textContent = "Submitting...";
        setActionStatus(status, "Submitting report...");
        try {
          const payload = await postResultAction(actions.report_url, item, {
            reason: reason.value,
            notes: notes.value
          });
          submit.textContent = payload.issue_ref ? "Reported as " + payload.issue_ref : "Reported";
          setActionStatus(status, "Issue recorded for review.", "success");
        } catch (error) {
          reason.disabled = false;
          notes.disabled = false;
          submit.disabled = false;
          submit.textContent = "Submit report";
          setActionStatus(status, error.message || "Report failed.", "error");
        }
      });

      form.append(reasonLabel, notesLabel, submit, status);
      return form;
    }

    function createOverflowActions(item, similarPanel, className = "") {
      const secondaryClass = ["result-actions-secondary", className].filter(Boolean).join(" ");
      const secondary = createNode("div", secondaryClass);
      secondary.appendChild(createOpenWaDraftAction(item));
      secondary.appendChild(createReportIssueForm(item));
      const utilities = createNode("div", "modal-utility-actions");
      const count = similarCount(item);
      if (count) {
        const similarButton = makeButton("+" + String(count) + " similar", "Show similar listings", () => {
          const shouldShow = similarPanel.hidden;
          if (shouldShow) populateSimilarPanel(item, similarPanel);
          similarPanel.hidden = !shouldShow;
          similarButton.setAttribute("aria-expanded", String(shouldShow));
          similarButton.textContent = shouldShow ? "Hide similar" : "+" + String(count) + " similar";
          similarButton.setAttribute("aria-label", shouldShow ? "Hide similar listings" : "Show similar listings");
          similarButton.title = shouldShow ? "Hide similar listings" : "Show similar listings";
        });
        similarButton.setAttribute("aria-expanded", "false");
        similarButton.setAttribute("aria-controls", similarPanel.id);
        utilities.appendChild(similarButton);
      }
      if (utilities.childElementCount) secondary.appendChild(utilities);
      return secondary;
    }

    function modalKicker(item) {
      return "WatchFacts listing";
    }

    function modalTitle(item) {
      return "Result #" + text(item.rank, "unknown") + " details";
    }

    function createResultDetailsContent(item) {
      const details = createNode("section", "result-details");
      const listingSection = createNode("section", "modal-section modal-listing-section");
      const hero = createNode("div", "modal-hero");
      const mediaCard = createNode("div", "modal-media-card");
      mediaCard.append(createThumb(item), createModalQuickFacts(item));
      const listingCopy = createNode("div", "modal-listing-copy");
      listingCopy.append(
        createNode("div", "modal-section-label", "Listing snapshot"),
        createFormattedListingDisplay(item, "listing-display-detail", "div")
      );
      hero.append(mediaCard, listingCopy);
      listingSection.appendChild(hero);
      details.appendChild(listingSection);

      const similar = createNode("section", "similar-panel");
      similar.hidden = true;
      similar.id = "modal-similar-" + text(item.rank, "result").replace(/[^a-zA-Z0-9_-]/g, "-");
      const actionsSection = createNode("section", "modal-actions-section");
      actionsSection.appendChild(createOverflowActions(item, similar, "modal-actions"));
      details.appendChild(actionsSection);

      const metaSection = createNode("section", "modal-section modal-meta-section");
      const idWrap = createNode("div", "result-id-wrap");
      const idIcon = createNode("span", "result-id-icon", "ID: ");
      idIcon.setAttribute("aria-hidden", "true");
      const idText = createNode("span", "result-id", text(item.result_id, "No result_id"));
      const idCopy = makeButton("Copy ID", "Copy result_id", () => copyText(item.result_id, "Result ID"), "mini-copy");
      idWrap.setAttribute("aria-label", "ID: " + text(item.result_id, "No result_id"));
      idWrap.append(idIcon, idText, idCopy);
      metaSection.appendChild(idWrap);

      const detailsMeta = createDetailsMeta(item);
      if (detailsMeta.childElementCount) metaSection.appendChild(detailsMeta);
      details.appendChild(metaSection);

      if (similarCount(item)) {
        const similarSection = createNode("section", "modal-section modal-similar-section");
        similarSection.appendChild(similar);
        details.appendChild(similarSection);
      }
      return details;
    }

    function modalFocusableNodes() {
      return Array.from(els.modalPanel.querySelectorAll(
        "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
      ));
    }

    function openDetailsModal(item, trigger) {
      lastModalTrigger = trigger || null;
      els.modalKicker.textContent = modalKicker(item);
      els.modalTitle.textContent = modalTitle(item);
      els.modalBody.replaceChildren(createResultDetailsContent(item));
      els.modal.hidden = false;
      lockModalScroll();
      focusWithoutScroll(els.modalClose);
    }

    function closeDetailsModal(options = {}) {
      const restoreFocus = options.restoreFocus !== false;
      if (els.modal.hidden) return;
      els.modal.hidden = true;
      els.modalKicker.textContent = "";
      els.modalTitle.textContent = "Result details";
      els.modalBody.replaceChildren();
      unlockModalScroll();
      if (restoreFocus && lastModalTrigger) focusWithoutScroll(lastModalTrigger);
      lastModalTrigger = null;
    }

    function handleModalKeydown(event) {
      if (els.modal.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeDetailsModal();
        return;
      }
      if (event.key !== "Tab") return;

      const nodes = modalFocusableNodes();
      if (!nodes.length) {
        event.preventDefault();
        focusWithoutScroll(els.modalClose);
        return;
      }

      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function createResultCard(item) {
      const imagePresent = hasImage(item);
      const article = document.createElement("article");
      article.className = "result-card " + (imagePresent ? "has-image" : "no-image");

      const media = createNode("div", "result-media");
      media.appendChild(createThumb(item));
      media.appendChild(createNode("span", "rank-badge", "#" + text(item.rank, "-")));

      const lead = createNode("div", "result-lead");
      const listingDisplay = createFormattedListingDisplay(item, "listing-display-card");
      lead.appendChild(listingDisplay);
      const body = createNode("div", "result-body");

      const actions = createNode("div", "result-actions");
      const primary = createNode("div", "result-actions-primary");
      primary.appendChild(makeFormattedCopyButton(item));
      if (item.source_url) {
        const link = document.createElement("a");
        link.className = "source-link";
        link.href = item.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Source";
        link.title = "Open source listing";
        link.setAttribute("aria-label", "Open source listing");
        primary.appendChild(link);
      } else {
        const sourceButton = makeButton("Source", "No source URL", () => {});
        sourceButton.disabled = true;
        primary.appendChild(sourceButton);
      }

      const detailsToggle = makeButton("More", "Show result details", () => {
        openDetailsModal(item, detailsToggle);
      }, "details-toggle");
      detailsToggle.setAttribute("aria-haspopup", "dialog");
      detailsToggle.setAttribute("aria-controls", "resultModal");
      actions.append(primary, detailsToggle);
      body.append(lead, actions);

      article.append(media, body);
      return article;
    }

    function openWaPrompt(item) {
      return [
        "Create an OpenWA chat draft for this WatchFacts result.",
        "query: " + text(results && results.query),
        "result_id: " + text(item.result_id)
      ].join("\\n");
    }

    function reportPrompt(item) {
      return [
        "Report an issue for this WatchFacts result.",
        "query: " + text(results && results.query),
        "result_id: " + text(item.result_id),
        "reason: wrong_result | missing_info | other",
        "notes: "
      ].join("\\n");
    }

    function emptyState(title, message) {
      const node = createNode("div", "empty");
      const heading = createNode("h2", "empty-title", title);
      const body = createNode("p", "empty-message", message);
      node.append(heading, body);
      return node;
    }

    function renderHeader() {
      if (!results) {
        els.query.textContent = "No result payload";
        els.createdAt.textContent = "Unknown";
        els.expiresAt.textContent = "Unknown";
        els.pageRange.textContent = "Unavailable";
        els.resultCount.textContent = "0";
        els.pageStatus.textContent = "Unavailable";
        return;
      }

      const total = numberValue(results.total_count, allResults().length);
      els.query.textContent = text(results.query, "Query unavailable");
      els.createdAt.textContent = formatDate(results.created_at);
      els.expiresAt.textContent = formatDate(results.expires_at);
      els.pageRange.textContent = pageRangeText();
      els.resultCount.textContent = String(total);
      els.pageStatus.textContent = statusText();
    }

    function renderToolbarState() {
      const hasPayload = Boolean(results);
      els.filter.disabled = !hasPayload;
      els.sort.disabled = !hasPayload;
      els.copyPageLink.disabled = !hasPayload;
      els.exportJson.disabled = !hasPayload;
      els.exportCsv.disabled = !hasPayload;
      els.printPage.disabled = !hasPayload;
      els.densityComfortable.disabled = !hasPayload;
      els.densityDense.disabled = !hasPayload;
      els.densityComfortable.setAttribute("aria-pressed", String(state.density === "comfortable"));
      els.densityDense.setAttribute("aria-pressed", String(state.density === "dense"));
      els.densityComfortable.classList.toggle("is-active", state.density === "comfortable");
      els.densityDense.classList.toggle("is-active", state.density === "dense");
      els.list.classList.toggle("density-dense", state.density === "dense");
      els.list.classList.toggle("density-comfortable", state.density === "comfortable");
      els.viewNote.textContent = hasPayload ? (state.density === "dense" ? "Dense view" : "Comfortable view") : "";
    }

    function renderResults() {
      closeDetailsModal({ restoreFocus: false });
      if (!results) {
        els.status.textContent = "No result payload loaded.";
        els.list.replaceChildren(emptyState("No result payload", "This page was opened without an injected WatchFacts result payload."));
        return;
      }

      const sourceItems = allResults();
      const items = currentResults();
      if (!sourceItems.length) {
        els.status.textContent = "0 shown";
        els.list.replaceChildren(emptyState("No results", "This query returned no WatchFacts listings."));
        return;
      }

      if (!items.length) {
        els.status.textContent = "0 of " + sourceItems.length + " matching";
        els.list.replaceChildren(emptyState("No matching results", "The current filter does not match any listing on this page."));
        return;
      }

      els.status.textContent = state.filter
        ? String(items.length) + " of " + String(sourceItems.length) + " matching"
        : resultLabel(items.length) + " shown";
      els.list.replaceChildren(...items.map(createResultCard));
    }

    function render() {
      renderHeader();
      renderToolbarState();
      renderResults();
    }

    function initializeDensity() {
      state.density = "comfortable";
    }

    els.filter.addEventListener("input", event => {
      state.filter = event.target.value;
      render();
    });
    els.sort.addEventListener("change", event => {
      state.sort = event.target.value;
      render();
    });
    els.densityComfortable.addEventListener("click", () => {
      state.density = "comfortable";
      render();
    });
    els.densityDense.addEventListener("click", () => {
      state.density = "dense";
      render();
    });
    els.copyPageLink.addEventListener("click", () => copyText(window.location.href, "Page link"));
    els.exportJson.addEventListener("click", exportJson);
    els.exportCsv.addEventListener("click", exportCsv);
    els.printPage.addEventListener("click", () => window.print());
    els.modalClose.addEventListener("click", () => closeDetailsModal());
    els.modalBackdrop.addEventListener("click", () => closeDetailsModal());
    document.addEventListener("keydown", handleModalKeydown);

    initializeDensity();
    render();
  </script>
</body>
</html>
"""

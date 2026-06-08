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

from app.search_result import SearchResult, source_result_id


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_TEXT_CHARS = 4000
MAX_SHORT_TEXT_CHARS = 512
MAX_URL_CHARS = 2048
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
        "results": page_results,
    }

    token = _new_token(active_config.storage_dir)
    cleanup_expired_result_pages(active_config, now=created_at)
    active_config.storage_dir.mkdir(parents=True, exist_ok=True)
    html = render_result_page_template(payload)
    page_path = _page_path(active_config, token)
    page_path.write_text(html, encoding="utf-8")
    timestamp = created_at.timestamp()
    os.utime(page_path, (timestamp, timestamp))
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
        _unlink_quietly(page_path)
        cleanup_expired_result_pages(active_config, now=now)
        return ResultPageRead(status_code=410)

    cleanup_expired_result_pages(active_config, now=now)
    return ResultPageRead(
        status_code=200,
        html=page_path.read_text(encoding="utf-8"),
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
            _unlink_quietly(path)
            removed += 1
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
    return {
        "rank": rank,
        "result_id": result_id,
        "source_result_id": result_id,
        "listing_text": _clean_text(result.listing_text, MAX_TEXT_CHARS),
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
    return {
        "listing_text": _clean_text(result.listing_text, MAX_TEXT_CHARS),
        "seller": _clean_optional_text(result.seller),
        "posted_date": _clean_optional_text(result.posted_date),
        "image_url": _normalize_url(result.image_url, config.watchfacts_url),
        "source_url": _normalize_url(result.source_url, config.watchfacts_url),
        "seller_phone": _clean_optional_text(result.seller_phone, max_chars=64),
    }


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
        if not (_page_path_for_dir(storage_dir, token).exists()):
            return token


def _page_path(config: ResultPageConfig, token: str) -> Path:
    return _page_path_for_dir(config.storage_dir, token)


def _page_path_for_dir(storage_dir: Path, token: str) -> Path:
    return storage_dir / f"{token}.html"


def _is_expired(path: Path, config: ResultPageConfig, *, now: datetime) -> bool:
    return path.stat().st_mtime + config.ttl_seconds < now.timestamp()


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
      --warning: #8a5a00;
      --danger: #b42318;
      --shadow: 0 1px 2px rgba(17, 24, 23, 0.06);
      --radius: 8px;
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
    }

    .density-toggle {
      display: inline-flex;
      border: 1px solid var(--border);
      border-radius: 7px;
      overflow: hidden;
      background: var(--surface);
    }

    .density-toggle button {
      border: 0;
      border-radius: 0;
      min-height: 2.25rem;
      padding: 0.42rem 0.6rem;
    }

    .density-toggle button + button {
      border-left: 1px solid var(--border);
    }

    .density-toggle button[aria-pressed="true"] {
      background: var(--accent);
      color: #ffffff;
    }

    .tool-button {
      min-width: 0;
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
      --title-box: 4.95rem;
      --meta-box: 1.05rem;
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

    .thumb img {
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
      grid-template-rows: var(--title-box) var(--meta-box) var(--meta-box);
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
      grid-template-columns: 1.25rem minmax(0, 1fr);
      gap: 0.28rem;
      align-items: start;
    }

    .listing-icon {
      display: inline-block;
      width: 1.25rem;
      color: var(--listing-strong);
      font-size: 0.86rem;
      line-height: 1.24;
      text-align: center;
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
      height: var(--title-box);
      color: var(--text);
      font-size: 0.92rem;
      font-weight: 760;
      line-height: 1.34;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }

    .listing-line-meta {
      height: var(--meta-box);
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
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 0.3rem;
      align-items: center;
      margin-top: 0.05rem;
      align-self: end;
    }

    .result-actions-primary {
      min-width: 0;
      display: contents;
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
      width: 100%;
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
      position: sticky;
      bottom: 0;
      z-index: 1;
      min-width: 0;
      padding: 0.7rem 1rem;
      border-top: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.96);
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
      display: flex;
      flex-wrap: wrap;
      gap: 0.52rem 0.5rem;
      align-items: center;
      box-shadow: 0 1px 0 rgba(17, 24, 23, 0.03);
    }

    .listing-display-detail .listing-line-title {
      flex: 1 0 100%;
      grid-template-columns: 1.55rem minmax(0, 1fr);
      gap: 0.48rem;
      align-items: start;
      border-bottom: 1px solid var(--surface-soft);
      padding-bottom: 0.58rem;
    }

    .listing-display-detail .listing-line-title .listing-icon {
      width: 1.35rem;
      min-height: 1.35rem;
      border: 1px solid #f2d6a8;
      border-radius: 5px;
      background: #fff8ec;
      color: #7a4d00;
      display: grid;
      place-items: center;
      font-size: 0.76rem;
      line-height: 1;
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
      flex: 0 1 auto;
      height: auto;
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--surface-raised);
      padding: 0.2rem 0.5rem 0.2rem 0.36rem;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 0.28rem;
      align-items: center;
    }

    .listing-display-detail .listing-line-meta .listing-icon {
      width: 1rem;
      color: var(--listing-strong);
      font-size: 0.78rem;
      line-height: 1;
    }

    .listing-display-detail .listing-line-meta .listing-value {
      font-size: 0.78rem;
      line-height: 1.2;
    }

    .listing-display-similar {
      gap: 0.2rem;
    }

    .listing-display-similar .listing-line {
      grid-template-columns: 1.1rem minmax(0, 1fr);
      gap: 0.22rem;
    }

    .listing-display-similar .listing-icon {
      width: 1.1rem;
      font-size: 0.76rem;
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
      padding: 0.52rem 0.58rem;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 0.45rem;
      align-items: center;
      color: var(--muted);
    }

    .result-id-icon {
      flex: 0 0 auto;
      color: var(--listing-strong);
      font-size: 0.86rem;
      line-height: 1.25;
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

    .detail-chip {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      padding: 0.52rem 0.58rem;
      display: grid;
      gap: 0.12rem;
    }

    .detail-label {
      color: var(--subtle);
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
      display: flex;
      gap: 0.25rem;
      align-items: center;
    }

    .detail-icon {
      color: var(--listing-strong);
      font-size: 0.78rem;
      line-height: 1;
    }

    .detail-value {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--text);
      font-size: 0.8rem;
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
      justify-content: flex-end;
    }

    .modal-actions .action-button {
      min-height: 2rem;
      background: var(--surface);
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
      --title-box: 3.2rem;
      --meta-box: 1rem;
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
      -webkit-line-clamp: 3;
      font-size: 0.84rem;
      line-height: 1.26;
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
        grid-template-columns: minmax(0, 1fr) minmax(8rem, 10rem);
        grid-template-areas:
          "search search"
          "sort actions";
      }

      .search-control { grid-area: search; }
      .sort-control { grid-area: sort; }
      .toolbar-actions { grid-area: actions; }
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
        --title-box: 3.7rem;
        --meta-box: 1rem;
        grid-template-columns: minmax(0, 1fr);
        grid-template-areas:
          "media"
          "body";
        padding: 0;
      }

      .result-media,
      .results.density-dense .result-media,
      .thumb,
      .results.density-dense .thumb {
        width: 100%;
      }

      .listing-line-title .listing-value,
      .results.density-dense .listing-line-title .listing-value {
        -webkit-line-clamp: 3;
      }

      .result-actions {
        width: 100%;
        grid-template-columns: auto minmax(0, 1fr) auto;
      }

      .result-actions-primary {
        display: contents;
      }

      .result-actions-primary .action-button,
      .result-actions-primary .source-link {
        width: auto;
      }

      .copy-label::after {
        content: attr(data-short);
      }

      button, .source-link {
        white-space: normal;
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
        flex: 1 0 8.75rem;
      }

      .tool-button {
        flex: 1 1 3.25rem;
      }

      .result-actions-primary {
        gap: 0.25rem;
      }

      .results,
      .results.density-dense {
        grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr));
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

      .modal-meta-section {
        grid-template-columns: 1fr;
        gap: 0.45rem;
      }

      .modal-actions {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(5.75rem, 1fr));
        gap: 0.35rem;
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

    @media (max-width: 360px) {
      .results,
      .results.density-dense {
        grid-template-columns: 1fr;
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

    function titleFromListing(item) {
      const listing = truncate(item.listing_text, 220);
      return listing || "Listing #" + text(item.rank, "unknown");
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

    function formattedListingFields(item) {
      return [
        {
          label: "Listing",
          icon: "🏷️",
          copyPrefix: "🏷️  ",
          className: "listing-line-title",
          value: copyField(item.listing_text, "No listing text")
        },
        {
          label: "Seller",
          icon: "👤",
          copyPrefix: "👤 ",
          className: "listing-line-meta listing-line-seller",
          value: copyField(item.seller)
        },
        {
          label: "Posted",
          icon: "📅",
          copyPrefix: "📅 ",
          className: "listing-line-meta listing-line-date",
          value: formatCopyDate(item.posted_date)
        }
      ];
    }

    function formatListingCopy(item) {
      return formattedListingFields(item).map((field) => field.copyPrefix + field.value).join("\\n");
    }

    function createFormattedListingDisplay(item, className = "", titleTag = "h2") {
      const displayClass = ["listing-display", className].filter(Boolean).join(" ");
      const display = createNode("div", displayClass);
      for (const field of formattedListingFields(item)) {
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
      if (item.seller_phone) values.push({ label: "Phone", icon: "☎️", value: text(item.seller_phone) });
      const source = hostName(item.source_url);
      if (source) values.push({ label: "Source", icon: "🔗", value: source });
      return values;
    }

    function createDetailsMeta(item) {
      const meta = createNode("div", "result-details-meta");
      for (const value of detailMeta(item)) {
        const chip = createNode("div", "detail-chip");
        const label = createNode("span", "detail-label");
        const icon = createNode("span", "detail-icon", value.icon);
        icon.setAttribute("aria-hidden", "true");
        label.append(icon, createNode("span", "", value.label));
        const content = createNode("span", "detail-value", value.value);
        content.title = value.value;
        chip.append(label, content);
        meta.appendChild(chip);
      }
      return meta;
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
        if (similarItem.seller_phone) rowMeta.appendChild(createNode("span", "", "☎️ Phone: " + similarItem.seller_phone));
        if (similarItem.source_url) rowMeta.appendChild(createNode("span", "", "🔗 Source: " + hostName(similarItem.source_url)));
        if (rowMeta.childElementCount) row.appendChild(rowMeta);
        panel.appendChild(row);
      }
      panel.dataset.loaded = "true";
    }

    function createOverflowActions(item, similarPanel, className = "") {
      const secondaryClass = ["result-actions-secondary", className].filter(Boolean).join(" ");
      const secondary = createNode("div", secondaryClass);
      secondary.appendChild(makeButton("Copy OpenWA", "Copy prompt to create an OpenWA chat draft", () => copyText(openWaPrompt(item), "OpenWA prompt")));
      secondary.appendChild(makeButton("Copy Report", "Copy prompt to report this result", () => copyText(reportPrompt(item), "Report prompt")));
      const sourceUrl = text(item.source_url).trim();
      if (sourceUrl) {
        secondary.appendChild(makeButton("Copy URL", "Copy source URL", () => copyText(sourceUrl, "Source URL")));
      }
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
        secondary.appendChild(similarButton);
      }
      return secondary;
    }

    function modalKicker(item) {
      const values = ["#" + text(item.rank, "-")];
      if (item.seller) values.push("👤 " + text(item.seller));
      if (item.posted_date) values.push("📅 " + formatCopyDate(item.posted_date));
      return values.join(" | ");
    }

    function modalTitle(item) {
      return "Result #" + text(item.rank, "unknown") + " details";
    }

    function createResultDetailsContent(item) {
      const details = createNode("section", "result-details");
      const listingSection = createNode("section", "modal-section modal-listing-section");
      listingSection.appendChild(createFormattedListingDisplay(item, "listing-display-detail", "div"));
      details.appendChild(listingSection);

      const metaSection = createNode("section", "modal-section modal-meta-section");
      const idWrap = createNode("div", "result-id-wrap");
      const idIcon = createNode("span", "result-id-icon", "🆔");
      idIcon.setAttribute("aria-hidden", "true");
      const idText = createNode("span", "result-id", text(item.result_id, "No result_id"));
      const idCopy = makeButton("Copy ID", "Copy result_id", () => copyText(item.result_id, "Result ID"), "mini-copy");
      idWrap.append(idIcon, idText, idCopy);
      metaSection.appendChild(idWrap);

      const detailsMeta = createDetailsMeta(item);
      if (detailsMeta.childElementCount) metaSection.appendChild(detailsMeta);
      details.appendChild(metaSection);

      const similar = createNode("section", "similar-panel");
      similar.hidden = true;
      similar.id = "modal-similar-" + text(item.rank, "result").replace(/[^a-zA-Z0-9_-]/g, "-");
      if (similarCount(item)) {
        const similarSection = createNode("section", "modal-section modal-similar-section");
        similarSection.appendChild(similar);
        details.appendChild(similarSection);
      }
      const actionsSection = createNode("section", "modal-actions-section");
      actionsSection.appendChild(createOverflowActions(item, similar, "modal-actions"));
      details.appendChild(actionsSection);
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
      const isDesktop = window.matchMedia("(min-width: 761px)").matches;
      if (results && allResults().length > 10 && isDesktop) {
        state.density = "dense";
      }
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

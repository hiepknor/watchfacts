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
      --warning: #8a5a00;
      --danger: #b42318;
      --shadow: 0 1px 2px rgba(17, 24, 23, 0.06);
      --radius: 8px;
    }

    * { box-sizing: border-box; }

    html {
      min-width: 0;
    }

    body {
      margin: 0;
      min-width: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
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
      width: min(1180px, calc(100vw - 2rem));
      margin: 0 auto;
    }

    .page-header {
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }

    .header-inner {
      padding: 0.95rem 0 0.85rem;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(14rem, auto);
      gap: 1rem;
      align-items: end;
    }

    .eyebrow {
      margin-bottom: 0.25rem;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    h1 {
      margin: 0;
      font-size: clamp(1.25rem, 2vw, 1.65rem);
      line-height: 1.15;
      letter-spacing: 0;
    }

    .query {
      display: block;
      overflow-wrap: anywhere;
    }

    .header-meta {
      margin-top: 0.6rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem 0.45rem;
      color: var(--muted);
      font-size: 0.84rem;
    }

    .meta-chip, .fact-chip {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--surface-raised);
      padding: 0.28rem 0.55rem;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      overflow-wrap: anywhere;
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
      gap: 0.5rem;
      min-width: 16rem;
    }

    .summary-item {
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface-raised);
      padding: 0.65rem 0.7rem;
      box-shadow: var(--shadow);
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
      font-size: 1.25rem;
      font-weight: 750;
      line-height: 1.15;
      overflow-wrap: anywhere;
    }

    .summary-value.status-value {
      font-size: 0.95rem;
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
      grid-template-columns: 1fr;
      gap: 0.65rem;
    }

    .result-card {
      display: grid;
      grid-template-columns: 8.75rem minmax(0, 1fr);
      gap: 0.85rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 0.8rem;
      box-shadow: var(--shadow);
    }

    .result-card:hover {
      border-color: var(--border-strong);
    }

    .result-media {
      min-width: 0;
    }

    .thumb {
      width: 100%;
      aspect-ratio: 4 / 3;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--surface-soft);
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 0.78rem;
      text-align: center;
      padding: 0.5rem;
    }

    .thumb img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }

    .result-content {
      min-width: 0;
      display: grid;
      gap: 0.5rem;
    }

    .result-top {
      min-width: 0;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 0.55rem;
      align-items: start;
    }

    .rank-badge {
      border: 1px solid var(--accent);
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent-strong);
      font-weight: 800;
      line-height: 1;
      padding: 0.38rem 0.48rem;
      white-space: nowrap;
    }

    .listing-title {
      margin: 0;
      min-width: 0;
      font-size: 1.02rem;
      line-height: 1.25;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }

    .result-id-wrap {
      max-width: 15rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-raised);
      padding: 0.28rem 0.42rem;
      display: flex;
      gap: 0.35rem;
      align-items: center;
      color: var(--muted);
    }

    .result-id {
      min-width: 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.72rem;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }

    .mini-copy {
      min-height: 1.5rem;
      padding: 0.15rem 0.35rem;
      font-size: 0.72rem;
      flex: 0 0 auto;
    }

    .listing-body {
      margin: 0;
      color: var(--text);
      font-size: 0.92rem;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .facts {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      min-width: 0;
    }

    .fact-chip {
      border-radius: 6px;
      padding: 0.28rem 0.45rem;
      font-size: 0.82rem;
      background: var(--surface-raised);
    }

    .fact-chip.source-chip .fact-value {
      color: var(--accent-strong);
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      min-width: 0;
    }

    .action-button, .source-link {
      min-height: 2rem;
      padding: 0.35rem 0.55rem;
      font-size: 0.84rem;
    }

    .source-link {
      color: var(--accent-strong);
    }

    .similar-panel {
      margin-top: 0.2rem;
      border-top: 1px solid var(--border);
      padding-top: 0.55rem;
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

    .similar-text {
      margin: 0 0 0.3rem;
      font-size: 0.86rem;
      color: var(--text);
      overflow-wrap: anywhere;
    }

    .similar-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.3rem 0.5rem;
      font-size: 0.78rem;
      color: var(--muted);
    }

    .results.density-dense {
      gap: 0.45rem;
    }

    .results.density-dense .result-card {
      grid-template-columns: 6.25rem minmax(0, 1fr);
      gap: 0.65rem;
      padding: 0.58rem 0.65rem;
    }

    .results.density-dense .thumb {
      aspect-ratio: 1;
      font-size: 0.72rem;
    }

    .results.density-dense .result-content {
      gap: 0.35rem;
    }

    .results.density-dense .result-top {
      grid-template-columns: auto minmax(0, 1fr) minmax(7rem, 13rem);
      gap: 0.45rem;
    }

    .results.density-dense .listing-title {
      font-size: 0.95rem;
    }

    .results.density-dense .listing-body {
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      white-space: normal;
      font-size: 0.84rem;
    }

    .results.density-dense .fact-chip {
      padding: 0.2rem 0.38rem;
      font-size: 0.78rem;
    }

    .results.density-dense .action-button,
    .results.density-dense .source-link {
      min-height: 1.85rem;
      padding: 0.25rem 0.45rem;
      font-size: 0.78rem;
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
        width: min(100vw - 1rem, 44rem);
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

      .result-card,
      .results.density-dense .result-card {
        grid-template-columns: 1fr;
        padding: 0.7rem;
      }

      .result-media {
        max-width: 10.5rem;
      }

      .result-top,
      .results.density-dense .result-top {
        grid-template-columns: auto minmax(0, 1fr);
      }

      .result-id-wrap {
        grid-column: 1 / -1;
        max-width: 100%;
      }

      .listing-body,
      .results.density-dense .listing-body {
        display: block;
        overflow: visible;
        white-space: pre-wrap;
        font-size: 0.9rem;
      }

      button, .source-link {
        white-space: normal;
      }
    }

    @media (max-width: 520px) {
      .wrap {
        width: min(100vw - 0.75rem, 44rem);
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
        flex-wrap: nowrap;
        justify-content: flex-start;
        overflow-x: auto;
        padding-bottom: 0.1rem;
        scrollbar-width: thin;
      }

      .density-toggle {
        flex: 0 0 auto;
      }

      .tool-button {
        flex: 0 0 auto;
      }
    }

    @media print {
      .commandbar, .actions, .toast {
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
      <div>
        <div class="eyebrow">WatchFacts search</div>
        <h1><span class="sr-only">Query </span><span class="query" id="queryText">No result payload</span></h1>
        <div class="header-meta" id="resultsMeta" aria-label="Result page metadata">
          <span class="meta-chip"><span class="meta-label">Generated</span><span class="meta-value" id="createdAt">Unknown</span></span>
          <span class="meta-chip"><span class="meta-label">Expires</span><span class="meta-value" id="expiresAt">Unknown</span></span>
          <span class="meta-chip"><span class="meta-label">Page</span><span class="meta-value" id="pageRange">Unavailable</span></span>
        </div>
      </div>
      <div class="summary-panel" aria-label="Result summary">
        <div class="summary-item">
          <span class="summary-label">Total</span>
          <span class="summary-value" id="resultCount">0</span>
        </div>
        <div class="summary-item">
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
      printPage: document.getElementById("printPage")
    };

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

    function appendFact(parent, label, value, extraClass = "") {
      if (!value) return;
      const item = createNode("span", "fact-chip" + (extraClass ? " " + extraClass : ""));
      const labelNode = createNode("span", "fact-label", label);
      const valueNode = createNode("span", "fact-value", value);
      item.append(labelNode, valueNode);
      parent.appendChild(item);
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
      const listing = truncate(item.listing_text, 116);
      return listing || "Listing #" + text(item.rank, "unknown");
    }

    function createThumb(item) {
      const thumb = createNode("div", "thumb");
      if (item.image_url) {
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

    function createResultCard(item) {
      const article = document.createElement("article");
      article.className = "result-card";

      const media = createNode("div", "result-media");
      media.appendChild(createThumb(item));

      const body = createNode("div", "result-content");
      const top = createNode("div", "result-top");
      const rank = createNode("span", "rank-badge", "#" + text(item.rank, "-"));

      const title = createNode("h2", "listing-title", titleFromListing(item));

      const idWrap = createNode("div", "result-id-wrap");
      const idText = createNode("span", "result-id", text(item.result_id, "No result_id"));
      const idCopy = makeButton("Copy", "Copy result_id", () => copyText(item.result_id, "Result ID"), "mini-copy");
      idWrap.append(idText, idCopy);
      top.append(rank, title, idWrap);

      const listing = createNode("p", "listing-body", text(item.listing_text, "No listing text"));

      const facts = createNode("div", "facts");
      appendFact(facts, "Seller", item.seller);
      appendFact(facts, "Posted", item.posted_date);
      appendFact(facts, "Phone", item.seller_phone);
      appendFact(facts, "Source", hostName(item.source_url), "source-chip");

      const actions = createNode("div", "actions");
      actions.appendChild(makeButton("ID", "Copy result_id", () => copyText(item.result_id, "Result ID")));
      actions.appendChild(makeButton("Text", "Copy listing text", () => copyText(item.listing_text, "Listing text")));
      if (item.source_url) {
        actions.appendChild(makeButton("URL", "Copy source URL", () => copyText(item.source_url, "Source URL")));
        const link = document.createElement("a");
        link.className = "source-link";
        link.href = item.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Source";
        actions.appendChild(link);
      }
      actions.appendChild(makeButton("OpenWA", "Copy OpenWA handoff prompt", () => copyText(openWaPrompt(item), "OpenWA prompt")));
      actions.appendChild(makeButton("Report", "Copy issue report prompt", () => copyText(reportPrompt(item), "Report prompt")));

      const similar = createNode("section", "similar-panel");
      similar.hidden = true;
      const similarId = "similar-" + text(item.rank, "result").replace(/[^a-zA-Z0-9_-]/g, "-");
      similar.id = similarId;

      if (Array.isArray(item.similar_results) && item.similar_results.length) {
        const toggle = makeButton("Similar " + item.similar_results.length, "Show similar listings", () => {
          const shouldShow = similar.hidden;
          similar.hidden = !shouldShow;
          toggle.setAttribute("aria-expanded", String(shouldShow));
          toggle.textContent = shouldShow ? "Hide similar" : "Similar " + item.similar_results.length;
          toggle.setAttribute("aria-label", shouldShow ? "Hide similar listings" : "Show similar listings");
          toggle.title = shouldShow ? "Hide similar listings" : "Show similar listings";
        });
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-controls", similarId);
        actions.appendChild(toggle);

        const similarTitle = createNode("h3", "similar-title", "Similar listings");
        similar.appendChild(similarTitle);
        for (const similarItem of item.similar_results) {
          const row = createNode("div", "similar-item");
          const rowText = createNode("p", "similar-text", text(similarItem.listing_text, "No listing text"));
          const rowMeta = createNode("div", "similar-meta");
          if (similarItem.seller) rowMeta.appendChild(createNode("span", "", "Seller: " + similarItem.seller));
          if (similarItem.posted_date) rowMeta.appendChild(createNode("span", "", "Posted: " + similarItem.posted_date));
          if (similarItem.seller_phone) rowMeta.appendChild(createNode("span", "", "Phone: " + similarItem.seller_phone));
          if (similarItem.source_url) rowMeta.appendChild(createNode("span", "", "Source: " + hostName(similarItem.source_url)));
          row.append(rowText, rowMeta);
          similar.appendChild(row);
        }
      }

      body.append(top, listing, facts, actions, similar);
      article.append(media, body);
      return article;
    }

    function openWaPrompt(item) {
      return "Create an OpenWA chat draft for query '" + text(results && results.query) + "' using result_id " + text(item.result_id) + ".";
    }

    function reportPrompt(item) {
      return "Report an issue for query '" + text(results && results.query) + "' using result_id " + text(item.result_id) + ": ";
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

    initializeDensity();
    render();
  </script>
</body>
</html>
"""

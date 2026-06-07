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
      --bg: #f6f5f2;
      --surface: #ffffff;
      --surface-soft: #f0eee9;
      --text: #171717;
      --muted: #66615a;
      --border: #d8d3ca;
      --accent: #0f766e;
      --accent-strong: #0b4f4a;
      --danger: #b42318;
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
      padding: 0.45rem 0.7rem;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.35rem;
      white-space: nowrap;
    }
    button:hover, .source-link:hover {
      border-color: var(--accent);
      color: var(--accent-strong);
    }
    button[aria-pressed="true"] {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    header {
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }
    .wrap {
      width: min(1180px, calc(100vw - 2rem));
      margin: 0 auto;
    }
    .header-inner {
      padding: 1rem 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 1rem;
      align-items: end;
    }
    h1 {
      margin: 0 0 0.35rem;
      font-size: 1.35rem;
      letter-spacing: 0;
    }
    .query {
      display: block;
      overflow-wrap: anywhere;
    }
    .meta {
      color: var(--muted);
      font-size: 0.9rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem 0.85rem;
    }
    .count {
      font-size: 1.8rem;
      font-weight: 700;
      text-align: right;
    }
    .toolbar {
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--border);
      background: rgba(246, 245, 242, 0.96);
      backdrop-filter: blur(8px);
    }
    .toolbar-inner {
      padding: 0.75rem 0;
      display: grid;
      grid-template-columns: minmax(12rem, 1fr) auto auto;
      gap: 0.6rem;
      align-items: center;
    }
    .toolbar-actions {
      display: flex;
      gap: 0.45rem;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    input[type="search"], select {
      min-height: 2.25rem;
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0.45rem 0.65rem;
    }
    .view-toggle {
      display: inline-flex;
      border: 1px solid var(--border);
      border-radius: 7px;
      overflow: hidden;
      background: var(--surface);
    }
    .view-toggle button {
      border: 0;
      border-radius: 0;
    }
    main {
      padding: 1rem 0 2rem;
    }
    .status {
      min-height: 1.5rem;
      color: var(--muted);
      font-size: 0.9rem;
      margin-bottom: 0.75rem;
    }
    .results {
      display: grid;
      grid-template-columns: 1fr;
      gap: 0.75rem;
    }
    .result {
      display: grid;
      grid-template-columns: 8.5rem minmax(0, 1fr);
      gap: 0.85rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 0.85rem;
    }
    .results.compact .result {
      grid-template-columns: 5.5rem minmax(0, 1fr);
      padding: 0.65rem;
    }
    .thumb {
      width: 100%;
      aspect-ratio: 1;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--surface-soft);
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 0.82rem;
      text-align: center;
      padding: 0.5rem;
    }
    .thumb img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .result-head {
      display: flex;
      gap: 0.5rem;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 0.45rem;
    }
    .rank {
      font-weight: 700;
      color: var(--accent-strong);
    }
    .result-id {
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.78rem;
      overflow-wrap: anywhere;
      text-align: right;
    }
    .listing {
      margin: 0 0 0.65rem;
      font-size: 1rem;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }
    .facts {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem 0.7rem;
      color: var(--muted);
      font-size: 0.9rem;
      margin-bottom: 0.7rem;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
    }
    .similar {
      margin-top: 0.7rem;
      border-top: 1px solid var(--border);
      padding-top: 0.7rem;
    }
    .similar[hidden] { display: none; }
    .similar-item {
      padding: 0.55rem 0;
      border-bottom: 1px solid var(--surface-soft);
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .empty {
      border: 1px dashed var(--border);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 2rem;
      text-align: center;
      color: var(--muted);
    }
    .toast {
      position: fixed;
      right: 1rem;
      bottom: 1rem;
      max-width: min(24rem, calc(100vw - 2rem));
      border-radius: 7px;
      background: var(--text);
      color: #fff;
      padding: 0.65rem 0.8rem;
      opacity: 0;
      transform: translateY(0.5rem);
      transition: opacity 150ms ease, transform 150ms ease;
      pointer-events: none;
      z-index: 5;
    }
    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }
    @media (max-width: 760px) {
      .wrap {
        width: min(100vw - 1rem, 44rem);
      }
      .header-inner, .toolbar-inner {
        grid-template-columns: 1fr;
        align-items: stretch;
      }
      .count {
        text-align: left;
        font-size: 1.35rem;
      }
      .toolbar-actions {
        justify-content: flex-start;
      }
      .result, .results.compact .result {
        grid-template-columns: 1fr;
      }
      .thumb {
        max-width: 12rem;
      }
      .result-head {
        display: block;
      }
      .result-id {
        margin-top: 0.2rem;
        text-align: left;
      }
      button, .source-link {
        white-space: normal;
      }
    }
    @media print {
      .toolbar, .actions, .toast {
        display: none;
      }
      body {
        background: #fff;
      }
      .result {
        break-inside: avoid;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap header-inner">
      <div>
        <h1>WatchFacts Results</h1>
        <strong class="query" id="queryText"></strong>
        <div class="meta">
          <span id="createdAt"></span>
          <span id="expiresAt"></span>
        </div>
      </div>
      <div class="count" id="resultCount"></div>
    </div>
  </header>
  <section class="toolbar">
    <div class="wrap toolbar-inner">
      <input id="filterInput" type="search" autocomplete="off" placeholder="Filter results">
      <select id="sortSelect" aria-label="Sort results">
        <option value="rank">Rank</option>
        <option value="posted_desc">Posted date</option>
        <option value="seller">Seller</option>
      </select>
      <div class="toolbar-actions">
        <div class="view-toggle" aria-label="View density">
          <button type="button" id="viewComfortable" aria-pressed="true">List</button>
          <button type="button" id="viewCompact" aria-pressed="false">Dense</button>
        </div>
        <button type="button" id="copyPageLink">Copy link</button>
        <button type="button" id="exportJson">JSON</button>
        <button type="button" id="exportCsv">CSV</button>
        <button type="button" id="printPage">Print</button>
      </div>
    </div>
  </section>
  <main class="wrap">
    <div class="status" id="statusText"></div>
    <section class="results" id="resultsList"></section>
  </main>
  <div class="toast" id="toast" role="status" aria-live="polite"></div>
  <script>
    let results = null;
    results = __WATCHFACTS_RESULTS_PAYLOAD__;

    const state = {
      filter: "",
      sort: "rank",
      compact: false
    };
    const els = {
      query: document.getElementById("queryText"),
      createdAt: document.getElementById("createdAt"),
      expiresAt: document.getElementById("expiresAt"),
      resultCount: document.getElementById("resultCount"),
      filter: document.getElementById("filterInput"),
      sort: document.getElementById("sortSelect"),
      list: document.getElementById("resultsList"),
      status: document.getElementById("statusText"),
      toast: document.getElementById("toast"),
      viewComfortable: document.getElementById("viewComfortable"),
      viewCompact: document.getElementById("viewCompact")
    };

    function text(value, fallback = "") {
      if (value === null || value === undefined || value === "") return fallback;
      return String(value);
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
        rows.push([item.rank, item.result_id, item.listing_text, item.seller, item.posted_date, item.seller_phone, item.source_url]);
      }
      download("watchfacts-results.csv", "text/csv", rows.map(row => row.map(csvEscape).join(",")).join("\\n"));
    }

    function resultText(item) {
      return [
        item.rank,
        item.result_id,
        item.listing_text,
        item.seller,
        item.posted_date,
        item.seller_phone,
        item.source_url
      ].map(value => text(value).toLowerCase()).join(" ");
    }

    function postedTime(value) {
      const parsed = Date.parse(text(value).split("·")[0]);
      return Number.isNaN(parsed) ? 0 : parsed;
    }

    function currentResults() {
      if (!results || !Array.isArray(results.results)) return [];
      const filter = state.filter.trim().toLowerCase();
      let items = results.results.filter(item => !filter || resultText(item).includes(filter));
      if (state.sort === "posted_desc") {
        items = items.slice().sort((a, b) => postedTime(b.posted_date) - postedTime(a.posted_date) || Number(a.rank) - Number(b.rank));
      } else if (state.sort === "seller") {
        items = items.slice().sort((a, b) => text(a.seller).localeCompare(text(b.seller)) || Number(a.rank) - Number(b.rank));
      } else {
        items = items.slice().sort((a, b) => Number(a.rank) - Number(b.rank));
      }
      return items;
    }

    function makeButton(label, title, onClick) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.title = title;
      button.addEventListener("click", onClick);
      return button;
    }

    function appendFact(parent, label, value) {
      if (!value) return;
      const item = document.createElement("span");
      item.textContent = label + ": " + value;
      parent.appendChild(item);
    }

    function createThumb(item) {
      const thumb = document.createElement("div");
      thumb.className = "thumb";
      if (item.image_url) {
        const img = document.createElement("img");
        img.src = item.image_url;
        img.alt = "Listing image";
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

    function createResult(item) {
      const article = document.createElement("article");
      article.className = "result";
      article.appendChild(createThumb(item));

      const body = document.createElement("div");
      const head = document.createElement("div");
      head.className = "result-head";
      const rank = document.createElement("div");
      rank.className = "rank";
      rank.textContent = "#" + text(item.rank);
      const id = document.createElement("div");
      id.className = "result-id";
      id.textContent = text(item.result_id);
      head.append(rank, id);

      const listing = document.createElement("p");
      listing.className = "listing";
      listing.textContent = text(item.listing_text, "No listing text");

      const facts = document.createElement("div");
      facts.className = "facts";
      appendFact(facts, "Seller", item.seller);
      appendFact(facts, "Posted", item.posted_date);
      appendFact(facts, "Phone", item.seller_phone);

      const actions = document.createElement("div");
      actions.className = "actions";
      actions.appendChild(makeButton("Copy ID", "Copy result_id", () => copyText(item.result_id, "Result ID")));
      actions.appendChild(makeButton("Copy text", "Copy listing text", () => copyText(item.listing_text, "Listing text")));
      if (item.source_url) {
        actions.appendChild(makeButton("Copy URL", "Copy source URL", () => copyText(item.source_url, "Source URL")));
        const link = document.createElement("a");
        link.className = "source-link";
        link.href = item.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Open source";
        actions.appendChild(link);
      }
      actions.appendChild(makeButton("OpenWA prompt", "Copy handoff prompt", () => copyText(openWaPrompt(item), "OpenWA prompt")));
      actions.appendChild(makeButton("Report prompt", "Copy issue report prompt", () => copyText(reportPrompt(item), "Report prompt")));

      const similar = document.createElement("div");
      similar.className = "similar";
      similar.hidden = true;
      if (Array.isArray(item.similar_results) && item.similar_results.length) {
        const toggle = makeButton("Similar " + item.similar_results.length, "Show similar listings", () => {
          similar.hidden = !similar.hidden;
        });
        actions.appendChild(toggle);
        for (const similarItem of item.similar_results) {
          const row = document.createElement("div");
          row.className = "similar-item";
          row.textContent = text(similarItem.listing_text, "No listing text");
          similar.appendChild(row);
        }
      }

      body.append(head, listing, facts, actions, similar);
      article.appendChild(body);
      return article;
    }

    function openWaPrompt(item) {
      return "Create an OpenWA chat draft for query '" + text(results.query) + "' using result_id " + text(item.result_id) + ".";
    }

    function reportPrompt(item) {
      return "Report an issue for query '" + text(results.query) + "' using result_id " + text(item.result_id) + ": ";
    }

    function render() {
      if (!results) {
        els.query.textContent = "No result payload";
        els.createdAt.textContent = "";
        els.expiresAt.textContent = "";
        els.resultCount.textContent = "0 results";
        els.status.textContent = "This page has no injected WatchFacts result payload.";
        els.list.replaceChildren(emptyState("No results to display."));
        return;
      }
      els.query.textContent = text(results.query, "Query unavailable");
      els.createdAt.textContent = "Created: " + text(results.created_at, "unknown");
      els.expiresAt.textContent = "Expires: " + text(results.expires_at, "unknown");
      const items = currentResults();
      els.resultCount.textContent = text(results.result_count, "0") + " results";
      els.status.textContent = items.length + " shown";
      els.list.classList.toggle("compact", state.compact);
      if (!items.length) {
        els.list.replaceChildren(emptyState("No matching results."));
        return;
      }
      els.list.replaceChildren(...items.map(createResult));
    }

    function emptyState(message) {
      const node = document.createElement("div");
      node.className = "empty";
      node.textContent = message;
      return node;
    }

    els.filter.addEventListener("input", event => {
      state.filter = event.target.value;
      render();
    });
    els.sort.addEventListener("change", event => {
      state.sort = event.target.value;
      render();
    });
    els.viewComfortable.addEventListener("click", () => {
      state.compact = false;
      els.viewComfortable.setAttribute("aria-pressed", "true");
      els.viewCompact.setAttribute("aria-pressed", "false");
      render();
    });
    els.viewCompact.addEventListener("click", () => {
      state.compact = true;
      els.viewComfortable.setAttribute("aria-pressed", "false");
      els.viewCompact.setAttribute("aria-pressed", "true");
      render();
    });
    document.getElementById("copyPageLink").addEventListener("click", () => copyText(window.location.href, "Page link"));
    document.getElementById("exportJson").addEventListener("click", () => download("watchfacts-results.json", "application/json", JSON.stringify(results, null, 2)));
    document.getElementById("exportCsv").addEventListener("click", exportCsv);
    document.getElementById("printPage").addEventListener("click", () => window.print());
    render();
  </script>
</body>
</html>
"""

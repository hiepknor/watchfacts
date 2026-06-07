from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.result_pages import (
    ResultPageConfig,
    generate_result_page,
    read_result_page_html,
    render_result_page_template,
)
from app.search_result import SearchResult


def make_config(tmp_path, *, public_base_url: str = "https://mcp.example/results"):
    return ResultPageConfig(
        public_base_url=public_base_url,
        ttl_seconds=60,
        max_results=1,
        storage_dir=tmp_path / "pages",
        watchfacts_url="https://watchfacts.example/simon-match-making",
    )


def test_render_result_page_template_defaults_to_null_results() -> None:
    html = render_result_page_template()

    assert "let results = null;" in html


def test_generate_result_page_writes_tokenized_safe_html(tmp_path) -> None:
    config = make_config(tmp_path)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    page = generate_result_page(
        query="5712g </script><script>alert(1)</script>",
        results=[
            SearchResult(
                listing_text="5712G </script><script>alert(1)</script>",
                seller="cookie=secret",
                posted_date="April 9, 2026",
                image_url="/images/5712g.jpg",
                source_url="/listing/5712g",
                raw_listing_text="raw cookie=secret data <html>full source</html>",
                seller_phone="17826241887",
                similar_results=(SearchResult("similar token=secret"),),
            ),
            SearchResult("second result should be bounded out"),
        ],
        offset=0,
        limit=5,
        total_count=2,
        next_offset=None,
        config=config,
        now=now,
    )

    assert page is not None
    assert page.url.startswith("https://mcp.example/results/")
    assert page.expires_at == "2026-06-07T12:01:00Z"
    assert page.result_count == 1

    files = list(config.storage_dir.glob("*.html"))
    assert len(files) == 1
    assert files[0].stem == page.url.rsplit("/", maxsplit=1)[1]
    html = files[0].read_text(encoding="utf-8")

    assert "let results = null;" in html
    assert "5712G \\u003c/script\\u003e\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in html
    assert "https://watchfacts.example/images/5712g.jpg" in html
    assert "https://watchfacts.example/listing/5712g" in html
    assert "raw_listing_text" not in html
    assert "raw cookie=secret" not in html
    assert "cookie=secret" not in html
    assert "token=secret" not in html
    assert "second result should be bounded out" not in html


def test_generate_result_page_returns_none_when_disabled(tmp_path) -> None:
    config = make_config(tmp_path, public_base_url="")

    page = generate_result_page(
        query="5712g",
        results=[SearchResult("5712G")],
        now=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        config=config,
    )

    assert page is None
    assert not config.storage_dir.exists()


def test_read_result_page_html_reports_missing_and_expired_tokens(tmp_path) -> None:
    config = make_config(tmp_path)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    page = generate_result_page(
        query="5712g",
        results=[SearchResult("5712G")],
        now=now,
        config=config,
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]

    found = read_result_page_html(token, config=config, now=now + timedelta(seconds=30))
    assert found.status_code == 200
    assert found.html is not None

    missing = read_result_page_html("missing-token", config=config, now=now)
    assert missing.status_code == 404
    assert missing.html is None

    expired = read_result_page_html(token, config=config, now=now + timedelta(seconds=61))
    assert expired.status_code == 410
    assert expired.html is None
    assert not (config.storage_dir / f"{token}.html").exists()

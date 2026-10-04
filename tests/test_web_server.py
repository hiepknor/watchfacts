from __future__ import annotations

from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from app.config import load_search_settings
from app import web_server
from app.result_pages import generate_result_page, read_result_page_action_payload
from app.search_result import SearchResult


def test_healthz_reports_web_service() -> None:
    response = TestClient(web_server.app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "watchfacts-web"}


def test_retired_openwa_action_is_not_routed() -> None:
    response = TestClient(web_server.app).post(
        "/results/token/actions/openwa-draft",
        json={},
    )

    assert response.status_code == 404


def test_result_page_route_serves_generated_html(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://watchfacts.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    monkeypatch.setattr(web_server, "load_search_settings", lambda: settings)

    response = TestClient(web_server.app).get(f"/results/{token}")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "5712G" in response.text


def test_result_page_route_reports_missing_and_expired(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://watchfacts.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "1",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    monkeypatch.setattr(web_server, "load_search_settings", lambda: settings)
    client = TestClient(web_server.app)

    assert client.get("/results/missing-token").status_code == 404
    assert client.get(f"/results/{token}").status_code == 410


def test_result_page_route_is_disabled_without_public_base_url(
    monkeypatch,
    tmp_path,
) -> None:
    enabled_settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://watchfacts.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=enabled_settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    disabled_settings = load_search_settings(
        env={
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    monkeypatch.setattr(web_server, "load_search_settings", lambda: disabled_settings)

    response = TestClient(web_server.app).get(f"/results/{token}")

    assert response.status_code == 404


def test_result_page_report_action_records_feedback_issue(
    monkeypatch,
    tmp_path,
) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://watchfacts.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
            "DB_PATH": str(tmp_path / "bot.db"),
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [
            SearchResult(
                "5712G blue 2015 full set",
                seller="Seller One",
                posted_date="June 1, 2026",
                source_url="/listing/5712g",
            )
        ],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    action_page = read_result_page_action_payload(token, settings=settings)
    assert action_page.payload is not None
    result_id = action_page.payload["results"][0]["result_id"]
    monkeypatch.setattr(web_server, "load_search_settings", lambda: settings)

    response = TestClient(web_server.app).post(
        f"/results/{token}/actions/report",
        json={
            "action_nonce": action_page.action_nonce,
            "result_id": result_id,
            "reason": "wrong_result",
            "notes": "bad reference",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "recorded"
    assert payload["result_id"] == result_id
    assert payload["issue_ref"] == "F1"
    assert payload["issue"] == {
        "id": 1,
        "issue_type": "feedback",
        "status": "open",
        "reason": "wrong_result",
    }


def test_result_page_action_rejects_invalid_nonce(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://watchfacts.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    action_page = read_result_page_action_payload(token, settings=settings)
    assert action_page.payload is not None
    result_id = action_page.payload["results"][0]["result_id"]
    monkeypatch.setattr(web_server, "load_search_settings", lambda: settings)

    response = TestClient(web_server.app).post(
        f"/results/{token}/actions/report",
        json={
            "action_nonce": "wrong",
            "result_id": result_id,
            "reason": "wrong_result",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "ok": False,
        "error": "invalid_nonce",
        "message": "Invalid result page action nonce.",
    }


def test_result_page_action_is_rate_limited(monkeypatch, tmp_path) -> None:
    settings = load_search_settings(
        env={
            "RESULT_PAGE_PUBLIC_BASE_URL": "https://watchfacts.example/results",
            "RESULT_PAGE_STORAGE_DIR": str(tmp_path / "pages"),
            "RESULT_PAGE_TTL_SECONDS": "60",
            "RESULT_PAGE_RATE_LIMIT_MAX_REQUESTS": "1",
            "RESULT_PAGE_RATE_LIMIT_WINDOW_SECONDS": "60",
            "RESULT_PAGE_RATE_LIMIT_BLOCK_SECONDS": "30",
        },
        project_root=tmp_path,
    )
    page = generate_result_page(
        "5712g",
        [SearchResult("5712G")],
        settings=settings,
        now=datetime.now(timezone.utc),
    )
    assert page is not None
    token = page.url.rsplit("/", maxsplit=1)[1]
    action_page = read_result_page_action_payload(token, settings=settings)
    assert action_page.payload is not None
    result_id = action_page.payload["results"][0]["result_id"]
    monkeypatch.setattr(web_server, "load_search_settings", lambda: settings)
    client = TestClient(web_server.app)
    body = {
        "action_nonce": action_page.action_nonce,
        "result_id": result_id,
        "reason": "wrong_result",
    }

    assert client.post(f"/results/{token}/actions/report", json=body).status_code == 200
    limited = client.post(f"/results/{token}/actions/report", json=body)

    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"

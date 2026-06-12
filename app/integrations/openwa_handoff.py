from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import Settings


DEFAULT_TIMEOUT_SECONDS = 10


class OpenWAHandoffError(RuntimeError):
    """Raised when a chat draft cannot be created in OpenWA."""


class OpenWAHandoffConfigError(OpenWAHandoffError):
    """Raised when OpenWA chat draft handoff is disabled or missing configuration."""


class OpenWAHandoffResponseError(OpenWAHandoffError):
    """Raised when OpenWA returns an unusable response."""


@dataclass(frozen=True)
class OpenWAHandoffConfig:
    base_url: str
    api_key: str
    dashboard_url: str
    chat_draft_endpoint: str
    enabled: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenWAHandoffConfig":
        return cls(
            base_url=settings.openwa_base_url,
            api_key=settings.openwa_api_key,
            dashboard_url=settings.openwa_dashboard_url,
            chat_draft_endpoint=settings.openwa_chat_draft_endpoint,
            enabled=settings.enable_openwa_chat_handoff,
        )

    @property
    def is_ready(self) -> bool:
        return bool(
            self.enabled
            and self.base_url
            and self.api_key
            and self.dashboard_url
            and self.chat_draft_endpoint
        )

    @property
    def chat_draft_url(self) -> str:
        return f"{self.base_url}{self.chat_draft_endpoint}"

    def dashboard_chat_draft_url(self, draft_id: str) -> str:
        return self.resolve_dashboard_url(f"/chats/drafts/{draft_id}")

    def resolve_dashboard_url(self, dashboard_url: str) -> str:
        normalized = dashboard_url.strip()
        parsed_url = urllib.parse.urlparse(normalized)
        if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
            return normalized
        return f"{self.dashboard_url.rstrip('/')}/{normalized.lstrip('/')}"


@dataclass(frozen=True)
class OpenWAChatDraftResponse:
    draft_id: str
    chat_id: str | None
    dashboard_url: str


async def create_openwa_chat_draft(
    config: OpenWAHandoffConfig,
    payload: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> OpenWAChatDraftResponse:
    if not config.is_ready:
        raise OpenWAHandoffConfigError("OpenWA chat draft handoff is not configured")

    return await asyncio.to_thread(
        _post_openwa_chat_draft,
        config,
        payload,
        timeout_seconds,
    )


def _post_openwa_chat_draft(
    config: OpenWAHandoffConfig,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> OpenWAChatDraftResponse:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        config.chat_draft_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": config.api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace").strip()
        detail = f": {error_body[:500]}" if error_body else ""
        raise OpenWAHandoffResponseError(f"OpenWA returned HTTP {exc.code}{detail}") from exc
    except urllib.error.URLError as exc:
        raise OpenWAHandoffError("OpenWA is not reachable") from exc
    except TimeoutError as exc:
        raise OpenWAHandoffError("OpenWA request timed out") from exc

    if status < 200 or status >= 300:
        raise OpenWAHandoffResponseError(f"OpenWA returned HTTP {status}")

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise OpenWAHandoffResponseError("OpenWA returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise OpenWAHandoffResponseError("OpenWA returned invalid JSON")

    draft_id = str(data.get("draftId") or data.get("chatDraftId") or "").strip()
    chat_id_value = data.get("chatId")
    chat_id = str(chat_id_value).strip() if chat_id_value is not None else None
    if chat_id == "":
        chat_id = None
    dashboard_url = str(data.get("dashboardUrl") or "").strip()
    if not draft_id:
        raise OpenWAHandoffResponseError("OpenWA response is missing draftId")
    if not dashboard_url:
        dashboard_url = config.dashboard_chat_draft_url(draft_id)
    else:
        dashboard_url = config.resolve_dashboard_url(dashboard_url)
    return OpenWAChatDraftResponse(
        draft_id=draft_id,
        chat_id=chat_id,
        dashboard_url=dashboard_url,
    )

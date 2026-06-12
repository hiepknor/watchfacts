from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.integrations.openwa_handoff import (
    OpenWAChatDraftResponse,
    OpenWAHandoffConfig,
    create_openwa_chat_draft,
)


ChatDraftClient = Callable[[dict[str, Any]], Awaitable[OpenWAChatDraftResponse]]


@dataclass(frozen=True)
class OpenWAHandoffUseCase:
    config: OpenWAHandoffConfig
    client: ChatDraftClient | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client: ChatDraftClient | None = None,
    ) -> "OpenWAHandoffUseCase":
        return cls(config=OpenWAHandoffConfig.from_settings(settings), client=client)

    async def create_chat_draft(
        self,
        payload: dict[str, Any],
    ) -> OpenWAChatDraftResponse:
        client = self.client or (
            lambda draft_payload: create_openwa_chat_draft(self.config, draft_payload)
        )
        return await client(payload)

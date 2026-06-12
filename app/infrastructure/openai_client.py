from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import Settings


@dataclass(frozen=True)
class OpenAIResponsesClient:
    api_key: str
    model: str
    timeout_seconds: int
    base_url: str = "https://api.openai.com/v1/responses"

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIResponsesClient":
        return cls(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        schema_name: str,
        schema: dict[str, Any],
        error_message: str = "OpenAI request failed",
    ) -> str:
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_output_tokens": max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as exc:
            raise RuntimeError(error_message) from exc

        return self.extract_response_text(data)

    @staticmethod
    def extract_response_text(data: dict[str, Any]) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str):
            return output_text

        output = data.get("output")
        if not isinstance(output, list):
            raise ValueError("OpenAI response missing output")
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    return text
        raise ValueError("OpenAI response missing output text")

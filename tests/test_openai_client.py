from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from app.infrastructure import OpenAIResponsesClient


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_openai_responses_client_posts_schema_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHTTPResponse({"output_text": '{"ok": true}'})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAIResponsesClient(
        api_key="sk-test",
        model="test-model",
        timeout_seconds=7,
    )

    response = client.complete_json(
        system_prompt="system",
        user_prompt="user",
        max_output_tokens=123,
        schema_name="watchfacts_test",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    assert response == '{"ok": true}'
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["timeout"] == 7
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"] == {
        "model": "test-model",
        "input": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "max_output_tokens": 123,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "watchfacts_test",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
        },
    }


def test_extract_response_text_accepts_nested_output_shape() -> None:
    data = {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"nested": true}',
                    }
                ]
            }
        ]
    }

    assert OpenAIResponsesClient.extract_response_text(data) == '{"nested": true}'


def test_openai_responses_client_translates_url_errors(monkeypatch) -> None:
    def fail_urlopen(*args, **kwargs):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    client = OpenAIResponsesClient(
        api_key="sk-test",
        model="test-model",
        timeout_seconds=7,
    )

    with pytest.raises(RuntimeError, match="OpenAI request failed"):
        client.complete_json(
            system_prompt="system",
            user_prompt="user",
            max_output_tokens=123,
            schema_name="watchfacts_test",
            schema={"type": "object"},
        )

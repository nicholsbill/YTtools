# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""Tests for the LLM provider abstraction. Ollama HTTP is mocked with httpx."""

from __future__ import annotations

import json

import httpx
import pytest

from yttools.config import Settings
from yttools.core.llm import (
    HOSTED_PROVIDER_CLASSES,
    PROVIDER_NAMES,
    AnthropicProvider,
    GeminiProvider,
    LLMError,
    OllamaProvider,
    OpenAIProvider,
    ProviderHealth,
    build_providers,
    get_provider,
)


def _ollama(handler) -> OllamaProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider(client=client)


def _hosted(provider_cls, handler, *, api_key: str = "key-123"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider_cls("model-x", api_key, client=client)


async def test_complete_returns_content() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "the answer"}})

    provider = _ollama(handler)
    result = await provider.complete("question", system="be terse")
    assert result == "the answer"
    assert captured["url"].endswith("/api/chat")
    body = captured["body"]
    assert body["messages"][0]["role"] == "system"
    assert body["stream"] is False


async def test_complete_sets_json_format() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "{}"}})

    provider = _ollama(handler)
    await provider.complete("q", response_format="json")
    assert captured["body"]["format"] == "json"


async def test_stream_yields_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        lines = [
            json.dumps({"message": {"content": "hello "}}),
            json.dumps({"message": {"content": "world"}}),
            json.dumps({"done": True}),
        ]
        return httpx.Response(200, content="\n".join(lines).encode())

    provider = _ollama(handler)
    pieces = [piece async for piece in provider.stream("q")]
    assert "".join(pieces) == "hello world"


async def test_embed_returns_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    provider = _ollama(handler)
    vectors = await provider.embed(["a", "b"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


async def test_embed_falls_back_to_legacy_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/api/embeddings":
            return httpx.Response(200, json={"embedding": [0.5, 0.6]})
        return httpx.Response(404)

    vectors = await _ollama(handler).embed(["a"])
    assert vectors == [[0.5, 0.6]]


async def test_embed_surfaces_model_not_found_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": "model 'nomic-embed-text' not found, try pulling it first"}
        )

    with pytest.raises(LLMError, match="not found"):
        await _ollama(handler).embed(["a"])


async def test_health_check_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

    health = await _ollama(handler).health_check()
    assert health.available is True
    assert "llama3.1:8b" in health.models


async def test_health_check_unavailable_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    health = await _ollama(handler).health_check()
    assert health.available is False
    assert "Could not reach" in health.message


async def test_anthropic_complete_parses_text_blocks() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi from claude"}]})

    provider = _hosted(AnthropicProvider, handler)
    result = await provider.complete("q")
    assert result == "hi from claude"
    assert captured["headers"]["x-api-key"] == "key-123"
    assert str(captured["url"]).endswith("/messages")


async def test_anthropic_json_mode_appends_instruction() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}]})

    provider = _hosted(AnthropicProvider, handler)
    await provider.complete("q", response_format="json")
    assert "JSON" in captured["body"]["system"]


async def test_openai_complete_and_json_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "an article"}}]})

    provider = _hosted(OpenAIProvider, handler)
    result = await provider.complete("q", system="be terse", response_format="json")
    assert result == "an article"
    assert captured["auth"] == "Bearer key-123"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["messages"][0]["role"] == "system"


async def test_openai_stream_yields_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices": [{"delta": {"content": "hello "}}]}\n'
            'data: {"choices": [{"delta": {"content": "world"}}]}\n'
            "data: [DONE]\n"
        )
        return httpx.Response(200, content=body.encode())

    provider = _hosted(OpenAIProvider, handler)
    pieces = [piece async for piece in provider.stream("q")]
    assert "".join(pieces) == "hello world"


async def test_openai_embed_returns_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    vectors = await _hosted(OpenAIProvider, handler).embed(["a"])
    assert vectors == [[0.1, 0.2]]


async def test_gemini_complete_uses_key_param() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.url.params.get("key")
        captured["path"] = request.url.path
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "gemini text"}]}}]}
        )

    result = await _hosted(GeminiProvider, handler).complete("q")
    assert result == "gemini text"
    assert captured["key"] == "key-123"
    assert str(captured["path"]).endswith(":generateContent")


async def test_anthropic_embed_unsupported() -> None:
    with pytest.raises(LLMError):
        await AnthropicProvider("m", "key").embed(["a"])


async def test_hosted_status_error_becomes_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(LLMError):
        await _hosted(OpenAIProvider, handler).complete("q")


@pytest.mark.parametrize("provider_cls", HOSTED_PROVIDER_CLASSES)
async def test_hosted_health_no_key_skips_network(provider_cls) -> None:
    health = await provider_cls("m", "").health_check()
    assert health.available is False
    assert "no api key" in health.message.lower()


async def test_openai_health_lists_models_with_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})

    health = await _hosted(OpenAIProvider, handler).health_check()
    assert health.available is True
    assert "gpt-4o" in health.models


def test_build_providers_covers_all() -> None:
    providers = build_providers(Settings())
    assert set(providers) == set(PROVIDER_NAMES)
    assert isinstance(providers[PROVIDER_NAMES[0]], OllamaProvider)


def test_get_provider_defaults_to_ollama() -> None:
    provider = get_provider(Settings())
    assert provider.name == "ollama"


def test_provider_health_model() -> None:
    health = ProviderHealth(name="ollama", available=True)
    assert health.models == []

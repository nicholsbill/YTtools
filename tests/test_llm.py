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
    OllamaProvider,
    ProviderHealth,
    build_providers,
    get_provider,
)


def _ollama(handler) -> OllamaProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider(client=client)


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


@pytest.mark.parametrize("provider_cls", HOSTED_PROVIDER_CLASSES)
async def test_hosted_complete_not_implemented(provider_cls) -> None:
    provider = provider_cls("some-model", "key-123")
    with pytest.raises(NotImplementedError):
        await provider.complete("q")
    with pytest.raises(NotImplementedError):
        await provider.embed(["q"])


async def test_hosted_stream_not_implemented() -> None:
    provider = HOSTED_PROVIDER_CLASSES[0]("m", "k")
    with pytest.raises(NotImplementedError):
        async for _ in provider.stream("q"):
            pass


async def test_hosted_health_reflects_key() -> None:
    provider_cls = HOSTED_PROVIDER_CLASSES[0]
    with_key = await provider_cls("m", "secret").health_check()
    without_key = await provider_cls("m", "").health_check()
    assert with_key.available is False
    assert "configured" in with_key.message.lower()
    assert "no api key" in without_key.message.lower()


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

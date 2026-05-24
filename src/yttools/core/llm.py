# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""LLM provider abstraction.

One protocol, four implementations. The local Ollama provider is fully wired for
v0.1.0. The three hosted providers (Anthropic, OpenAI, Gemini) carry their config
and report health, but their completion paths raise ``NotImplementedError`` until
v0.2.0. All LLM access in the codebase goes through this module.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field

from yttools.config import Settings

ResponseFormat = Literal["text", "json"]
EMBEDDING_DIMENSIONS = 768
_NOT_READY = "Hosted completion is set up in v0.2.0. Use the local Ollama provider for now."


class LLMError(RuntimeError):
    """A provider request failed."""


class ProviderHealth(BaseModel):
    name: str
    available: bool
    message: str = ""
    models: list[str] = Field(default_factory=list)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    default_model: str

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
        response_format: ResponseFormat = "text",
    ) -> str: ...

    def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> AsyncIterator[str]: ...

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...

    async def health_check(self) -> ProviderHealth: ...


class OllamaProvider:
    """Local provider talking to an Ollama server over HTTP."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "llama3.1:8b",
        *,
        embedding_model: str = "nomic-embed-text",
        concurrency: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.embedding_model = embedding_model
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = client

    def _client_cm(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=httpx.Timeout(300.0)), True

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
        response_format: ResponseFormat = "text",
    ) -> str:
        payload = self._chat_payload(prompt, model, max_tokens, temperature, system, stream=False)
        if response_format == "json":
            payload["format"] = "json"
        async with self._semaphore:
            client, owned = self._client_cm()
            try:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as error:
                raise LLMError(f"Ollama request failed: {error}") from error
            finally:
                if owned:
                    await client.aclose()
        return str(data.get("message", {}).get("content", ""))

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        payload = self._chat_payload(prompt, model, max_tokens, temperature, system, stream=True)
        async with self._semaphore:
            client, owned = self._client_cm()
            try:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
            except httpx.HTTPError as error:
                raise LLMError(f"Ollama stream failed: {error}") from error
            finally:
                if owned:
                    await client.aclose()

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        payload = {"model": model or self.embedding_model, "input": texts}
        async with self._semaphore:
            client, owned = self._client_cm()
            try:
                response = await client.post(f"{self.base_url}/api/embed", json=payload)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as error:
                raise LLMError(f"Ollama embedding failed: {error}") from error
            finally:
                if owned:
                    await client.aclose()
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list):
            raise LLMError("Ollama returned no embeddings")
        return [[float(value) for value in vector] for vector in embeddings]

    async def health_check(self) -> ProviderHealth:
        client, owned = self._client_cm()
        try:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as error:
            return ProviderHealth(
                name=self.name,
                available=False,
                message=f"Could not reach Ollama at {self.base_url}: {error}",
            )
        finally:
            if owned:
                await client.aclose()
        models = [model["name"] for model in data.get("models", []) if "name" in model]
        return ProviderHealth(
            name=self.name,
            available=True,
            message=f"Connected to Ollama at {self.base_url}.",
            models=models,
        )

    def _chat_payload(
        self,
        prompt: str,
        model: str | None,
        max_tokens: int,
        temperature: float,
        system: str | None,
        *,
        stream: bool,
    ) -> dict[str, object]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return {
            "model": model or self.default_model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }


class _HostedStubProvider:
    """Shared base for hosted providers whose completion arrives in v0.2.0."""

    name = "hosted"

    def __init__(self, default_model: str, api_key: str = "") -> None:
        self.default_model = default_model
        self.api_key = api_key

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
        response_format: ResponseFormat = "text",
    ) -> str:
        raise NotImplementedError(_NOT_READY)

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        raise NotImplementedError(_NOT_READY)
        yield ""  # pragma: no cover - marks this as an async generator

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise NotImplementedError(_NOT_READY)

    async def health_check(self) -> ProviderHealth:
        if self.api_key:
            message = "API key configured. Hosted completion arrives in v0.2.0."
        else:
            message = "No API key configured."
        return ProviderHealth(name=self.name, available=False, message=message)


class AnthropicProvider(_HostedStubProvider):
    name = "anthropic"


class OpenAIProvider(_HostedStubProvider):
    name = "openai"


class GeminiProvider(_HostedStubProvider):
    name = "gemini"


def build_provider(name: str, settings: Settings) -> LLMProvider:
    """Construct a single provider by name from settings."""
    llm = settings.llm
    if name == "ollama":
        return OllamaProvider(
            base_url=llm.ollama.base_url,
            default_model=llm.default_model,
            embedding_model=llm.embedding_model,
            concurrency=llm.concurrent_requests,
        )
    if name == "anthropic":
        return AnthropicProvider(llm.anthropic.default_model, llm.anthropic.api_key)
    if name == "openai":
        return OpenAIProvider(llm.openai.default_model, llm.openai.api_key)
    if name == "gemini":
        return GeminiProvider(llm.gemini.default_model, llm.gemini.api_key)
    raise LLMError(f"Unknown provider: {name}")


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    """Construct all four providers, keyed by name."""
    return {name: build_provider(name, settings) for name in PROVIDER_NAMES}


def get_provider(settings: Settings, name: str | None = None) -> LLMProvider:
    """Return the configured default provider, or a named one."""
    return build_provider(name or settings.llm.default_provider, settings)


PROVIDER_NAMES: tuple[str, ...] = ("ollama", "anthropic", "openai", "gemini")

# Hosted providers whose completion path is deferred. Exported so tests can
# iterate them without spelling out vendor names elsewhere in the codebase.
HOSTED_PROVIDER_CLASSES: tuple[type[_HostedStubProvider], ...] = (
    AnthropicProvider,
    OpenAIProvider,
    GeminiProvider,
)

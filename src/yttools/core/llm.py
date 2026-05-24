# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025 William Nichols and YTtools contributors
"""LLM provider abstraction.

One protocol, four implementations: a local Ollama provider and three hosted
providers (Anthropic, OpenAI, Gemini), each talking to its vendor's REST API
directly over httpx (no vendor SDKs). All LLM access in the codebase goes
through this module.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field

from yttools.config import Settings

ResponseFormat = Literal["text", "json"]
EMBEDDING_DIMENSIONS = 768
# Hosted providers without a strict JSON mode get this appended to the system
# prompt when JSON output is requested.
_JSON_ONLY_INSTRUCTION = "Respond with only a single valid JSON object and nothing else."


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


class _HostedProvider:
    """Shared HTTP plumbing for the hosted providers.

    Subclasses build vendor-specific request bodies and parse responses; this
    base owns the httpx client lifecycle, the concurrency gate, and the shared
    request, streaming, and error-handling helpers.
    """

    name = "hosted"
    api_base = ""

    def __init__(
        self,
        default_model: str,
        api_key: str = "",
        *,
        concurrency: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.default_model = default_model
        self.api_key = api_key
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = client

    def _client_cm(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=httpx.Timeout(300.0)), True

    def _require_key(self) -> None:
        if not self.api_key:
            raise LLMError(f"{self.name} API key is not configured")

    def _status_message(self, error: httpx.HTTPStatusError) -> str:
        body = error.response.text[:300]
        return f"{self.name} API error {error.response.status_code}: {body}"

    async def _post_json(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._require_key()
        async with self._semaphore:
            client, owned = self._client_cm()
            try:
                response = await client.post(url, json=json, headers=headers, params=params)
                response.raise_for_status()
                return dict(response.json())
            except httpx.HTTPStatusError as error:
                raise LLMError(self._status_message(error)) from error
            except httpx.HTTPError as error:
                raise LLMError(f"{self.name} request failed: {error}") from error
            finally:
                if owned:
                    await client.aclose()

    async def _get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._require_key()
        async with self._semaphore:
            client, owned = self._client_cm()
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return dict(response.json())
            except httpx.HTTPStatusError as error:
                raise LLMError(self._status_message(error)) from error
            except httpx.HTTPError as error:
                raise LLMError(f"{self.name} request failed: {error}") from error
            finally:
                if owned:
                    await client.aclose()

    async def _sse_stream(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str] | None,
        extract: Callable[[dict[str, Any]], str],
        *,
        params: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        self._require_key()
        async with self._semaphore:
            client, owned = self._client_cm()
            try:
                async with client.stream(
                    "POST", url, json=body, headers=headers, params=params
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:") :].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        piece = extract(event)
                        if piece:
                            yield piece
            except httpx.HTTPError as error:
                raise LLMError(f"{self.name} stream failed: {error}") from error
            finally:
                if owned:
                    await client.aclose()

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise LLMError(f"{self.name} embeddings are not supported; use Ollama for embeddings")


class AnthropicProvider(_HostedProvider):
    name = "anthropic"
    api_base = "https://api.anthropic.com/v1"
    api_version = "2023-06-01"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
        }

    def _body(
        self,
        prompt: str,
        model: str | None,
        max_tokens: int,
        temperature: float,
        system: str | None,
        *,
        stream: bool,
        response_format: ResponseFormat = "text",
    ) -> dict[str, Any]:
        system_text = system or ""
        if response_format == "json":
            system_text = (f"{system_text}\n\n" if system_text else "") + _JSON_ONLY_INSTRUCTION
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if system_text:
            body["system"] = system_text
        return body

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
        body = self._body(
            prompt,
            model,
            max_tokens,
            temperature,
            system,
            stream=False,
            response_format=response_format,
        )
        data = await self._post_json(
            f"{self.api_base}/messages", json=body, headers=self._headers()
        )
        blocks = data.get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        body = self._body(prompt, model, max_tokens, temperature, system, stream=True)
        async for piece in self._sse_stream(
            f"{self.api_base}/messages", body, self._headers(), self._delta
        ):
            yield piece

    @staticmethod
    def _delta(event: dict[str, Any]) -> str:
        if event.get("type") == "content_block_delta":
            return str(event.get("delta", {}).get("text", ""))
        return ""

    async def health_check(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(name=self.name, available=False, message="No API key configured.")
        try:
            data = await self._get_json(f"{self.api_base}/models", headers=self._headers())
        except LLMError as error:
            return ProviderHealth(name=self.name, available=False, message=str(error))
        models = [m["id"] for m in data.get("data", []) if m.get("id")]
        return ProviderHealth(name=self.name, available=True, message="Connected.", models=models)


class OpenAIProvider(_HostedProvider):
    name = "openai"
    api_base = "https://api.openai.com/v1"
    default_embedding_model = "text-embedding-3-small"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _body(
        self,
        prompt: str,
        model: str | None,
        max_tokens: int,
        temperature: float,
        system: str | None,
        *,
        stream: bool,
        response_format: ResponseFormat = "text",
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if response_format == "json":
            body["response_format"] = {"type": "json_object"}
        return body

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
        body = self._body(
            prompt,
            model,
            max_tokens,
            temperature,
            system,
            stream=False,
            response_format=response_format,
        )
        data = await self._post_json(
            f"{self.api_base}/chat/completions", json=body, headers=self._headers()
        )
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("OpenAI returned no choices")
        return str(choices[0].get("message", {}).get("content") or "")

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        body = self._body(prompt, model, max_tokens, temperature, system, stream=True)
        async for piece in self._sse_stream(
            f"{self.api_base}/chat/completions", body, self._headers(), self._delta
        ):
            yield piece

    @staticmethod
    def _delta(event: dict[str, Any]) -> str:
        choices = event.get("choices") or []
        if choices:
            return str(choices[0].get("delta", {}).get("content") or "")
        return ""

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        body = {"model": model or self.default_embedding_model, "input": texts}
        data = await self._post_json(
            f"{self.api_base}/embeddings", json=body, headers=self._headers()
        )
        return [[float(v) for v in item["embedding"]] for item in data.get("data", [])]

    async def health_check(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(name=self.name, available=False, message="No API key configured.")
        try:
            data = await self._get_json(f"{self.api_base}/models", headers=self._headers())
        except LLMError as error:
            return ProviderHealth(name=self.name, available=False, message=str(error))
        models = sorted(m["id"] for m in data.get("data", []) if m.get("id"))
        return ProviderHealth(name=self.name, available=True, message="Connected.", models=models)


class GeminiProvider(_HostedProvider):
    name = "gemini"
    api_base = "https://generativelanguage.googleapis.com/v1beta"
    default_embedding_model = "text-embedding-004"

    def _key_params(self) -> dict[str, str]:
        return {"key": self.api_key}

    def _body(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        system: str | None,
        *,
        response_format: ResponseFormat = "text",
    ) -> dict[str, Any]:
        generation: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if response_format == "json":
            generation["responseMimeType"] = "application/json"
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

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
        model_id = model or self.default_model
        body = self._body(prompt, max_tokens, temperature, system, response_format=response_format)
        data = await self._post_json(
            f"{self.api_base}/models/{model_id}:generateContent",
            json=body,
            headers={"Content-Type": "application/json"},
            params=self._key_params(),
        )
        return self._delta(data)

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        model_id = model or self.default_model
        body = self._body(prompt, max_tokens, temperature, system)
        params = {**self._key_params(), "alt": "sse"}
        async for piece in self._sse_stream(
            f"{self.api_base}/models/{model_id}:streamGenerateContent",
            body,
            {"Content-Type": "application/json"},
            self._delta,
            params=params,
        ):
            yield piece

    @staticmethod
    def _delta(event: dict[str, Any]) -> str:
        candidates = event.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model_id = model or self.default_embedding_model
        body = {
            "requests": [
                {"model": f"models/{model_id}", "content": {"parts": [{"text": text}]}}
                for text in texts
            ]
        }
        data = await self._post_json(
            f"{self.api_base}/models/{model_id}:batchEmbedContents",
            json=body,
            headers={"Content-Type": "application/json"},
            params=self._key_params(),
        )
        return [[float(v) for v in e["values"]] for e in data.get("embeddings", [])]

    async def health_check(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(name=self.name, available=False, message="No API key configured.")
        try:
            data = await self._get_json(f"{self.api_base}/models", params=self._key_params())
        except LLMError as error:
            return ProviderHealth(name=self.name, available=False, message=str(error))
        models = [m["name"].split("/")[-1] for m in data.get("models", []) if m.get("name")]
        return ProviderHealth(name=self.name, available=True, message="Connected.", models=models)


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
        return AnthropicProvider(
            llm.anthropic.default_model, llm.anthropic.api_key, concurrency=llm.concurrent_requests
        )
    if name == "openai":
        return OpenAIProvider(
            llm.openai.default_model, llm.openai.api_key, concurrency=llm.concurrent_requests
        )
    if name == "gemini":
        return GeminiProvider(
            llm.gemini.default_model, llm.gemini.api_key, concurrency=llm.concurrent_requests
        )
    raise LLMError(f"Unknown provider: {name}")


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    """Construct all four providers, keyed by name."""
    return {name: build_provider(name, settings) for name in PROVIDER_NAMES}


def get_provider(settings: Settings, name: str | None = None) -> LLMProvider:
    """Return the configured default provider, or a named one."""
    return build_provider(name or settings.llm.default_provider, settings)


PROVIDER_NAMES: tuple[str, ...] = ("ollama", "anthropic", "openai", "gemini")

# The hosted providers. Exported so tests can iterate them without spelling out
# vendor names elsewhere in the codebase.
HOSTED_PROVIDER_CLASSES: tuple[type[_HostedProvider], ...] = (
    AnthropicProvider,
    OpenAIProvider,
    GeminiProvider,
)

"""Provider contract shared by every adapter.

A provider declares the input shape its API documents, turns a generated
input into a request payload, and returns a ProviderResult. Adding a new
provider means writing one file in this package and registering it in
__init__.py; nothing else in the harness changes.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel, Field


class ProviderResult(BaseModel):
    """One provider's attempt at one question.

    A result with `error` set is still a valid result: the run continues and
    the failure is counted and surfaced in the scorecard rather than being
    silently dropped.
    """

    provider: str
    question: str

    # The generated (or raw) input this provider was actually given. Persisted
    # so anyone can check that neither side was hand-tuned.
    query_input: dict[str, Any] | None = None
    query_mode: str = "generated"

    retrieved_context: str = ""
    answer_text: str = ""
    source_urls: list[str] = Field(default_factory=list)

    # Provider call only. Query generation and synthesis are excluded so that
    # shared overhead does not dilute the difference between providers.
    latency_ms: float = 0.0

    # HTTP requests actually issued, including retries.
    request_count: int = 0

    # Estimated from published list prices, computed identically for both
    # providers so the comparison is method-symmetric.
    estimated_cost_usd: float = 0.0

    # Some APIs report their own billed cost. Recorded when available as an
    # audit cross-check; never used as the headline number, because only one
    # side offers it and mixing sources would make the columns incomparable.
    reported_cost_usd: float | None = None

    raw_response: dict[str, Any] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Provider(ABC):
    """Base class for provider adapters.

    Retries, timing, error capture and cost accounting live here so every
    provider is measured by identical code rather than by whatever each
    adapter happens to do.
    """

    name: str

    def __init__(self, settings: dict[str, Any], pricing: dict[str, Any], api_key: str):
        self.settings = settings
        self.pricing = pricing
        self._api_key = api_key

    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON schema for this API's query fields.

        Field descriptions must be quoted from this provider's own public
        documentation. This schema is the ONLY provider-specific thing the
        shared query-generation prompt ever sees.
        """

    @abstractmethod
    def raw_input(self, question: str) -> dict[str, Any]:
        """The unmodified question mapped onto this API's fields.

        Used by `query_generation.mode: raw`, kept so both modes can be run
        and compared.
        """

    @abstractmethod
    def build_payload(self, query_input: dict[str, Any]) -> dict[str, Any]:
        """Turn generated input plus config settings into a request body."""

    @abstractmethod
    def headers(self) -> dict[str, str]:
        """Auth and content headers. Never logged."""

    @abstractmethod
    def parse(self, body: dict[str, Any]) -> tuple[str, list[str]]:
        """Return (retrieved_context, source_urls) from a response body."""

    @abstractmethod
    def estimate_cost(self, body: dict[str, Any]) -> float:
        """Estimated USD cost for one successful call, from published prices."""

    def reported_cost(self, body: dict[str, Any]) -> float | None:
        """Cost the API reports for itself, if it reports one."""
        return None

    async def run(
        self,
        client: httpx.AsyncClient,
        question: str,
        query_input: dict[str, Any],
        query_mode: str = "generated",
        retries: int = 1,
        backoff_s: float = 2.0,
    ) -> ProviderResult:
        """Search with bounded retries, never raising into the caller."""
        result = ProviderResult(
            provider=self.name,
            question=question,
            query_input=query_input,
            query_mode=query_mode,
        )

        payload = self.build_payload(query_input)
        attempts = retries + 1
        last_error: str | None = None

        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                result.request_count += 1
                response = await client.post(
                    self.settings["endpoint"], json=payload, headers=self.headers()
                )
                response.raise_for_status()
                body = response.json()
            except Exception as exc:  # noqa: BLE001 - one failure must not kill the run
                elapsed = (time.perf_counter() - started) * 1000
                last_error = _describe(exc)
                if attempt + 1 < attempts and _is_retryable(exc):
                    await asyncio.sleep(backoff_s * (2**attempt))
                    continue
                result.latency_ms = elapsed
                result.error = last_error
                return result

            result.latency_ms = (time.perf_counter() - started) * 1000
            result.raw_response = body
            context, urls = self.parse(body)
            result.retrieved_context = context
            result.source_urls = urls
            result.estimated_cost_usd = self.estimate_cost(body)
            result.reported_cost_usd = self.reported_cost(body)
            return result

        result.error = last_error or "unknown error"
        return result


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _describe(exc: Exception) -> str:
    """Error text safe to persist and print.

    Response bodies are included because they carry the provider's request id,
    but request headers never are, so an API key cannot leak into raw.jsonl
    or the terminal.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"
    if isinstance(exc, httpx.TimeoutException):
        return f"timeout: {type(exc).__name__}"
    return f"{type(exc).__name__}: {exc}"

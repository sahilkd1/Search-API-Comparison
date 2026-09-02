"""Parallel Search API adapter.

Request/response shapes taken from the live docs, not from memory:
  https://docs.parallel.ai/api-reference/search/search
  https://docs.parallel.ai/search/modes
  https://docs.parallel.ai/getting-started/pricing
Verified against a real /v1/search call.
"""

from __future__ import annotations

from typing import Any

from .base import Provider

# Results included in the base price before per-result charges begin.
INCLUDED_RESULTS = 10


class ParallelSearch(Provider):
    name = "parallel"

    def input_schema(self) -> dict[str, Any]:
        """Field descriptions are quoted from Parallel's own API reference.

        Nothing here is tuned in Parallel's favour: the wording is the
        vendor's own description of what each field is for.
        """
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["objective", "search_queries"],
            "properties": {
                "objective": {
                    "type": "string",
                    "description": (
                        "Natural-language description of the underlying question or "
                        "goal driving the search. Should be self-contained with enough "
                        "context to understand the intent of the search."
                    ),
                },
                "search_queries": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 3,
                    "items": {"type": "string"},
                    "description": (
                        "Concise keyword search queries, 3-6 words each. At least one "
                        "query is required, provide 2-3 for best results. Used together "
                        "with objective to focus results on the most relevant content."
                    ),
                },
            },
        }

    def raw_input(self, question: str) -> dict[str, Any]:
        return {"objective": question, "search_queries": [question]}

    def build_payload(self, query_input: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings
        payload: dict[str, Any] = {
            "objective": query_input["objective"],
            "search_queries": query_input["search_queries"],
        }

        if settings.get("mode"):
            payload["mode"] = settings["mode"]

        advanced: dict[str, Any] = {}
        if settings.get("max_results"):
            advanced["max_results"] = settings["max_results"]
        if settings.get("max_chars_per_result"):
            advanced["excerpt_settings"] = {
                "max_chars_per_result": settings["max_chars_per_result"]
            }
        if advanced:
            payload["advanced_settings"] = advanced

        return payload

    def headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "Content-Type": "application/json"}

    def parse(self, body: dict[str, Any]) -> tuple[str, list[str]]:
        blocks: list[str] = []
        urls: list[str] = []

        for result in body.get("results") or []:
            url = result.get("url")
            if not url:
                continue
            if url not in urls:
                urls.append(url)

            title = result.get("title") or url
            header = f"[{len(blocks) + 1}] {title} ({url})"
            if result.get("publish_date"):
                header += f" — published {result['publish_date']}"
            excerpts = "\n".join(result.get("excerpts") or [])
            blocks.append(f"{header}\n{excerpts}".strip())

        return "\n\n".join(blocks), urls

    def estimate_cost(self, body: dict[str, Any]) -> float:
        """Estimate one successful call from published list prices.

        Failed attempts are assumed unbilled, the conservative reading;
        request_count is persisted so this can be re-derived either way.
        """
        rates = self.pricing.get("per_1k_by_mode", {})
        mode = self.settings.get("mode") or "advanced"
        base_per_1k = rates.get(mode)
        if base_per_1k is None:
            return 0.0

        extra_per_1k = self.pricing.get("additional_result_per_1k", 0.0)
        extra_results = max(0, len(body.get("results") or []) - INCLUDED_RESULTS)
        return (base_per_1k + extra_per_1k * extra_results) / 1000.0

"""Exa Search API adapter.

Request shape and auth taken from Exa's published OpenAPI spec
(https://api.exa.ai/openapi.json), pricing from
https://docs.exa.ai/reference/pricing. Response shape confirmed against a
real /search call rather than assumed, because the spec does not fully
enumerate SearchResponse.
"""

from __future__ import annotations

from typing import Any

from .base import Provider

INCLUDED_RESULTS = 10

# Per Exa's spec, additionalQueries "only works with a deep-search type".
DEEP_TYPES = {"deep-lite", "deep", "deep-reasoning"}


class ExaSearch(Provider):
    name = "exa"

    def input_schema(self) -> dict[str, Any]:
        """Field descriptions are quoted from Exa's own OpenAPI spec.

        The shape is narrower than Parallel's because Exa's API is narrower:
        under non-deep search types it documents a single query string and
        ignores additional queries. Generating extra keyword queries here
        would be inventing a capability the vendor does not offer, so the
        schema exposes the extra field only when the configured search type
        actually supports it.
        """
        schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The query string for the search.",
                }
            },
        }

        if (self.settings.get("type") or "auto") in DEEP_TYPES:
            schema["required"].append("additionalQueries")
            schema["properties"]["additionalQueries"] = {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "string"},
                "description": (
                    "Additional query variations for deep-search variants. When "
                    "provided, these queries are used alongside the main query for "
                    "broader results."
                ),
            }

        return schema

    def raw_input(self, question: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": question}
        if (self.settings.get("type") or "auto") in DEEP_TYPES:
            payload["additionalQueries"] = [question]
        return payload

    def build_payload(self, query_input: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings
        payload: dict[str, Any] = {"query": query_input["query"]}

        if query_input.get("additionalQueries"):
            payload["additionalQueries"] = query_input["additionalQueries"]
        if settings.get("type"):
            payload["type"] = settings["type"]
        if settings.get("num_results"):
            payload["numResults"] = settings["num_results"]
        if settings.get("highlights"):
            payload["contents"] = {"highlights": True}

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
            if result.get("publishedDate"):
                header += f" — published {result['publishedDate']}"

            highlights = result.get("highlights")
            if isinstance(highlights, list):
                content = "\n".join(str(h) for h in highlights)
            elif isinstance(highlights, str):
                content = highlights
            else:
                content = result.get("text") or result.get("summary") or ""

            blocks.append(f"{header}\n{content}".strip())

        return "\n\n".join(blocks), urls

    def estimate_cost(self, body: dict[str, Any]) -> float:
        """Estimate from published list prices.

        Computed the same way as every other provider even though Exa also
        reports its own billed cost, so the scorecard's headline cost column
        compares like with like. The reported figure is captured separately.
        """
        rates = self.pricing.get("per_1k_by_type", {})
        search_type = self.settings.get("type") or "auto"
        base_per_1k = rates.get(search_type)
        if base_per_1k is None:
            return 0.0

        extra_per_1k = self.pricing.get("additional_result_per_1k", 0.0)
        extra_results = max(0, len(body.get("results") or []) - INCLUDED_RESULTS)
        return (base_per_1k + extra_per_1k * extra_results) / 1000.0

    def reported_cost(self, body: dict[str, Any]) -> float | None:
        cost = body.get("costDollars")
        if isinstance(cost, dict) and isinstance(cost.get("total"), (int, float)):
            return float(cost["total"])
        return None

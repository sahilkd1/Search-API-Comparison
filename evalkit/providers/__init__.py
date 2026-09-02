"""Provider registry.

To add a provider: write one adapter module in this package, then add one
line to REGISTRY, one line to API_KEY_ENV, and one block to config.yaml.
Nothing else changes.
"""

from __future__ import annotations

from .base import Provider, ProviderResult
from .exa import ExaSearch
from .parallel_search import ParallelSearch

REGISTRY: dict[str, type[Provider]] = {
    ParallelSearch.name: ParallelSearch,
    ExaSearch.name: ExaSearch,
}

API_KEY_ENV: dict[str, str] = {
    "parallel": "PARALLEL_API_KEY",
    "exa": "EXA_API_KEY",
}

__all__ = ["Provider", "ProviderResult", "REGISTRY", "API_KEY_ENV"]

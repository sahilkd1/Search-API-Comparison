"""Metrics and scorecard rendering.

Editorial rule enforced here: this module reports numbers and never declares
a winner. Providers appear in a fixed, stable order everywhere; no cell is
bolded, starred, coloured or reordered based on who scored better; and a
question the incumbent wins renders identically to one Parallel wins.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from judge import JudgeResult
from providers import ProviderResult


@dataclass
class QuestionOutcome:
    question: str
    expected_answer: str
    source_url: str
    results: dict[str, ProviderResult]
    judge: JudgeResult


@dataclass
class ProviderMetrics:
    provider: str
    graded: int
    correct: int
    partial: int
    incorrect: int
    errors: int
    accuracy_pct: float
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    cost_per_1k_usd: float
    citation_coverage_pct: float


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    Deliberately not interpolated: with 15-25 questions an interpolated p95
    invents a number that lies between two real observations, and every
    figure in this scorecard should be traceable to a measurement.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(pct / 100 * len(ordered) + 0.5)))
    return ordered[rank - 1]


def compute_metrics(outcomes: list[QuestionOutcome], provider: str) -> ProviderMetrics:
    results = [o.results[provider] for o in outcomes if provider in o.results]
    successful = [r for r in results if r.ok]
    latencies = [r.latency_ms for r in successful]

    verdicts = [
        o.judge.grades[provider].verdict
        for o in outcomes
        if o.judge.ok and provider in o.judge.grades
    ]
    graded = len(verdicts)
    correct = verdicts.count("correct")
    partial = verdicts.count("partial")
    incorrect = verdicts.count("incorrect")

    with_sources = sum(1 for r in successful if r.source_urls)
    costs = [r.estimated_cost_usd for r in successful]
    mean_cost = statistics.fmean(costs) if costs else 0.0

    return ProviderMetrics(
        provider=provider,
        graded=graded,
        correct=correct,
        partial=partial,
        incorrect=incorrect,
        errors=len(results) - len(successful),
        accuracy_pct=(correct / graded * 100) if graded else 0.0,
        mean_latency_ms=statistics.fmean(latencies) if latencies else 0.0,
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        cost_per_1k_usd=mean_cost * 1000,
        citation_coverage_pct=(with_sources / len(successful) * 100) if successful else 0.0,
    )


def _fmt(value: float, suffix: str = "", digits: int = 1) -> str:
    return f"{value:.{digits}f}{suffix}"


def render_scorecard(
    outcomes: list[QuestionOutcome],
    providers: list[str],
    config: dict[str, Any],
    warnings: list[str],
    started_at: datetime,
) -> str:
    metrics = {p: compute_metrics(outcomes, p) for p in providers}
    n = len(outcomes)
    lines: list[str] = []

    lines.append("# Search API comparison scorecard")
    lines.append("")
    lines.append(f"- **Run started (UTC):** {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **Questions:** {n}")
    lines.append(f"- **Providers:** {' vs '.join(providers)}")
    lines.append(f"- **Judge model:** `{config['judge']['model']}`")
    lines.append(f"- **Synthesizer model:** `{config['synthesis']['model']}` (identical for both providers)")
    lines.append(
        f"- **Search input mode:** `{config['query_generation']['mode']}` "
        f"via `{config['query_generation']['model']}`"
    )
    lines.append("")
    lines.append(
        "Both providers were given the same questions, the same synthesizer, and "
        "search inputs written by the same model from the same prompt. This "
        "document reports measurements only and does not name a winner."
    )
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    header = "| Metric | " + " | ".join(providers) + " |"
    lines.append(header)
    lines.append("| --- | " + " | ".join(["---"] * len(providers)) + " |")

    def row(label: str, fn) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(metrics[p]) for p in providers) + " |")

    row("Questions graded", lambda m: str(m.graded))
    row("Accuracy (correct / graded)", lambda m: f"{_fmt(m.accuracy_pct)}% ({m.correct}/{m.graded})")
    row("Partial", lambda m: str(m.partial))
    row("Incorrect", lambda m: str(m.incorrect))
    row("Mean latency", lambda m: f"{_fmt(m.mean_latency_ms, ' ms', 0)}")
    row("p50 latency", lambda m: f"{_fmt(m.p50_latency_ms, ' ms', 0)}")
    row("p95 latency", lambda m: f"{_fmt(m.p95_latency_ms, ' ms', 0)}")
    row("Est. cost per 1,000 requests", lambda m: f"${m.cost_per_1k_usd:.2f}")
    row("Citation coverage", lambda m: f"{_fmt(m.citation_coverage_pct)}%")
    row("Errors", lambda m: str(m.errors))
    lines.append("")
    lines.append(
        "Latency is the provider API call only. Query generation and answer "
        "synthesis are excluded because they are identical for both providers "
        "and would dilute the difference being measured."
    )
    lines.append("")
    lines.append(
        "Cost figures are **estimates** derived from published list prices in "
        "`config.yaml`, computed by identical code for both providers. They "
        "ignore negotiated rates, free tiers and volume commitments. Verify "
        "against each vendor's pricing page before quoting them."
    )
    lines.append("")

    reported = {
        p: [o.results[p].reported_cost_usd for o in outcomes
            if p in o.results and o.results[p].reported_cost_usd is not None]
        for p in providers
    }
    if any(reported.values()):
        lines.append("Some APIs report their own billed cost. Where available:")
        lines.append("")
        for p in providers:
            values = reported[p]
            if values:
                lines.append(
                    f"- `{p}`: mean ${statistics.fmean(values):.5f} per request "
                    f"as reported by the API, vs ${metrics[p].cost_per_1k_usd / 1000:.5f} estimated."
                )
            else:
                lines.append(f"- `{p}`: does not report per-request cost.")
        lines.append("")

    lines.append("## Per question")
    lines.append("")
    lines.append("| # | Question | " + " | ".join(f"{p} verdict" for p in providers) + " | Latency delta |")
    lines.append("| --- | --- | " + " | ".join(["---"] * len(providers)) + " | --- |")

    for index, outcome in enumerate(outcomes, start=1):
        verdicts = []
        for p in providers:
            if outcome.judge.ok and p in outcome.judge.grades:
                verdicts.append(outcome.judge.grades[p].verdict)
            else:
                verdicts.append("ungraded")

        latencies = [outcome.results[p].latency_ms for p in providers if p in outcome.results]
        if len(latencies) == 2 and all(outcome.results[p].ok for p in providers):
            delta = latencies[0] - latencies[1]
            delta_text = f"{providers[0]} {'+' if delta >= 0 else '−'}{abs(delta):.0f} ms"
        else:
            delta_text = "—"

        question = outcome.question.replace("|", "\\|")
        if len(question) > 90:
            question = question[:87] + "..."
        lines.append(f"| {index} | {question} | " + " | ".join(verdicts) + f" | {delta_text} |")

    lines.append("")

    divergences = [
        o for o in outcomes
        if o.judge.ok
        and len({o.judge.grades[p].verdict for p in providers if p in o.judge.grades}) > 1
    ]

    lines.append("## Divergences")
    lines.append("")
    if not divergences:
        lines.append(
            "The two providers received the same verdict on every question. "
            "There is nothing to examine here, which is itself a result: on this "
            "question set the judge could not separate them."
        )
        lines.append("")
    else:
        lines.append(
            f"{len(divergences)} of {n} questions where the two providers received "
            "different verdicts. These are the questions worth reading closely — "
            "they are where the providers actually differ."
        )
        lines.append("")
        for outcome in divergences:
            lines.append(f"### {outcome.question}")
            lines.append("")
            lines.append(f"**Expected answer:** {outcome.expected_answer}")
            if outcome.source_url:
                lines.append("")
                lines.append(f"**Ground truth source:** <{outcome.source_url}>")
            lines.append("")
            for p in providers:
                result = outcome.results.get(p)
                grade = outcome.judge.grades.get(p)
                lines.append(f"#### {p}")
                lines.append("")
                if grade:
                    lines.append(f"- **Verdict:** {grade.verdict}")
                    lines.append(f"- **Graded as answer {grade.position}** (order randomized per question)")
                if result:
                    lines.append(f"- **Latency:** {result.latency_ms:.0f} ms")
                    lines.append(f"- **Sources returned:** {len(result.source_urls)}")
                    lines.append("")
                    lines.append(f"**Search input given to this API ({result.query_mode}):**")
                    lines.append("")
                    lines.append("```json")
                    import json as _json
                    lines.append(_json.dumps(result.query_input, indent=2))
                    lines.append("```")
                    lines.append("")
                    lines.append("**Answer:**")
                    lines.append("")
                    lines.append("> " + (result.answer_text or result.error or "(none)").replace("\n", "\n> "))
                    lines.append("")
                if grade:
                    lines.append("**Judge's reasoning:**")
                    lines.append("")
                    lines.append("> " + grade.reasoning.replace("\n", "\n> "))
                    lines.append("")
            lines.append("---")
            lines.append("")

    lines.append("## How to read this")
    lines.append("")
    lines.append(
        f"- **n = {n}.** This is a sample, not a benchmark. Treat differences of "
        "one or two questions as noise."
    )
    lines.append(
        "- **The judge is a model.** It can be wrong. Its full reasoning is above "
        "for every divergent question so you can check it."
    )
    lines.append(
        "- **Latency depends on the network** between this machine and each "
        "vendor, and on time of day. Rerun before drawing conclusions."
    )
    lines.append(
        "- **Rerunning the same questions measures caches, not search.** Vendors "
        "cache results, and a second run against an identical question set can "
        "report latency an order of magnitude lower than the first. Quote latency "
        "from the FIRST run against a fresh question set, and treat a suspiciously "
        "fast number as a cache hit rather than a finding."
    )
    lines.append(
        "- **Costs are estimates** from published list prices, not invoices."
    )
    lines.append(
        "- **Every raw API response is on disk** in `raw.jsonl` next to this file. "
        "Every number here can be recomputed from it."
    )
    lines.append("")

    return "\n".join(lines)


def write_results_csv(path: Path, outcomes: list[QuestionOutcome], providers: list[str]) -> None:
    columns = ["question", "expected_answer", "source_url"]
    for p in providers:
        columns += [
            f"{p}_verdict", f"{p}_position", f"{p}_latency_ms",
            f"{p}_sources", f"{p}_est_cost_usd", f"{p}_error", f"{p}_answer",
        ]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for outcome in outcomes:
            row: list[Any] = [outcome.question, outcome.expected_answer, outcome.source_url]
            for p in providers:
                result = outcome.results.get(p)
                grade = outcome.judge.grades.get(p) if outcome.judge.ok else None
                row += [
                    grade.verdict if grade else "ungraded",
                    grade.position if grade else "",
                    f"{result.latency_ms:.0f}" if result else "",
                    len(result.source_urls) if result else "",
                    f"{result.estimated_cost_usd:.6f}" if result else "",
                    (result.error or "") if result else "",
                    (result.answer_text or "") if result else "",
                ]
            writer.writerow(row)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

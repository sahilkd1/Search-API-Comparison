"""evalkit runner.

Loads questions.csv, runs both providers concurrently, grades them blind with
a single-call judge, and writes a timestamped run directory containing
raw.jsonl, results.csv and scorecard.md.

One provider failing a question never kills the run: the error is recorded,
the run continues, and the failure is counted in the scorecard.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from judge import grade_pair
from llm import synthesize_answer
from providers import API_KEY_ENV, REGISTRY, Provider, ProviderResult
from query_gen import generate_input
from report import QuestionOutcome, compute_metrics, render_scorecard, utc_now, write_results_csv

ROOT = Path(__file__).parent
console = Console()


@dataclass
class Question:
    question: str
    expected_answer: str
    source_url: str
    notes: str = ""


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or ROOT / "config.yaml"
    if not config_path.exists():
        console.print(f"[red]Missing config file:[/red] {config_path}")
        raise SystemExit(1)
    with config_path.open() as handle:
        return yaml.safe_load(handle)


def load_questions(path: Path, min_warn: int) -> tuple[list[Question], list[str]]:
    """Load and validate questions.csv, warning loudly rather than failing."""
    if not path.exists():
        console.print(f"[red]Missing questions file:[/red] {path}")
        raise SystemExit(1)

    questions: list[Question] = []
    warnings: list[str] = []

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = {"question", "expected_answer", "source_url"} - set(reader.fieldnames or [])
        if missing:
            console.print(f"[red]questions.csv is missing columns:[/red] {', '.join(sorted(missing))}")
            raise SystemExit(1)

        for line_no, row in enumerate(reader, start=2):
            question = (row.get("question") or "").strip()
            if not question:
                warnings.append(f"Row {line_no}: empty question, skipped.")
                continue

            expected = (row.get("expected_answer") or "").strip()
            source = (row.get("source_url") or "").strip()
            label = question[:60] + ("..." if len(question) > 60 else "")

            if not expected:
                warnings.append(
                    f"Row {line_no} (\"{label}\") has no expected_answer. "
                    "The judge has no ground truth to grade against, so this row "
                    "is unusable. Fill it in or delete the row."
                )
            if not source:
                warnings.append(
                    f"Row {line_no} (\"{label}\") has no source_url. Without one, "
                    "a prospect cannot verify the ground truth and can dismiss "
                    "the result."
                )

            questions.append(
                Question(question, expected, source, (row.get("notes") or "").strip())
            )

    if any("REPLACE ME" in q.question for q in questions):
        warnings.append(
            "questions.csv still contains the shipped sample rows. These exist "
            "only to prove the harness runs. Replace them with the prospect's "
            "own questions before showing results to anyone."
        )

    if len(questions) < min_warn:
        warnings.append(
            f"Only {len(questions)} questions loaded. Below {min_warn}, results "
            "are not statistically meaningful — a single question swings accuracy "
            f"by {100 / max(1, len(questions)):.0f} points. Aim for 15-25."
        )

    return questions, warnings


def build_provider(name: str, config: dict[str, Any]) -> Provider:
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        console.print(f"[red]Unknown provider '{name}'.[/red] Registered: {known}")
        raise SystemExit(1)

    env_var = API_KEY_ENV[name]
    api_key = os.environ.get(env_var, "").strip()
    if not api_key:
        console.print(
            f"[red]{env_var} is not set.[/red] "
            f"Copy .env.example to .env and add your {name} key."
        )
        raise SystemExit(1)

    return REGISTRY[name](
        settings=config["providers"][name],
        pricing=config["pricing"][name],
        api_key=api_key,
    )


async def run_provider(
    client: httpx.AsyncClient,
    provider: Provider,
    question: str,
    config: dict[str, Any],
) -> ProviderResult:
    """Generate this provider's search input, search, then synthesize."""
    qg = config["query_generation"]
    mode = qg.get("mode", "generated")

    if mode == "raw":
        query_input = provider.raw_input(question)
    else:
        try:
            query_input = await generate_input(
                client,
                question=question,
                input_schema=provider.input_schema(),
                model=qg["model"],
                max_completion_tokens=qg.get("max_completion_tokens", 400),
            )
        except Exception as exc:  # noqa: BLE001
            # Falling back to the raw question keeps the run alive, but the
            # substitution is recorded so nobody mistakes it for a fair input.
            query_input = provider.raw_input(question)
            result = await provider.run(
                client, question, query_input, query_mode="raw-fallback",
                retries=config["run"]["retries"], backoff_s=config["run"]["retry_backoff_s"],
            )
            result.error = result.error or f"query generation failed: {type(exc).__name__}: {exc}"
            return result

    run_config = config["run"]
    result = await provider.run(
        client, question, query_input, query_mode=mode,
        retries=run_config["retries"], backoff_s=run_config["retry_backoff_s"],
    )

    if result.ok:
        synth = config["synthesis"]
        try:
            result.answer_text = await synthesize_answer(
                client,
                model=synth["model"],
                question=question,
                context=result.retrieved_context,
                max_completion_tokens=synth.get("max_completion_tokens", 400),
                temperature=synth.get("temperature", 0.0),
            )
        except Exception as exc:  # noqa: BLE001
            result.error = f"synthesis failed: {type(exc).__name__}: {exc}"

    return result


async def process_question(
    client: httpx.AsyncClient,
    item: Question,
    providers: list[Provider],
    config: dict[str, Any],
    rng: random.Random,
) -> QuestionOutcome:
    results = await asyncio.gather(
        *(run_provider(client, p, item.question, config) for p in providers)
    )
    by_name = {r.provider: r for r in results}

    judge_config = config["judge"]
    judged = await grade_pair(
        client,
        question=item.question,
        expected_answer=item.expected_answer,
        answers={name: result.answer_text for name, result in by_name.items()},
        model=judge_config["model"],
        max_completion_tokens=judge_config.get("max_completion_tokens", 2000),
        temperature=judge_config.get("temperature", 0.0),
        rng=rng,
        randomize=judge_config.get("randomize_positions", True),
    )

    return QuestionOutcome(
        question=item.question,
        expected_answer=item.expected_answer,
        source_url=item.source_url,
        results=by_name,
        judge=judged,
    )


def write_raw_jsonl(path: Path, outcomes: list[QuestionOutcome]) -> None:
    """Persist everything needed to recompute every number in the scorecard."""
    with path.open("w", encoding="utf-8") as handle:
        for outcome in outcomes:
            record = {
                "question": outcome.question,
                "expected_answer": outcome.expected_answer,
                "source_url": outcome.source_url,
                "judge_error": outcome.judge.error,
                "providers": {
                    name: {
                        "query_mode": result.query_mode,
                        "query_input": result.query_input,
                        "answer_text": result.answer_text,
                        "source_urls": result.source_urls,
                        "latency_ms": result.latency_ms,
                        "request_count": result.request_count,
                        "estimated_cost_usd": result.estimated_cost_usd,
                        "reported_cost_usd": result.reported_cost_usd,
                        "error": result.error,
                        "judge_position": (
                            outcome.judge.grades[name].position
                            if outcome.judge.ok and name in outcome.judge.grades else None
                        ),
                        "verdict": (
                            outcome.judge.grades[name].verdict
                            if outcome.judge.ok and name in outcome.judge.grades else None
                        ),
                        "judge_reasoning": (
                            outcome.judge.grades[name].reasoning
                            if outcome.judge.ok and name in outcome.judge.grades else None
                        ),
                        "raw_response": result.raw_response,
                    }
                    for name, result in outcome.results.items()
                },
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Parallel against one incumbent.")
    parser.add_argument("--query-mode", choices=["generated", "raw"],
                        help="Override query_generation.mode.")
    parser.add_argument("--questions", type=Path, help="Override questions.csv path.")
    parser.add_argument("--limit", type=int, help="Only run the first N questions.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Seed for judge position randomization, for reproducible runs.")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    load_dotenv(ROOT / ".env", override=True)
    config = load_config()

    if args.query_mode:
        config["query_generation"]["mode"] = args.query_mode

    run_config = config["run"]
    questions_path = args.questions or (ROOT / run_config["questions_file"])
    questions, warnings = load_questions(questions_path, run_config["min_questions_warn"])
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        console.print("[red]No usable questions loaded.[/red]")
        return 1

    incumbent = config["incumbent"]
    providers = [build_provider("parallel", config), build_provider(incumbent, config)]
    names = [p.name for p in providers]

    for warning in warnings:
        console.print(f"[yellow]warning[/yellow] {warning}")
    if warnings:
        console.print()

    started_at = utc_now()
    rng = random.Random(args.seed)
    semaphore = asyncio.Semaphore(run_config["concurrency"])
    timeout = httpx.Timeout(run_config["timeout_s"])

    console.print(
        f"Comparing [bold]{names[0]}[/bold] vs [bold]{names[1]}[/bold] on "
        f"[bold]{len(questions)}[/bold] questions "
        f"(input mode: {config['query_generation']['mode']})\n"
    )

    outcomes: list[QuestionOutcome | None] = [None] * len(questions)

    async with httpx.AsyncClient(timeout=timeout) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Searching and grading", total=len(questions))

            async def worker(index: int, item: Question) -> None:
                async with semaphore:
                    outcomes[index] = await process_question(client, item, providers, config, rng)
                    progress.advance(task)

            await asyncio.gather(*(worker(i, q) for i, q in enumerate(questions)))

    completed = [o for o in outcomes if o is not None]

    run_dir = ROOT / run_config["output_dir"] / started_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    write_raw_jsonl(run_dir / "raw.jsonl", completed)
    write_results_csv(run_dir / "results.csv", completed, names)
    scorecard = render_scorecard(completed, names, config, warnings, started_at)
    (run_dir / "scorecard.md").write_text(scorecard, encoding="utf-8")

    table = Table(show_header=True, header_style="bold", title="Summary")
    table.add_column("Metric")
    for name in names:
        table.add_column(name)

    metrics = {name: compute_metrics(completed, name) for name in names}

    def row(label: str, fn) -> None:
        table.add_row(label, *[fn(metrics[n]) for n in names])

    row("Accuracy", lambda m: f"{m.accuracy_pct:.1f}% ({m.correct}/{m.graded})")
    row("Mean latency", lambda m: f"{m.mean_latency_ms:.0f} ms")
    row("p95 latency", lambda m: f"{m.p95_latency_ms:.0f} ms")
    row("Est. cost / 1k requests", lambda m: f"${m.cost_per_1k_usd:.2f}")
    row("Citation coverage", lambda m: f"{m.citation_coverage_pct:.0f}%")
    row("Errors", lambda m: str(m.errors))
    console.print(table)

    judge_failures = sum(1 for o in completed if not o.judge.ok)
    if judge_failures:
        console.print(f"[yellow]{judge_failures} question(s) could not be graded.[/yellow]")

    console.print(f"\nScorecard: [bold]{run_dir / 'scorecard.md'}[/bold]")
    console.print(f"Raw responses: {run_dir / 'raw.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

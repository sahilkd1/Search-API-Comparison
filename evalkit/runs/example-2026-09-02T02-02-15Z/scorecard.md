# Search API comparison scorecard

- **Run started (UTC):** 2026-09-02 02:02:15
- **Questions:** 3
- **Providers:** parallel vs exa
- **Judge model:** `gpt-5.4-2026-03-05`
- **Synthesizer model:** `gpt-5.4-2026-03-05` (identical for both providers)
- **Search input mode:** `generated` via `gpt-5.4-mini-2026-03-17`

Both providers were given the same questions, the same synthesizer, and search inputs written by the same model from the same prompt. This document reports measurements only and does not name a winner.

## Warnings

- questions.csv still contains the shipped sample rows. These exist only to prove the harness runs. Replace them with the prospect's own questions before showing results to anyone.
- Only 3 questions loaded. Below 10, results are not statistically meaningful — a single question swings accuracy by 33 points. Aim for 15-25.

## Summary

| Metric | parallel | exa |
| --- | --- | --- |
| Questions graded | 3 | 3 |
| Accuracy (correct / graded) | 100.0% (3/3) | 100.0% (3/3) |
| Partial | 0 | 0 |
| Incorrect | 0 | 0 |
| Mean latency | 998 ms | 101 ms |
| p50 latency | 892 ms | 93 ms |
| p95 latency | 1266 ms | 132 ms |
| Est. cost per 1,000 requests | $5.00 | $7.00 |
| Citation coverage | 100.0% | 100.0% |
| Errors | 0 | 0 |

Latency is the provider API call only. Query generation and answer synthesis are excluded because they are identical for both providers and would dilute the difference being measured.

Cost figures are **estimates** derived from published list prices in `config.yaml`, computed by identical code for both providers. They ignore negotiated rates, free tiers and volume commitments. Verify against each vendor's pricing page before quoting them.

Some APIs report their own billed cost. Where available:

- `parallel`: does not report per-request cost.
- `exa`: mean $0.00700 per request as reported by the API, vs $0.00700 estimated.

## Per question

| # | Question | parallel verdict | exa verdict | Latency delta |
| --- | --- | --- | --- | --- |
| 1 | REPLACE ME — What is the list price per 1,000 requests for the Parallel Search API in b... | correct | correct | parallel +743 ms |
| 2 | REPLACE ME — What is the base price per 1,000 requests for Exa's /search endpoint? | correct | correct | parallel +760 ms |
| 3 | REPLACE ME — What does Exa charge per request for an Agent run at medium effort? | correct | correct | parallel +1187 ms |

## Divergences

The two providers received the same verdict on every question. There is nothing to examine here, which is itself a result: on this question set the judge could not separate them.

## How to read this

- **n = 3.** This is a sample, not a benchmark. Treat differences of one or two questions as noise.
- **The judge is a model.** It can be wrong. Its full reasoning is above for every divergent question so you can check it.
- **Latency depends on the network** between this machine and each vendor, and on time of day. Rerun before drawing conclusions.
- **Rerunning the same questions measures caches, not search.** Vendors cache results, and a second run against an identical question set can report latency an order of magnitude lower than the first. Quote latency from the FIRST run against a fresh question set, and treat a suspiciously fast number as a cache hit rather than a finding.
- **Costs are estimates** from published list prices, not invoices.
- **Every raw API response is on disk** in `raw.jsonl` next to this file. Every number here can be recomputed from it.

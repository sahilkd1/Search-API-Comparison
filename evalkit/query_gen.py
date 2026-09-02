"""Provider-agnostic search-input generation.

Why this exists
---------------
The two APIs under comparison do not accept the same input shape. One takes a
natural-language objective plus several short keyword queries; another takes a
single query string. Feeding both the identical raw question is not neutral —
it uses at least one of them in a way its own documentation advises against,
and then reports the result as "search quality".

What is symmetric is the METHOD: one model, one prompt, no per-provider hand
editing, each API receiving the input shape its own documentation asks for.

The prompt below is written once. It is given the question and the target
API's input schema, and nothing else. It is never told which vendor it is
generating for, and it contains no vendor names, no vendor-specific hints and
no per-vendor branching. The field descriptions inside each schema come from
that vendor's own published docs and live next to the adapter that owns them.

Every generated input is persisted to raw.jsonl and surfaced in the scorecard,
so "did you optimise one side?" is answerable by reading them.
"""

from __future__ import annotations

from typing import Any

import httpx

from llm import chat_json

SYSTEM_PROMPT = """\
You turn a research question into the input fields of a web search API.

You will be given:
  1. QUESTION - what the user needs answered.
  2. INPUT_SCHEMA - a JSON schema for the fields this particular search API
     accepts. Each field's description is quoted from that API's own public
     documentation and states how the field is meant to be used.

Produce values that give the API the best possible chance of retrieving pages
that answer the question, following each field's documented guidance exactly.

Rules:
- Follow every constraint in the schema: field count, item count, and any
  stated length or style guidance in a field's description.
- Use only information present in the question. Never invent entities, dates
  or numbers that the question does not contain.
- Do not answer the question. You are writing search inputs, not answers.
- Preserve the specific, distinguishing terms from the question. Proper nouns,
  product names, version numbers and dates are what make a search precise.
- Do not add words that narrow the question to something the user did not ask,
  and do not broaden it into a different question.
- Write inputs that would work for any competent web search engine. You are
  not told which engine this is. The same instructions are used for every
  engine in this comparison, so do not tailor your output to the quirks of any
  particular vendor.
"""

USER_TEMPLATE = """\
QUESTION:
{question}

INPUT_SCHEMA:
{schema}
"""


async def generate_input(
    client: httpx.AsyncClient,
    *,
    question: str,
    input_schema: dict[str, Any],
    model: str,
    max_completion_tokens: int = 400,
) -> dict[str, Any]:
    """Generate one API's input fields for one question.

    Note the arguments: the question and the schema. The provider's name is
    deliberately not a parameter, so it cannot leak into the prompt even by
    accident.
    """
    import json

    return await chat_json(
        client,
        model=model,
        system=SYSTEM_PROMPT,
        user=USER_TEMPLATE.format(
            question=question,
            schema=json.dumps(input_schema, indent=2),
        ),
        schema=input_schema,
        schema_name="search_input",
        max_completion_tokens=max_completion_tokens,
        temperature=0.0,
    )

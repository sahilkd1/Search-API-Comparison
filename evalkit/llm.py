"""Thin OpenAI helper shared by the query generator, synthesizer and judge.

One place to talk to OpenAI means the judge, the synthesizer and the query
generator cannot drift apart in how they call the model.

Parameter constraints here were confirmed against the live API, not assumed:
gpt-5.x rejects `max_tokens` and requires `max_completion_tokens`.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


class LLMError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise LLMError("OPENAI_API_KEY is not set. Add it to .env.")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def _post(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a chat completion, tolerating models that reject temperature.

    Model families differ on this: some accept `temperature: 0`, others allow
    only their default. Rather than hard-code which is which and go stale, we
    send the configured value and drop it if the API says it is unsupported,
    so swapping the judge model in config.yaml cannot fail a live customer run.
    """
    response = await client.post(CHAT_COMPLETIONS_URL, headers=_headers(), json=payload)

    if (
        response.status_code == 400
        and "temperature" in payload
        and "temperature" in response.text
        and "does not support" in response.text
    ):
        retry = {k: v for k, v in payload.items() if k != "temperature"}
        response = await client.post(CHAT_COMPLETIONS_URL, headers=_headers(), json=retry)

    if response.status_code != 200:
        # Response bodies carry OpenAI's error message but never the request
        # headers, so the API key cannot surface here.
        raise LLMError(f"OpenAI HTTP {response.status_code}: {response.text[:400]}")
    return response.json()


async def chat_json(
    client: httpx.AsyncClient,
    *,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    max_completion_tokens: int,
    temperature: float | None = 0.0,
) -> dict[str, Any]:
    """Call the model and return a JSON object conforming to `schema`.

    Uses strict structured outputs so callers never have to parse prose or
    strip markdown fences.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": max_completion_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    }
    if temperature is not None:
        payload["temperature"] = temperature

    body = await _post(client, payload)
    content = body["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON content: {content[:200]}") from exc


# The synthesizer must answer ONLY from the retrieved context. If it is
# allowed to fall back on what the model already knows, both providers
# produce the same correct answer regardless of what their search returned,
# and the comparison measures nothing. Refusing to answer when the context
# is insufficient is the behaviour that makes retrieval differences visible.
SYNTHESIS_SYSTEM = """\
You answer a question using ONLY the search results provided to you.

Rules:
- Use only facts stated in the provided search results. Do not use anything
  you know from training. Do not infer beyond what the text supports.
- If the search results do not contain enough information to answer, reply
  exactly: INSUFFICIENT CONTEXT
- Be specific and brief. Lead with the direct answer in the first sentence.
- Cite the bracketed result numbers you relied on, like [1] or [2][5].
- Do not speculate, hedge at length, or describe what you would need.
"""

SYNTHESIS_USER = """\
QUESTION:
{question}

SEARCH RESULTS:
{context}
"""


async def synthesize_answer(
    client: httpx.AsyncClient,
    *,
    model: str,
    question: str,
    context: str,
    max_completion_tokens: int = 400,
    temperature: float = 0.0,
) -> str:
    """Turn one provider's retrieved context into an answer.

    Identical model, prompt and token budget for every provider, so this step
    cancels out of the comparison.
    """
    if not context.strip():
        return "INSUFFICIENT CONTEXT"
    return await chat_text(
        client,
        model=model,
        system=SYNTHESIS_SYSTEM,
        user=SYNTHESIS_USER.format(question=question, context=context),
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
    )


async def chat_text(
    client: httpx.AsyncClient,
    *,
    model: str,
    system: str,
    user: str,
    max_completion_tokens: int,
    temperature: float | None = 0.0,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": max_completion_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    body = await _post(client, payload)
    return body["choices"][0]["message"]["content"]

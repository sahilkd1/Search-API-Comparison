"""LLM judge.

Design constraints, all of which exist to make a prospect's "how do I know
this is fair?" answerable:

1. No string matching anywhere. A correct answer worded differently, in
   different units, or with extra detail must score correct.
2. The judge is never told which provider produced which answer. It sees
   ANSWER A and ANSWER B and nothing else identifying.
3. Both answers are graded in ONE call so they are held to the same standard
   by the same reasoning pass, rather than drifting between two calls.
4. The A/B order is randomized per question and the assignment is recorded,
   so any residual position bias is spread across the question set instead of
   accumulating on one provider.
"""

from __future__ import annotations

import random
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from llm import chat_json

Verdict = Literal["correct", "partial", "incorrect"]

JUDGE_SYSTEM = """\
You are grading two candidate answers to the same question against a verified
reference answer. Grade strictly, fairly, and identically.

You are NOT told where either answer came from. Do not speculate about their
origin. Nothing about their source should affect your grading.

THE STANDARD
The reference answer is ground truth. Grade each candidate on whether it
conveys the same facts as the reference — not on whether it uses the same
words. If you personally believe the reference is wrong, grade against the
reference anyway and say so in your reasoning.

VERDICTS
- "correct": conveys the reference answer's key fact(s) and contradicts none
  of them. Different wording, different phrasing, different units, different
  level of detail, or additional accurate information are all still correct.
- "partial": gets some key facts right but omits others, is too vague to be
  actionable, hedges so heavily the answer is unclear, or is right about the
  main fact while contradicting a secondary one.
- "incorrect": contradicts the reference on a key fact, answers a different
  question, or fails to answer at all. An answer stating that it lacks
  sufficient context is "incorrect" — it did not answer — but say explicitly
  in your reasoning that it declined rather than that it was wrong, because
  those are different failures.

WHAT MUST NOT AFFECT THE VERDICT
- Length. A one-line answer and a paragraph are graded the same.
- Confidence or tone. Assertive phrasing is not more correct.
- Formatting, markdown, or the presence and number of citations.
- Extra correct information beyond the reference. Never penalize it.
- The other candidate. Grade each independently against the reference. One
  being better does not make the other worse. Both may be correct. Both may
  be incorrect.

REASONING
For each answer, state in one or two sentences which specific fact you
compared and why it earned that verdict. Quote the deciding phrase from the
candidate. Someone re-reading this later must be able to check your work
without rerunning anything.
"""

JUDGE_USER = """\
QUESTION:
{question}

REFERENCE ANSWER (ground truth):
{expected_answer}

ANSWER A:
{answer_a}

ANSWER B:
{answer_b}
"""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer_a", "answer_b"],
    "properties": {
        "answer_a": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "reasoning"],
            "properties": {
                "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
                "reasoning": {"type": "string"},
            },
        },
        "answer_b": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "reasoning"],
            "properties": {
                "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
                "reasoning": {"type": "string"},
            },
        },
    },
}


class Grade(BaseModel):
    verdict: Verdict
    reasoning: str
    position: Literal["A", "B"]


class JudgeResult(BaseModel):
    """Grades keyed by provider name, plus the position each one drew."""

    grades: dict[str, Grade]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def grade_pair(
    client: httpx.AsyncClient,
    *,
    question: str,
    expected_answer: str,
    answers: dict[str, str],
    model: str,
    max_completion_tokens: int = 2000,
    temperature: float = 0.0,
    rng: random.Random | None = None,
    randomize: bool = True,
) -> JudgeResult:
    """Grade both providers' answers in a single blind call.

    `answers` maps provider name to answer text. The returned grades are
    mapped back to provider names here, after the judge has responded, so the
    judge never sees a provider name.
    """
    names = list(answers)
    if len(names) != 2:
        return JudgeResult(grades={}, error=f"expected 2 answers, got {len(names)}")

    if randomize:
        (rng or random).shuffle(names)

    position_of = {names[0]: "A", names[1]: "B"}

    try:
        response = await chat_json(
            client,
            model=model,
            system=JUDGE_SYSTEM,
            user=JUDGE_USER.format(
                question=question,
                expected_answer=expected_answer,
                answer_a=answers[names[0]] or "(no answer produced)",
                answer_b=answers[names[1]] or "(no answer produced)",
            ),
            schema=JUDGE_SCHEMA,
            schema_name="grades",
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
        )
    except Exception as exc:  # noqa: BLE001 - a judge failure must not kill the run
        return JudgeResult(grades={}, error=f"{type(exc).__name__}: {exc}")

    grades: dict[str, Grade] = {}
    for provider, position in position_of.items():
        block = response["answer_a" if position == "A" else "answer_b"]
        grades[provider] = Grade(
            verdict=block["verdict"],
            reasoning=block["reasoning"],
            position=position,
        )

    return JudgeResult(grades=grades)

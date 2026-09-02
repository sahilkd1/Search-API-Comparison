# RUNBOOK

For account executives. No engineering background assumed. If you can edit a
spreadsheet and paste a command into Terminal, you can run this.

---

## What this does, in one paragraph

A prospect using Exa says "prove Parallel is better on *my* questions." You
collect their questions, put them in a spreadsheet with the right answers, and
run one command. Both search APIs get asked the same questions the same way,
an AI grader scores both answers without being told which is which, and you
get a document with the numbers in it. You walk them through that document.

The tool does not decide who wins. It reports what happened. That is the point:
a comparison the prospect can't poke holes in is worth more than one that
always flatters us.

---

## 1. Five-minute setup

You do this once, ever.

**Get the three keys.** Ask your solutions engineer, or:

| Key | Where |
| --- | --- |
| `PARALLEL_API_KEY` | https://platform.parallel.ai |
| `EXA_API_KEY` | https://dashboard.exa.ai/api-keys |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys |

**Install.** Open Terminal and paste these one at a time:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd "path/to/evalkit"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

**Add your keys.**

```bash
cp .env.example .env
open -e .env
```

Paste each key after its `=` sign, with no spaces and no quotes. Save. Close.

> `.env` is the private file with your real keys and is never shared or
> committed. `.env.example` is the blank template that *is* shared. **Never put
> a real key in `.env.example`.** If you do, rotate that key immediately.

**Check it works.**

```bash
.venv/bin/python runner.py
```

You should see a progress bar, then a summary table, then a file path. It runs
on three built-in sample questions. Two loud yellow warnings are expected and
correct — they're telling you the sample rows aren't a real test.

---

## 2. Getting good questions on a discovery call

This is the part that determines whether the whole exercise is credible. The
tool is only as good as the questions you feed it.

### What to say

> "I don't want to show you a benchmark we built to make ourselves look good.
> Give me 20 questions your agent actually has to answer in production — the
> ones you'd be embarrassed to get wrong — and I'll run them against both and
> show you everything, including the ones we lose."

Then get specific:

- "What does your agent search for on a normal Tuesday?"
- "What has your current setup gotten wrong recently?"
- "What's a question where being one day stale would be a real problem?"
- "Where do you need a citation, not just an answer?"

**Aim for 15-25 questions.** Below 10 the tool warns you, and it's right to:
with 8 questions, one lucky result moves accuracy by 12 points and the prospect
can dismiss the whole thing.

### What makes a question diagnostic

A good question **separates** the two providers. A bad question is one where
both look identical, which tells you nothing and wastes a slot.

**Bad — answerable without searching at all:**

> "What is the capital of France?"

The AI already knows this. Both providers will look perfect. Zero information.

**Bad — vague, no single right answer:**

> "Is Salesforce a good CRM?"

There's no fact to grade against. The grader will flounder and you'll deserve it.

**Bad — unverifiable or private:**

> "What is Acme Corp's internal Q3 headcount plan?"

If it isn't on a public page, web search can't find it and neither provider can
win. You're testing the wrong thing.

**Good — fresh, specific, verifiable, and not memorized:**

> "Which company did Databricks announce it was acquiring in June 2026, and for
> how much?"

Recent enough that it's not baked into the model, specific enough to have one
right answer, and sitting on a public page you can link to.

**Good — precise detail buried in a document:**

> "What is the rate limit, in requests per minute, on Stripe's live-mode API?"

The answer exists on exactly one page. Retrieval either finds it or doesn't.

**Good — requires combining two facts:**

> "Who became CTO of Shopify after the 2026 reorg, and what was their previous
> role at the company?"

Rewards search that returns genuinely relevant pages rather than merely topical
ones.

**The test:** if you can answer it confidently without looking it up, cut it.

> One warning about the three sample rows shipped in `questions.csv`: they ask
> about Parallel's and Exa's own pricing pages. They exist only to prove the
> harness runs. They are a *bad* question set for a real comparison, because a
> vendor may index its own documentation unusually well. Delete them.

---

## 3. Filling in questions.csv

Open `questions.csv` in Excel, Numbers, or Google Sheets. Four columns:

| Column | What goes in it |
| --- | --- |
| `question` | The prospect's question, exactly as they'd ask it. |
| `expected_answer` | The correct answer, in your own words. Short is fine. |
| `source_url` | The public page that proves the answer. |
| `notes` | Anything for you. Ignored by the tool. |

**Delete the three sample rows first.**

### Where ground truth comes from

`expected_answer` is what everything gets graded against, so it has to be
right. In order of preference:

1. **The primary source.** The company's own page, the official filing, the
   vendor's own docs.
2. **A reputable secondary source** that cites the primary one.
3. **The prospect themselves.** If they're telling you the right answer for
   their own domain, that's strong — and it makes the result *theirs*, which
   is worth a lot in the room.

Never write an answer from memory, and never let the AI write it. If you can't
find a URL that proves it, cut the question. A question whose ground truth you
can't defend will be the one they attack.

Don't worry about wording. The grader is explicitly instructed to score on
meaning, not phrasing. "$5 per 1,000 requests" and "five dollars per thousand
calls" both score correct.

---

## 4. Running it

```bash
.venv/bin/python runner.py
```

Takes a few seconds per question. The progress bar is there so you have
something to show on a screen share.

Useful variations:

```bash
.venv/bin/python runner.py --query-mode raw   # see section 6
.venv/bin/python runner.py --limit 5          # quick smoke test
.venv/bin/python runner.py --seed 7           # reproducible grading order
```

To compare against a different incumbent, change `incumbent:` at the top of
`config.yaml`.

Everything lands in a timestamped folder under `runs/`:

- `scorecard.md` — the document you walk them through
- `results.csv` — the same data as a spreadsheet
- `raw.jsonl` — every raw API response, for when they ask "prove it"

---

## 5. Reading the scorecard

**Summary table** — accuracy, latency, estimated cost, citation coverage,
errors. Read accuracy first; it's what they care about. Read errors second: a
provider that failed three questions had three chances to be wrong removed from
its accuracy denominator, and you should say so out loud.

**Per-question table** — one row per question, both verdicts, latency
difference. Skim it. Its job is to show nothing is hidden.

**Divergences — this is the section that matters.** Only the questions where
the two providers got *different* verdicts. Everything else is noise; if both
got it right, the question didn't separate them.

For each divergence you get both full answers, the exact search input each API
received, and the grader's full reasoning. This is where the conversation
actually happens. Don't summarize it — put it on screen and read it together.

**Spend your time in Divergences.** If there are none, say so plainly: on this
question set, the two are indistinguishable. That's a real finding and saying
it costs you nothing you weren't going to lose anyway.

---

## 6. The fairness question, and how to answer it

Sooner or later a technical buyer asks: **"Did you tune this to make Parallel
win?"** Have this answer ready, because it's the most important thing you'll
say all call.

The two APIs don't take the same input. Parallel accepts a plain-language
objective plus a few short keyword queries. Exa takes a single query string.
So "give them both the identical text" sounds fair but isn't — pasting a full
sentence into a field documented to want three-to-six-word keywords uses that
API in a way its own docs advise against.

What this tool does instead: **one AI writes the search input for both
providers, from one prompt, and that prompt is never told which vendor it's
writing for.** Each API gets the input shape its own public documentation asks
for. Nobody hand-edited either side.

And you can prove it: **every generated input is printed in the scorecard's
Divergences section and stored in `raw.jsonl`.** Offer to show them. "Same
model wrote both inputs, here they are, read them" ends the conversation.

If they push further, run it the other way in front of them:

```bash
.venv/bin/python runner.py --query-mode raw
```

That feeds the raw question to both. If the result holds up under both modes,
that's a much stronger claim than winning under one. If it doesn't hold up,
you've learned something you needed to know before they did.

---

## 7. Honest limits — say these before they do

Volunteering the weaknesses buys you more credibility than hiding them costs.
A prospect who finds a hole you didn't mention stops believing the rest.

- **Small sample.** 20 questions is 20 questions. It's evidence about *their*
  questions, not a benchmark. One question is five percentage points.
- **The grader is an AI and can be wrong.** Its reasoning is printed for every
  divergent question precisely so it can be checked. If they disagree with a
  verdict, read the reasoning together — sometimes they're right, and
  conceding a mis-grade in the room costs you almost nothing.
- **Latency is measured from your laptop**, over your network, at one moment.
  It is not a datacenter benchmark. Re-running the same questions is worse:
  vendors cache, and a second run can report latency ten times lower than the
  first. **Quote latency from the first run on a fresh question set.**
- **Costs are estimates** from published list prices, not invoices. They ignore
  any discount either vendor might offer. Say "estimated" every time.
- **Only the search step is compared.** Both providers' results go through the
  identical AI to write the final answer, so what's being measured is search
  quality, not the vendors' own answer products.

---

## 8. When the incumbent wins a question

It will happen. If it never happened, nobody should believe your tool.

**Do not skip it. Do not explain it away.** Go to it deliberately:

> "Here's one Exa got and we didn't. Let's look at why."

Then open the divergence, read both answers and the grader's reasoning, and
say what you see. Usually one of:

- **They returned a page we didn't.** Say so. "Their index had a source ours
  didn't surface for this query." That's a real, bounded weakness.
- **We returned the right page but the answer missed a detail.** Also real, and
  a different problem than a retrieval miss.
- **The grader was harsh.** Sometimes true. Say "I think that grading is
  arguable" and move on — don't fight it. Fighting one verdict makes every
  other verdict look negotiable.

Then reframe to the aggregate, without spin:

> "On these 20 questions we came out ahead on accuracy and cost, they came out
> ahead on three questions including this one. That's the real picture. If
> those three are the kind of question that matters most to you, that's worth
> knowing now rather than after you switch."

**The tool is deliberately built not to declare a winner.** No provider is
highlighted, ordered first, or bolded based on score. That's what lets you say
"I didn't build this to win, I built it to find out" — and be telling the truth.

The credibility you get from walking toward a loss is worth more than the
question was.

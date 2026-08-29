# awtoll

**Aither World Toll** — what every tool call costs you in context, measured from your own
agent transcripts rather than claimed in a README.

Every agent stack asserts that its search / graph / memory tool is cheaper than grepping
and re-reading files. Almost nobody measures it. The claim ends up in a code comment,
measured once during development and gated by nothing — so a tool can regress into costing
more than the thing it replaced and every signal stays green.

`awtoll` reads the transcripts already on your disk and prints what you actually paid.

```bash
pip install awtoll          # zero dependencies
pip install 'awtoll[exact]' # + tiktoken, for exact counts instead of estimates

awtoll scan       # toll table: what each tool shape cost
awtoll repeats    # toll you paid more than once
awtoll versus 'awgraph callers foo' 'grep -rn foo .'
awtoll check      # gate: expensive shapes decided, waste ratcheting down
```

## The unit

The **toll** is the number of tokens a tool call's *result* adds to your context. It is
deterministic, needs no API key, and can be recomputed from a transcript months later.

```
shape                                         calls    ok      total   median      p90
--------------------------------------------------------------------------------------
$ sed                                           650   645    271,868      308      827
$ grep                                          938   925    200,441      142      476
$ python -c (inline)                           1927  1800    199,267       67      219
Read                                             63    63     46,586      378    1,265
```

## The rule everything here is built around

> **A tool that answers nothing is the cheapest tool there is.**

Rank tools by cost alone and the winner is whichever one is broken. So an `ok` toll is
never averaged together with an `error`, `empty` or `truncated` one. Those are counted,
printed, and held out — and a shape whose calls are mostly not-ok is **flagged as
suspicious** rather than celebrated as cheap.

There is a sixth outcome, `opaque`, and it exists because of a real false positive on this
tool's first run: a result made of structured blocks carrying no text is *not* an empty
answer, it is a cost a transcript cannot see. Rendering it to `""` reported 36 perfectly
healthy tool-loading calls as broken. Opaque calls are excluded from the judgement, not
counted as failures.

## Repeats — the strongest savings signal

The most defensible number here is not a model of what some other tool *would* have
returned. It is the toll you demonstrably paid twice:

```
   wasted    x      each  shape / target
    8,332    2     8,332  Read :: .../resume-all.md
    3,675    8       525  Read :: .../swebench_awdk.py
```

Eight reads of one file in one session is 3,675 tokens of pure re-purchase. Repeats are
scoped to a single session on purpose — re-reading a file next week is a new question, not
waste, and counting it would flood the report.

## Amplification: the toll is not what it costs

Every turn re-sends the whole context, so a token a tool admits *early* is paid for again
on every turn after it. Measured across 12 real sessions: 1.67M tokens of tool results sat
under 205M cumulative prompt tokens — a 123x multiple.

That figure is **not** a denominator and awtoll refuses to print it as one. An earlier
version divided the two and displayed "1%", which reads as *tool output is a rounding
error* when it means the opposite.

## Counts are labelled

With `tiktoken` installed, counts are exact. Without it, awtoll uses **3.459 chars/token**
— measured across 7,690 real tool results (median 3.463; the two agree, so the constant is
stable) — and labels every number ESTIMATED. Note that the folk constant of 4.0 would
understate every toll by ~13%: tool output is denser than prose.

## Exit codes

| code | meaning |
|---|---|
| 0 | measured, nothing violated |
| 1 | a rule was violated |
| 2 | **could not judge** — no transcripts, no tool calls, unreadable ledger |

2 is never 0. A meter that found nothing and a meter that measured a healthy system look
identical unless the tool refuses to call silence a pass.

## The ledger

`awtoll check` turns the measurement into a gate. A toll table nobody decides anything
about is a dashboard: read twice, then never again.

```bash
awtoll check --init    # writes awtoll.json pinned to today's waste ratio
awtoll check           # gate
```

```json
{
  "decide_above_tokens": 50000,
  "waste_ratio_pin": 0.012,
  "decisions": {
    "$ sed": { "status": "keep", "reason": "reading file slices is the job; the alternative is a whole-file Read" },
    "Read":  { "status": "replace", "with": "awgraph context — 8 reads of one file in one session" }
  }
}
```

| rule | asserts |
|---|---|
| TL001 | every decision has a known status (`replace`, `keep`, `watch`) |
| TL002 | every status carries its second field — a reasonless decision is a hole dressed as a decision |
| TL003 | every shape above the threshold is decided |
| TL004 | no duplicate decision rows |
| TL005 | the repeat-waste ratio is pinned and ratchets **down** only |

## Reading transcripts other than Claude Code's

The default root is `~/.claude/projects`. Point somewhere else with `--root` (repeatable,
accepts a file or a directory) or `AWTOLL_TRANSCRIPTS`. The parser wants JSONL records with
`tool_use` / `tool_result` blocks paired by `tool_use_id`.

Results are paired **by id, never by adjacency**. Interleaved calls and sub-agent turns mean
the next record is frequently not the answer to the previous call, and positional pairing
produces a table that looks entirely reasonable while attributing every cost to the wrong
tool.

## Proving it still works

```bash
awtoll self-test
```

Every arm asserts a positive (the rule fires on the broken shape) and, where the rule could
over-fire, a negative (it stays quiet on the healthy one). A rule that always fires is as
useless as one that never does — the first floods and gets switched off, the second is
decoration.

## License

Apache-2.0

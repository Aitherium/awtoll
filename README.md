# awtoll

<!-- aither-header:start GENERATED from the ecosystem registry. Edits here are overwritten; change the registry instead. -->

**[Docs](https://aitherium.github.io/awtoll/)**  ·  [Source](https://github.com/Aitherium/awtoll)  ·  `pip install git+https://github.com/Aitherium/awtoll.git`  ·  [The Aither World](https://aitherium.github.io/)

> **The Aither World** is an operating system for agents — a Linux you can hand to one, the runtimes it works in, and the tools it works with. [awnix](https://github.com/Aitherium/awnix) is the Linux underneath it; **awtoll** is one of its 37 bricks — each installs on its own, runs offline, and needs no account.
>
> **Start here:** Point it at your agent transcripts and see what your ten most-used commands cost.

<!-- aither-header:end -->

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

<!-- aither-ecosystem:start GENERATED from the ecosystem registry. Edits here are overwritten; change the registry instead. -->

## The aw family

Standalone tools that share one idea: **replace something you would otherwise have to _trust_ with something you can _check_.**

Each installs on its own, works offline, and needs no account.

| | instead of trusting | you check |
|---|---|---|
| [awdk](https://github.com/Aitherium/awdk) | a framework's idea of how your agents should run | one loop you can read, pointed at a backend you already pay for |
| [awskills](https://github.com/Aitherium/awskills) | that an agent knows your procedure | the procedure written down, versioned, and loadable by any agent |
| [awpack](https://github.com/Aitherium/awpack) | that the pack you want shipped inside somebody's SDK, under whatever licence that SDK happens to carry | the pack as its own versioned artifact, with its own licence, that any agent runtime can install |
| [awm](https://github.com/Aitherium/awm) | that memory stayed in its lane | tenant:user:project scopes, so a write cannot cross a boundary |
| [awnode](https://github.com/Aitherium/awnode) | a vendor's cloud with every prompt | a local gateway routing to backends you chose |
| [awgraph](https://github.com/Aitherium/awgraph) | that grep found everything | an AST + tree-sitter call graph an agent can traverse |
| [awgit](https://github.com/Aitherium/awgit) | that no one else is editing this file | a lease, refused at commit time if you do not hold it |
| **awtoll** _(you are here)_ | that your tooling is saving you context | the measured token cost of each tool call, and what the alternative cost |
| [awseal](https://github.com/Aitherium/awseal) | that the artifact came from who you think | an Ed25519 seal — the key that verifies is not the key that forges |
| [awshare](https://github.com/Aitherium/awshare) | that the download is intact | content-addressed bundles, verified on fetch |
| [awnest](https://github.com/Aitherium/awnest) | that there is a person on the other end | a verdict with evidence, where "we could not tell" is not "yes" |
| [awnboard](https://github.com/Aitherium/awnboard) | a share link anyone who sees it can use | an invitation addressed to one person, for one gate, revocable |
| [awnix](https://github.com/Aitherium/awnix) | that the box is what you left it as | an immutable image you built, with atomic rollback |
| [awrecover](https://github.com/Aitherium/awrecover) | that the restore worked | a restore that fully lands or does not land at all |
| [awrelay](https://github.com/Aitherium/awrelay) | a SaaS in the middle of your agents | findings, alerts and coordination over your own transport |
| [awmail](https://github.com/Aitherium/awmail) | a mailbox somebody else can read | mail your agents send and receive over your own server |
| [awfind](https://github.com/Aitherium/awfind) | one vendor's idea of the web | results from whichever providers you configured |
| [awbrowse](https://github.com/Aitherium/awbrowse) | that the page said what you were told | the render, the DOM and the requests it made |
| [gobbonet-agentic](https://github.com/Aitherium/gobbonet-agentic) | the model to keep a 300-message campaign coherent by itself | campaign facts recalled from scoped memory you can list and edit |
| [aitherkvcache](https://github.com/Aitherium/aitherkvcache) | a vendor's quantisation defaults | sub-byte KV cache kernels you can benchmark yourself |
| [AitherZero](https://github.com/Aitherium/AitherZero) | a pile of scripts nobody has numbered | numbered, discoverable automation with declarative playbooks |
| [AitherConnect](https://github.com/Aitherium/AitherConnect) | what a page tells your browser to do | a federated search and desktop bridge you host |
| [awreason](https://github.com/Aitherium/awreason) | a confident paragraph | the phases it went through, and every tool call it made to get there |
| [awrecurse](https://github.com/Aitherium/awrecurse) | that everything you pasted in was actually read | which slices it opened, and what it concluded from each |
| [awprism](https://github.com/Aitherium/awprism) | the first explanation that fits | the ranked alternatives, and the observation that separates them |
| [awrepl](https://github.com/Aitherium/awrepl) | what the agent believes the value is | the value, printed from the live session |
| [awresearch](https://github.com/Aitherium/awresearch) | a summary of pages nobody opened | every claim against the source it came from |
| [awpredict](https://github.com/Aitherium/awpredict) | a model because it trained without erroring | its prediction against a self-updating lookup, on the rows that are actually novel |
| [awsh](https://github.com/Aitherium/awsh) | that you already know the name of the command | what it decided your line meant, before it acts on it |
| [awkno](https://github.com/Aitherium/awkno) | that the docs site is up, or that you remember the family | the whole ecosystem in your terminal, with no network at all |

[**awnix**](https://github.com/Aitherium/awnix) is the ground floor — A Linux you can hand to an agent — immutable base, capabilities included.

## The Aitherium ecosystem

Every repository here is public. Each publishes an `aither-manifest.json` beside its page, so any surface can read every sibling's — the network is browsable from any node in it.

| repo | what it is | pages |
|---|---|---|
| [awdk](https://github.com/Aitherium/awdk) | Build AI agent fleets — 3 lines, any backend, local or cloud | [docs](https://aitherium.github.io/awdk/) |
| [awskills](https://github.com/Aitherium/awskills) | Portable agent skills — self-contained procedures an agent loads on demand | [docs](https://aitherium.github.io/awskills/) |
| [awpack](https://github.com/Aitherium/awpack) | First-party agent packs — the ones we build, versioned and installable on their own | — |
| [awm](https://github.com/Aitherium/awm) | A portable, scoped agent memory | [docs](https://aitherium.github.io/awm/) |
| [awnode](https://github.com/Aitherium/awnode) | A lightweight local gateway — bridges your apps to the AI backends you chose | [docs](https://aitherium.github.io/awnode/) |
| [awrun](https://github.com/Aitherium/awrun) | A priority-aware queue and dispatcher for agentic runs and ad-hoc CI builds. It also judges whether the runner pool is big enough for the queue it is draining, and can ask a host to grow it -- reserving capacity is zero-sum, so a saturated pool needs more of it, not a different share of it | [docs](https://aitherium.github.io/awrun/) |
| [awgraph](https://github.com/Aitherium/awgraph) | A semantic code graph for agents — AST + tree-sitter, call graphs | [docs](https://aitherium.github.io/awgraph/) |
| [awgit](https://github.com/Aitherium/awgit) | Semantic version control on top of git — edit-ops and leases | [docs](https://aitherium.github.io/awgit/) |
| **awtoll** _(you are here)_ | What every tool call costs you in context, measured from your own transcripts | [docs](https://aitherium.github.io/awtoll/) |
| [awseal](https://github.com/Aitherium/awseal) | Sign an artifact so a stranger can verify it | [docs](https://aitherium.github.io/awseal/) |
| [awshare](https://github.com/Aitherium/awshare) | Publish an artifact and fetch it back verified | [docs](https://aitherium.github.io/awshare/) |
| [awdit](https://github.com/Aitherium/awdit) | An append-only audit trail whose gaps are DETECTABLE | [docs](https://aitherium.github.io/awdit/) |
| [awbac](https://github.com/Aitherium/awbac) | Role-based access control that fails closed and explains itself | [docs](https://aitherium.github.io/awbac/) |
| [awiam](https://github.com/Aitherium/awiam) | Who is this caller? A directory and session store that fails honestly | [docs](https://aitherium.github.io/awiam/) |
| [awtunnel](https://github.com/Aitherium/awtunnel) | Reach a service that has no public address | [docs](https://aitherium.github.io/awtunnel/) |
| [awnest](https://github.com/Aitherium/awnest) | Prove there is a human before you let them into the nest | [docs](https://aitherium.github.io/awnest/) |
| [awnboard](https://github.com/Aitherium/awnboard) | A front gate you can put in front of anything, and hand someone the key to | [docs](https://aitherium.github.io/awnboard/) |
| [awnix](https://github.com/Aitherium/awnix) | A Linux you can hand to an agent — immutable base, capabilities included | [docs](https://aitherium.github.io/awnix/) |
| [awrecover](https://github.com/Aitherium/awrecover) | Labelled snapshots with an all-or-nothing restore | [docs](https://aitherium.github.io/awrecover/) |
| [awrelay](https://github.com/Aitherium/awrelay) | Portable agent messaging — findings, alerts, coordination | [docs](https://aitherium.github.io/awrelay/) |
| [awmail](https://github.com/Aitherium/awmail) | Give an agent an email address — send, and actually receive | [docs](https://aitherium.github.io/awmail/) |
| [awnet](https://github.com/Aitherium/awnet) | The agentic web — agents host a mesh, and agents join one | [docs](https://aitherium.github.io/awnet/) |
| [awfind](https://github.com/Aitherium/awfind) | A portable search client — query, results, ranking | [docs](https://aitherium.github.io/awfind/) |
| [awbrowse](https://github.com/Aitherium/awbrowse) | A portable browser client — navigate, console, network, DOM, screenshot | [docs](https://aitherium.github.io/awbrowse/) |
| [awknowledge](https://github.com/Aitherium/awknowledge) | How to run a coding agent so the result survives — the laws, with evidence | [docs](https://aitherium.github.io/awknowledge/) |
| [gobbonet-agentic](https://github.com/Aitherium/gobbonet-agentic) | GobboNet campaigns with a real agent brain — scoped memory, graph recall | — |
| [aitherkvcache](https://github.com/Aitherium/aitherkvcache) | Near-optimal KV cache quantization for LLM inference — sub-byte compression | [docs](https://aitherium.github.io/aitherkvcache/) |
| [AitherZero](https://github.com/Aitherium/AitherZero) | PowerShell 7+ automation framework — numbered, self-describing scripts | [docs](https://aitherium.github.io/AitherZero/) |
| [AitherConnect](https://github.com/Aitherium/AitherConnect) | Browser extension — federated AI search, page context, and the Living OS overlay | [docs](https://aitherium.github.io/AitherConnect/) |
| [awreason](https://github.com/Aitherium/awreason) | A portable reasoning client — sessions, phases, thoughts, and the chain that produced the answer | [docs](https://aitherium.github.io/awreason/) |
| [awrecurse](https://github.com/Aitherium/awrecurse) | Answer a question over a context far larger than the window — recursively, with the trace kept | [docs](https://aitherium.github.io/awrecurse/) |
| [awprism](https://github.com/Aitherium/awprism) | Turn a failure into ranked hypotheses — and say what would confirm each one | [docs](https://aitherium.github.io/awprism/) |
| [awrepl](https://github.com/Aitherium/awrepl) | A REPL an agent can actually use — state that survives between turns | [docs](https://aitherium.github.io/awrepl/) |
| [awresearch](https://github.com/Aitherium/awresearch) | Ask a research question, get a cited report you can check | [docs](https://aitherium.github.io/awresearch/) |
| [awpredict](https://github.com/Aitherium/awpredict) | Predict what your environment does next, and how surprised you were | [docs](https://aitherium.github.io/awpredict/) |
| [awsh](https://github.com/Aitherium/awsh) | Your terminal answers you -- type a question where a command would go | — |
| [awkno](https://github.com/Aitherium/awkno) | The man page for the Aither World — every brick, stack and law, offline | [docs](https://aitherium.github.io/awkno/) |

<div id="aither-constellation" data-self="awtoll"></div>
<script src="aither-constellation.js"></script>

<!-- aither-ecosystem:end -->

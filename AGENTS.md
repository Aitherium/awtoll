# awtoll for agents

Read this if you are an agent (or a human) editing this package. Short on
purpose: the commands, the traps that cost a session, and where the rest lives.
Nothing here is read at runtime — it is for you.

## What this is

PyPI distribution **`awtoll`** (version in `pyproject.toml`), import package
`awtoll`, Python >= 3.10. What every tool call costs you in context, measured
from your own transcripts. Every agent stack claims its search/graph/memory
tool is cheaper than grepping — this is the brick that checks.

This repository is a **synced mirror** of the AitherOS monorepo (lane
`.github/workflows/sync-awtoll.yml`). Hand edits made here are overwritten on
the next sync — change the source and let the lane publish.

## Build, test, verify

```bash
python -m pytest tests -q        # the suite: 26 tests, green at v0.1.1
pip install -e .                 # editable install for developing against it
```

The suite was run from a source checkout with no prior install. The publish
lane (`publish-brick.yml`) additionally builds the wheel, installs it and
imports it — a tree that tests green can still ship a broken wheel.

## Rules that keep this useful

- **Every number is generated from transcripts, never typed.** The failure
  this brick exists to catch is a cost claim measured once and then gated by
  nothing; a hardcoded figure in this repo would be the same defect wearing
  the tool's own name. `test_awtoll.py` pins the accounting paths.
- **A tool that returns less looks identical to a tool that saves you
  something.** Keep the measurement shaped so those two are distinguishable —
  cost AND what the call was for, per call.
- **Compare like with like.** Session, model and harness go into the record;
  a number quoted across them without saying so is wrong in the direction
  that flatters the tool.
- **The registry drives the public surface.** This repo's README header,
  `llms.txt` and `aither-manifest.json` are generated from the ecosystem
  registry (one yaml in the AitherOS monorepo) and rewritten on every sync.
  Change the registry; do not hand-edit the generated blocks.

## Read next

- `llms.txt` — the install/use card written for an agent to execute
- `README.md` — the human front door
- `docs/` — the generated docs site source

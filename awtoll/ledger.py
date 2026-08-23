"""The decision ledger: what you concluded about each expensive shape.

A toll table is a measurement, and a measurement nobody decides anything about
is a dashboard -- it gets read twice and then never again. The ledger is the
half that makes it a gate: every shape above the threshold must carry a
recorded decision, and the waste ratio is pinned and may only ratchet DOWN.

Deliberately JSON and zero-dependency. This has to run on a stranger's machine
with nothing installed; a decision ledger that needs a YAML parser to be read
is one import away from being unreadable in the situation you most want it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

LEDGER_NAME = "awtoll.json"

# A decision is one of these, and each demands its own second field. A status
# with nothing behind it is a hole dressed up as a decision -- the failure
# every ledger of this shape has had at least once.
STATUSES = {
    "replace": "with",   # this shape should be served by a cheaper tool: which one
    "keep": "reason",    # this cost is correct and worth paying: why
    "watch": "reason",   # not decided yet, but deliberately so: why
}

DEFAULTS: Dict[str, Any] = {
    # Chosen by --init so that exactly `undecided_budget` shapes sit above it.
    # A fixed 50,000 opened this gate at 40+ findings on a real 14-day window,
    # and a gate that opens red gets bypassed rather than satisfied -- which is
    # how a repo ends up with per-file lint ignores. The threshold RATCHETS
    # DOWN as decisions land: that is how coverage widens.
    "decide_above_tokens": 50_000,
    "undecided_budget": 5,
    "waste_ratio_pin": None,   # set on first run; ratchets DOWN only
    "decisions": {},
}


@dataclass
class Finding:
    rule: str
    detail: str


@dataclass
class Ledger:
    path: Path
    data: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    existed: bool = False

    @property
    def decide_above(self) -> int:
        try:
            return int(self.data.get("decide_above_tokens") or DEFAULTS["decide_above_tokens"])
        except (TypeError, ValueError):
            return DEFAULTS["decide_above_tokens"]

    @property
    def waste_pin(self) -> Optional[float]:
        v = self.data.get("waste_ratio_pin")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def budget(self) -> int:
        try:
            return max(int(self.data.get("undecided_budget") or 5), 1)
        except (TypeError, ValueError):
            return 5

    @property
    def decisions(self) -> Dict[str, Any]:
        d = self.data.get("decisions")
        return d if isinstance(d, dict) else {}


def load(path: Path) -> Ledger:
    """Load a ledger. A malformed one RAISES rather than defaulting.

    Defaulting a broken ledger to empty would silently discard every recorded
    decision and every pin, and the run would then pass -- turning a typo into
    a clean bill of health.
    """
    if not path.exists():
        return Ledger(path=path, existed=False)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be an object")
    data = dict(DEFAULTS)
    data.update(raw)
    return Ledger(path=path, data=data, existed=True)


def save(ledger: Ledger) -> None:
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(
        json.dumps(ledger.data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def check(ledger: Ledger, report) -> List[Finding]:
    """Every rule. Returns findings; an empty list is a pass."""
    out: List[Finding] = []
    decisions = ledger.decisions

    seen = set()
    for shape, row in decisions.items():
        if shape in seen:
            out.append(Finding("TL004", f"duplicate decision for shape {shape!r}"))
        seen.add(shape)
        if not isinstance(row, dict):
            out.append(Finding("TL001", f"{shape!r}: decision must be an object"))
            continue
        status = row.get("status")
        if status not in STATUSES:
            out.append(
                Finding(
                    "TL001",
                    f"{shape!r}: status {status!r} is not one of {sorted(STATUSES)}",
                )
            )
            continue
        required = STATUSES[status]
        val = row.get(required)
        if not isinstance(val, str) or not val.strip():
            out.append(
                Finding(
                    "TL002",
                    f"{shape!r}: status {status!r} requires a non-empty {required!r} "
                    "-- a decision with nothing behind it is a hole dressed as a decision",
                )
            )

    # TL003 -- every expensive shape is decided.
    threshold = ledger.decide_above
    for st in report.shapes:
        if st.ok_tokens < threshold:
            continue
        if st.shape not in decisions:
            out.append(
                Finding(
                    "TL003",
                    f"{st.shape!r} cost {st.ok_tokens:,} tokens over {st.ok_calls} calls "
                    f"and carries no decision (threshold {threshold:,})",
                )
            )

    # TL005 -- the waste ratio ratchets DOWN only.
    pin = ledger.waste_pin
    if pin is not None:
        actual = report.waste_ratio
        # A hair of tolerance: the ratio moves with session mix, and a gate
        # that goes red on noise gets switched off rather than satisfied.
        if actual > pin + 0.01:
            out.append(
                Finding(
                    "TL005",
                    f"repeat waste is {actual:.1%} against a pin of {pin:.1%} "
                    "-- the same results are being fetched more than once, more than before",
                )
            )
    return out


def suggest_pin(report) -> float:
    return round(report.waste_ratio, 4)


def suggest_threshold(report, budget: int) -> int:
    """A threshold that leaves exactly `budget` shapes to decide."""
    tolls = sorted((s.ok_tokens for s in report.shapes), reverse=True)
    if len(tolls) <= budget:
        return 1
    return tolls[budget] + 1


def unjudged(ledger: Ledger, report) -> tuple:
    """(count, tokens) of shapes this gate is deliberately NOT judging.

    Printed on every run. A run that bounds its own coverage without saying so
    reads as "covered everything", which is the failure this whole family of
    checks exists to avoid.
    """
    below = [
        s
        for s in report.shapes
        if s.ok_tokens < ledger.decide_above and s.shape not in ledger.decisions
    ]
    return len(below), sum(s.ok_tokens for s in below)

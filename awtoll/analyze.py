"""Aggregate tool calls into a toll table, and find the toll you paid twice.

The one rule that governs every number here:

    A TOOL THAT ANSWERS NOTHING IS THE CHEAPEST TOOL THERE IS.

Rank shapes by cost alone and the winner is whichever tool is broken. So an
`ok` toll is never mixed with an `error`, `empty` or `truncated` one -- those
are counted, printed and kept out of the average. A shape whose calls are
mostly not-ok is FLAGGED as suspicious rather than celebrated as cheap.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .shapes import shape_of, target_of
from .toll import Tokenizer, count_result, has_image
from .transcripts import Session

# A shape whose useful calls are this rare is not cheap, it is failing.
SUSPICIOUS_OK_RATIO = 0.5


@dataclass
class ShapeStat:
    shape: str
    calls: int = 0
    ok_calls: int = 0
    ok_tokens: int = 0
    tolls: List[int] = field(default_factory=list)  # ok calls only
    by_outcome: Dict[str, int] = field(default_factory=dict)
    images: int = 0
    opaque: int = 0
    sessions: set = field(default_factory=set)

    @property
    def median(self) -> int:
        return int(statistics.median(self.tolls)) if self.tolls else 0

    @property
    def p90(self) -> int:
        if not self.tolls:
            return 0
        if len(self.tolls) < 10:
            return max(self.tolls)
        return int(statistics.quantiles(self.tolls, n=10)[8])

    @property
    def judged(self) -> int:
        """Calls whose outcome this tool can actually judge.

        OPAQUE and UNPAIRED calls are excluded. A textless structured result is
        not a failure, it is a cost this tool cannot see -- and counting it as
        a failure is how the first real run reported 36 healthy ToolSearch
        calls as broken.
        """
        unknown = self.by_outcome.get("opaque", 0) + self.by_outcome.get("unpaired", 0)
        return max(self.calls - unknown, 0)

    @property
    def ok_ratio(self) -> float:
        return (self.ok_calls / self.judged) if self.judged else 0.0

    @property
    def suspicious(self) -> bool:
        """Cheap because it is not answering, rather than cheap because it is good."""
        return self.judged >= 3 and self.ok_ratio < SUSPICIOUS_OK_RATIO


@dataclass
class Repeat:
    """The same answer fetched more than once. This is toll you paid twice."""

    shape: str
    target: str
    times: int
    tokens_each: int
    session_id: str

    @property
    def wasted(self) -> int:
        return self.tokens_each * (self.times - 1)


@dataclass
class Report:
    tokenizer: str
    estimated: bool
    sessions: int
    calls: int
    paired: int
    total_ok_tokens: int
    shapes: List[ShapeStat]
    repeats: List[Repeat]
    # Runtime ground truth, so the toll table can be checked against reality
    # instead of being trusted on its own arithmetic.
    runtime_prompt_tokens: int
    unreadable_lines: int
    undecidable_shapes: int

    @property
    def wasted_tokens(self) -> int:
        return sum(r.wasted for r in self.repeats)

    @property
    def waste_ratio(self) -> float:
        return (self.wasted_tokens / self.total_ok_tokens) if self.total_ok_tokens else 0.0

    @property
    def resend_factor(self) -> float:
        """How many times the runtime re-sent, on average, each token it held.

        NOT a share of the bill, and the first version of this field WAS one --
        it divided toll by cumulative prompt tokens and printed "1%", which
        reads as "tool output is a rounding error" when it means the opposite.
        Every turn re-sends the whole context, so cumulative prompt tokens
        counts each admitted token once per subsequent turn. That makes the
        ratio an AMPLIFIER, not a denominator: a token a tool admits early in a
        session is paid for again on every turn that follows it.
        """
        return (self.runtime_prompt_tokens / self.total_ok_tokens) if self.total_ok_tokens else 0.0


def analyze(
    sessions: Iterable[Session],
    tok: Tokenizer,
    min_repeat_tokens: int = 200,
) -> Report:
    shapes: Dict[str, ShapeStat] = {}
    repeats: List[Repeat] = []
    calls = paired = total_ok = 0
    rt_cache = 0
    unreadable = 0
    undecidable = 0
    n_sessions = 0

    for sess in sessions:
        n_sessions += 1
        rt_cache += sess.cache_creation_tokens
        unreadable += sess.unreadable_lines
        # (shape, target) -> [tolls] within THIS session. Repeats are scoped to
        # a session on purpose: re-reading a file a week later is not waste,
        # it is a new question.
        seen: Dict[Tuple[str, str], List[int]] = {}

        for call in sess.calls:
            calls += 1
            shape = shape_of(call.tool, call.inp)
            if shape.endswith("(undecidable)"):
                undecidable += 1
            st = shapes.setdefault(shape, ShapeStat(shape=shape))
            st.calls += 1
            st.sessions.add(sess.session_id)
            outcome = call.outcome
            st.by_outcome[outcome] = st.by_outcome.get(outcome, 0) + 1
            if call.paired:
                paired += 1
            if has_image(call.result_text):
                st.images += 1
            if outcome == "opaque":
                st.opaque += 1
            if outcome != "ok":
                continue

            toll = count_result(tok, call.result_text)
            st.ok_calls += 1
            st.ok_tokens += toll
            st.tolls.append(toll)
            total_ok += toll

            target = target_of(call.tool, call.inp)
            if target and toll >= min_repeat_tokens:
                seen.setdefault((shape, target), []).append(toll)

        for (shape, target), tolls in seen.items():
            if len(tolls) < 2:
                continue
            repeats.append(
                Repeat(
                    shape=shape,
                    target=target,
                    times=len(tolls),
                    tokens_each=int(statistics.median(tolls)),
                    session_id=sess.session_id,
                )
            )

    ordered = sorted(shapes.values(), key=lambda s: s.ok_tokens, reverse=True)
    repeats.sort(key=lambda r: r.wasted, reverse=True)
    return Report(
        tokenizer=tok.describe(),
        estimated=tok.estimated,
        sessions=n_sessions,
        calls=calls,
        paired=paired,
        total_ok_tokens=total_ok,
        shapes=ordered,
        repeats=repeats,
        runtime_prompt_tokens=rt_cache,
        unreadable_lines=unreadable,
        undecidable_shapes=undecidable,
    )


def find_shape(report: Report, shape: str) -> Optional[ShapeStat]:
    for s in report.shapes:
        if s.shape == shape:
            return s
    return None

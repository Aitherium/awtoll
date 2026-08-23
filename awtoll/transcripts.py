"""Read agent transcripts and pair every tool call with what its result cost.

The unit of measurement is the TOLL: the number of tokens a tool call's RESULT
adds to the context window. That is the thing you actually pay for, it is
deterministic, and it can be recomputed from a transcript months later without
an API key.

Two decisions here are load-bearing and easy to get wrong:

1. **A result is paired to its call by id, never by adjacency.** Tool results
   arrive as `user` records carrying a `tool_use_id`; interleaved calls and
   sub-agent turns mean the next `user` record is frequently NOT the answer to
   the previous `tool_use`. Pairing by position produces a table that looks
   completely reasonable and attributes each cost to the wrong tool.

2. **An unpaired call is recorded as unpaired, never dropped.** A call whose
   result never arrived (the session was interrupted, the tool was denied) has
   an unknown toll, and unknown is not zero. Dropping it silently makes an
   expensive shape look cheap in exactly the sessions where it misbehaved.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .toll import IMAGE_SENTINEL

# Claude Code writes one JSONL per session under a per-project directory.
# Other agent runtimes are read by pointing --root at their transcript tree;
# nothing below assumes this location, it is only the default.
DEFAULT_ROOT = Path.home() / ".claude" / "projects"

# Markers a runtime leaves when it cuts a result short. A truncated result is
# CHEAP and that cheapness is a lie: the bytes you needed were dropped and you
# pay again on the re-run. Counted separately, never averaged in as a win.
TRUNCATION_MARKERS = (
    "[truncated]",
    "... (truncated)",
    "output truncated",
    "results truncated",
    "(showing first",
    "lines were truncated",
)


@dataclass
class ToolCall:
    """One tool invocation and the cost of its answer."""

    session_id: str
    seq: int
    tool: str
    inp: Dict[str, Any]
    result_text: Optional[str] = None  # None == never paired
    is_error: bool = False
    nontext_blocks: int = 0  # structured blocks carrying no text (see _text_of)

    @property
    def paired(self) -> bool:
        return self.result_text is not None

    @property
    def result_chars(self) -> int:
        return len(self.result_text or "")

    @property
    def truncated(self) -> bool:
        if not self.result_text:
            return False
        low = self.result_text[-4000:].lower()
        return any(m in low for m in TRUNCATION_MARKERS)

    @property
    def outcome(self) -> str:
        """One of: unpaired, error, empty, opaque, truncated, ok.

        The ORDER matters. `error` and `empty` are checked before `ok` because
        a call that failed or answered nothing is the cheapest possible call,
        and a report that ranks by cost alone would crown it. Every consumer
        buckets on this field rather than averaging over all calls.
        """
        if self.result_text is None:
            return "unpaired"
        if self.is_error:
            return "error"
        if not self.result_text.strip():
            # Structured-but-textless is OPAQUE, never empty. See _text_of.
            return "opaque" if self.nontext_blocks else "empty"
        if self.truncated:
            return "truncated"
        return "ok"


@dataclass
class Session:
    """One transcript: its calls, plus the runtime's own token accounting."""

    path: Path
    session_id: str
    calls: List[ToolCall] = field(default_factory=list)
    # Ground truth from the runtime, used to sanity-check the toll table
    # against reality rather than trusting our own arithmetic.
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    assistant_turns: int = 0
    unreadable_lines: int = 0


def discover(roots: Optional[Iterable[Path]] = None) -> List[Path]:
    """Every transcript under the given roots, newest first."""
    out: List[Path] = []
    for root in [Path(r) for r in (roots or [DEFAULT_ROOT])]:
        if root.is_file() and root.suffix == ".jsonl":
            out.append(root)
            continue
        if not root.is_dir():
            continue
        out.extend(p for p in root.rglob("*.jsonl") if p.is_file())
    out.sort(key=lambda p: _mtime(p), reverse=True)
    return out


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _text_of(content: Any) -> tuple:
    """Flatten a tool_result's content into (text, nontext_block_count).

    The block count is what stops the parser from inventing failures. A result
    can be a list of blocks carrying NO text field at all -- `tool_reference`
    blocks from a tool-loading call are the live example -- and rendering that
    to "" would classify a perfectly working tool as having answered nothing.
    Measured: doing exactly that flagged 36 healthy ToolSearch calls as broken
    on this tool's first real run. The blocks are counted instead and the call
    is reported as `opaque`: its cost is real and NOT VISIBLE from a
    transcript, which is a third state -- not a failure, and not a zero.
    """
    if content is None:
        return "", 0
    if isinstance(content, str):
        return content, 0
    if isinstance(content, list):
        parts = []
        nontext = 0
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") == "image":
                    # An image costs real tokens but not text ones; recording 0
                    # here would understate it, so it is marked and counted by
                    # the caller as a non-text result rather than as free.
                    parts.append(IMAGE_SENTINEL)
                else:
                    nontext += 1
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts), nontext
    return str(content), 0


def parse_session(path: Path) -> Optional[Session]:
    """Parse one transcript. Returns None only if nothing at all could be read."""
    session_id = path.stem
    sess = Session(path=path, session_id=session_id)
    pending: Dict[str, ToolCall] = {}
    seq = 0
    any_line = False

    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            any_line = True
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                sess.unreadable_lines += 1
                continue
            if not isinstance(rec, dict):
                sess.unreadable_lines += 1
                continue

            msg = rec.get("message")
            msg = msg if isinstance(msg, dict) else {}
            rtype = rec.get("type")

            if rtype == "assistant":
                sess.assistant_turns += 1
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    sess.cache_creation_tokens += _int(usage.get("cache_creation_input_tokens"))
                    sess.output_tokens += _int(usage.get("output_tokens"))
                for block in _blocks(msg.get("content")):
                    if block.get("type") != "tool_use":
                        continue
                    seq += 1
                    call = ToolCall(
                        session_id=session_id,
                        seq=seq,
                        tool=str(block.get("name") or "unknown"),
                        inp=block.get("input") if isinstance(block.get("input"), dict) else {},
                    )
                    sess.calls.append(call)
                    tid = block.get("id")
                    if isinstance(tid, str):
                        pending[tid] = call

            elif rtype == "user":
                for block in _blocks(msg.get("content")):
                    if block.get("type") != "tool_result":
                        continue
                    tid = block.get("tool_use_id")
                    call = pending.pop(tid, None) if isinstance(tid, str) else None
                    if call is None:
                        # A result for a call we never saw. Real: sub-agent
                        # transcripts and resumed sessions both do it. It is
                        # not attributable to a shape, so it is not counted.
                        continue
                    call.result_text, call.nontext_blocks = _text_of(block.get("content"))
                    call.is_error = bool(block.get("is_error"))

    if not any_line:
        return None
    return sess


def _blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def load(
    roots: Optional[Iterable[Path]] = None,
    limit: Optional[int] = None,
    since_days: Optional[float] = None,
) -> List[Session]:
    """Discover and parse transcripts, newest first."""
    paths = discover(roots)
    if since_days is not None:
        import time

        cutoff = time.time() - (since_days * 86400.0)
        paths = [p for p in paths if _mtime(p) >= cutoff]
    if limit is not None:
        paths = paths[:limit]
    out = []
    for p in paths:
        s = parse_session(p)
        if s is not None:
            out.append(s)
    return out


def default_roots_from_env() -> List[Path]:
    env = os.environ.get("AWTOLL_TRANSCRIPTS")
    if env:
        return [Path(x) for x in env.split(os.pathsep) if x]
    return [DEFAULT_ROOT]

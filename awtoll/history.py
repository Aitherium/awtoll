"""Append-only JSONL history of awtoll check runs.

Every run writes a record with comparable metrics. History answers the question:
are we getting better? History must be honest about what is and isn't
comparable across runs, because a trend line that claims more precision than
it has is the failure mode this exists to prevent.

Session count and call count vary run-to-run, so absolute token counts are
not directly comparable. Only ratios (waste_ratio, ok_ratio per shape) move
independently of session mix.

Repeats are session-scoped by design, so a time-series comparison is at
session-level granularity minimum: the same file read once per session is
not waste. A comparison across sessions that shows 'waste went down' means
fewer within-session re-fetches, not fewer absolute tokens.

Ledger pins ratchet DOWN only (TL005); history records every run, passing
and failing alike. History tracks pass/fail separately so a violation can
be read in context.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Version of the history format and shape detection. If the shape algorithm
# changes (added wrappers to strip, new detection rules), increment this.
# Old records with a different version are still readable but flagged as
# potentially incomparable.
HISTORY_VERSION = 1


@dataclass
class HistoryRecord:
    """One run of `awtoll check`.

    Only ratios and counts are recorded because they remain meaningful
    across session-mix changes. Absolute token counts drift with load.
    """
    timestamp: str  # ISO 8601
    version: int  # shape detection / history format version
    sessions: int
    calls: int
    waste_ratio: float
    wasted_tokens: int
    total_ok_tokens: int
    shapes_count: int
    repeats_count: int
    ledger_pass: bool  # check() ran AND returned no findings
    ledger_findings_count: Optional[int]  # count if not passed
    ledger_waste_pin: Optional[float]  # the pin at time of run
    ledger_waste_violation: Optional[bool]  # was waste_ratio > pin + 0.01
    #: Set when check() RAISED. Distinguishes "passed" from "could not judge":
    #: without it an empty findings list reads as a pass, so a crashed check
    #: would be written into the history as a clean run. Defaulted, so it goes
    #: LAST -- a dataclass refuses a non-default field after a defaulted one.
    check_error: Optional[str] = None


@dataclass
class HistoryStats:
    """Comparable metrics from a set of history records."""
    records: List[HistoryRecord]
    # Minimum sessions in the set (e.g., if one run used --limit 5 sessions)
    min_sessions: int
    # Is this a valid time series (all same version, enough records)?
    comparable: bool
    comparable_reason: str = ""


def default_history_path(ledger_path: Path) -> Path:
    """History location: next to the ledger, with a standard name.

    If ledger is ./awtoll.json, history is ./awtoll-history.jsonl
    """
    return ledger_path.parent / (ledger_path.stem + "-history.jsonl")


def record_from_check(report, ledger, path: Path) -> HistoryRecord:
    """Create a history record from a check run."""
    findings = []
    check_error: str | None = None
    try:
        from .ledger import check
        findings = check(ledger, report)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        # Recording the run beats crashing the command -- but an empty findings
        # list means "the ledger passed", so swallowing the error here would
        # write a CRASHED check into the history as a clean pass. That is the
        # silent no-op this whole brick exists to catch, in the one file whose
        # job is to be trustworthy about the past. The error is recorded, and
        # ledger_pass is False rather than vacuously True.
        check_error = f"{type(exc).__name__}: {exc}"[:200]

    waste_violation = None
    pin = ledger.waste_pin
    if pin is not None:
        actual = report.waste_ratio
        waste_violation = actual > pin + 0.01

    return HistoryRecord(
        timestamp=datetime.utcnow().isoformat() + "Z",
        version=HISTORY_VERSION,
        sessions=report.sessions,
        calls=report.calls,
        waste_ratio=round(report.waste_ratio, 4),
        wasted_tokens=report.wasted_tokens,
        total_ok_tokens=report.total_ok_tokens,
        shapes_count=len(report.shapes),
        repeats_count=len(report.repeats),
        ledger_pass=(check_error is None and len(findings) == 0),
        check_error=check_error,
        ledger_findings_count=len(findings) if findings else None,
        ledger_waste_pin=round(pin, 4) if pin is not None else None,
        ledger_waste_violation=waste_violation,
    )


def append_record(record: HistoryRecord, path: Path) -> None:
    """Append a record to the history file.

    The file is JSONL: one JSON object per line. Atomicity is not guaranteed
    (a partial line on crash is possible), so load() skips unreadable lines
    rather than failing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record)) + "\n")


def load_history(path: Path) -> tuple[List[HistoryRecord], int]:
    """Load history from JSONL file.

    Returns (records, unreadable_count). Unreadable lines are counted and
    reported but do not stop the load -- silence about corruption is the
    failure this exists to prevent.
    """
    records = []
    unreadable = 0

    if not path.exists():
        return records, 0

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            rec = HistoryRecord(
                timestamp=data.get("timestamp", ""),
                version=data.get("version", HISTORY_VERSION),
                sessions=int(data.get("sessions", 0)),
                calls=int(data.get("calls", 0)),
                waste_ratio=float(data.get("waste_ratio", 0.0)),
                wasted_tokens=int(data.get("wasted_tokens", 0)),
                total_ok_tokens=int(data.get("total_ok_tokens", 0)),
                shapes_count=int(data.get("shapes_count", 0)),
                repeats_count=int(data.get("repeats_count", 0)),
                ledger_pass=bool(data.get("ledger_pass", False)),
                ledger_findings_count=data.get("ledger_findings_count"),
                ledger_waste_pin=data.get("ledger_waste_pin"),
                ledger_waste_violation=data.get("ledger_waste_violation"),
            )
            records.append(rec)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            unreadable += 1

    return records, unreadable


def validate_comparability(records: List[HistoryRecord]) -> HistoryStats:
    """Validate whether a set of records form a comparable time series.

    Reasons a set is not comparable:
    - fewer than 2 records
    - different version (shape detection changed)
    - session counts vary too much (use ratios, not absolutes)

    A trend line that claims precision it does not have is the failure;
    this function refuses to produce one.
    """
    if len(records) < 2:
        return HistoryStats(
            records=records,
            min_sessions=0,
            comparable=False,
            comparable_reason="fewer than 2 records in history",
        )

    versions = {r.version for r in records}
    if len(versions) > 1:
        return HistoryStats(
            records=records,
            min_sessions=min(r.sessions for r in records),
            comparable=False,
            comparable_reason=f"shape versions differ: {versions}. "
                             "Reindex history or start fresh.",
        )

    min_sessions = min(r.sessions for r in records)
    max_sessions = max(r.sessions for r in records)
    # Allow 2x variation in session count (reasonable for varied workloads)
    # Beyond that, absolute numbers drift too much to claim a trend.
    # Special case: if min is 1 and max is >2, that's still too much drift.
    if max_sessions > 2 * min_sessions:
        return HistoryStats(
            records=records,
            min_sessions=min_sessions,
            comparable=False,
            comparable_reason=f"session count varies {min_sessions} to {max_sessions} "
                             "(>2x difference). Use ratios (waste_ratio, ok_ratio), not absolutes.",
        )

    return HistoryStats(
        records=records,
        min_sessions=min_sessions,
        comparable=True,
        comparable_reason="",
    )

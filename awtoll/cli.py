"""awtoll -- what every tool call costs you in context.

Exit codes, and they are the point:
    0  measured, nothing violated
    1  a rule was violated
    2  COULD NOT JUDGE -- no transcripts, no tool calls, unreadable ledger

2 is never 0. A meter that found nothing to measure and a meter that measured a
healthy system produce identical output unless the tool refuses to call silence
a pass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .analyze import Report, analyze
from .history import (
    append_record,
    default_history_path,
    load_history,
    record_from_check,
    validate_comparability,
)
from .ledger import LEDGER_NAME, check, load, save, suggest_pin, suggest_threshold, unjudged
from .toll import get_tokenizer
from .transcripts import default_roots_from_env
from .transcripts import load as load_sessions

EXIT_OK, EXIT_VIOLATION, EXIT_DEAD = 0, 1, 2


def _fmt(n: int) -> str:
    return f"{n:,}"


def _collect(args) -> tuple:
    roots = [Path(r) for r in args.root] if args.root else default_roots_from_env()
    sessions = load_sessions(roots, limit=args.limit, since_days=args.since_days)
    tok = get_tokenizer(prefer_estimate=args.estimate)
    report = analyze(sessions, tok)
    return sessions, report, roots


def _dead(msg: str) -> int:
    print(f"awtoll: COULD NOT JUDGE -- {msg}", file=sys.stderr)
    print("  (exit 2, never 0: silence is not a pass)", file=sys.stderr)
    return EXIT_DEAD


def _guard(report: Report, roots) -> Optional[int]:
    if report.sessions == 0:
        return _dead(
            f"no transcripts found under {', '.join(str(r) for r in roots)}. "
            "Point --root at your agent's transcript directory, or set AWTOLL_TRANSCRIPTS."
        )
    if report.calls == 0:
        return _dead(f"{report.sessions} transcript(s) read, but they contain no tool calls")
    return None


def _header(report: Report) -> None:
    print(f"tokenizer: {report.tokenizer}")
    print(
        f"{report.sessions} session(s), {_fmt(report.calls)} tool calls "
        f"({_fmt(report.paired)} with a result)"
    )
    if report.unreadable_lines:
        print(f"  {_fmt(report.unreadable_lines)} unreadable transcript line(s) -- skipped")
    if report.undecidable_shapes:
        print(
            f"  {_fmt(report.undecidable_shapes)} call(s) had no decidable shape "
            "-- counted, not dropped"
        )


def _footer(report: Report) -> None:
    print()
    print(f"tool results admitted {_fmt(report.total_ok_tokens)} tokens into context")
    if report.runtime_prompt_tokens:
        print(
            f"  the runtime recorded {_fmt(report.runtime_prompt_tokens)} cumulative prompt "
            f"tokens across these sessions ({report.resend_factor:.0f}x)."
        )
        print(
            "    That is NOT a denominator -- every turn re-sends the whole context, so a"
        )
        print(
            "    token a tool admits early is paid for again on every turn after it. The"
        )
        print(
            "    multiple is why an expensive shape early in a session costs far more than"
        )
        print("    its own toll.")
    if report.estimated:
        print()
        print("  NOTE: counts are ESTIMATED. Install tiktoken for exact numbers.")


def cmd_scan(args) -> int:
    _, report, roots = _collect(args)
    dead = _guard(report, roots)
    if dead is not None:
        return dead
    if args.json:
        print(json.dumps(_as_dict(report), indent=2))
        return EXIT_OK

    _header(report)
    print()
    print(f"{'shape':<44} {'calls':>6} {'ok':>5} {'total':>10} {'median':>8} {'p90':>8}")
    print("-" * 86)
    for st in report.shapes[: args.top]:
        flag = "  <- mostly NOT ok" if st.suspicious else ""
        print(
            f"{st.shape[:44]:<44} {st.calls:>6} {st.ok_calls:>5} "
            f"{_fmt(st.ok_tokens):>10} {_fmt(st.median):>8} {_fmt(st.p90):>8}{flag}"
        )

    bad = [s for s in report.shapes if s.suspicious]
    if bad:
        print()
        print("SUSPICIOUS -- cheap because they are not answering, not because they are good:")
        for st in bad[:10]:
            outcomes = ", ".join(f"{k}={v}" for k, v in sorted(st.by_outcome.items()))
            print(f"  {st.shape}  ({outcomes})")
        print(
            "  A tool that returns nothing has the lowest toll there is. Ranking on cost"
        )
        print("  alone would crown whichever tool is broken; these are held out instead.")
    _footer(report)
    return EXIT_OK


def cmd_repeats(args) -> int:
    _, report, roots = _collect(args)
    dead = _guard(report, roots)
    if dead is not None:
        return dead
    if args.json:
        print(json.dumps(_as_dict(report)["repeats"], indent=2))
        return EXIT_OK

    _header(report)
    print()
    if not report.repeats:
        print("No repeated results. Nothing was fetched twice in one session.")
        _footer(report)
        return EXIT_OK

    print("Toll paid more than once -- the same result, fetched again in one session:")
    print()
    print(f"{'wasted':>9}  {'x':>3}  {'each':>8}  shape / target")
    print("-" * 86)
    for r in report.repeats[: args.top]:
        target = r.target if len(r.target) <= 46 else r.target[:43] + "..."
        print(
            f"{_fmt(r.wasted):>9}  {r.times:>3}  {_fmt(r.tokens_each):>8}  {r.shape} :: {target}"
        )
    print()
    print(
        f"wasted: {_fmt(report.wasted_tokens)} tokens = {report.waste_ratio:.1%} of total toll"
    )
    print(
        "  This is the strongest savings signal here: you paid for identical bytes twice."
    )
    _footer(report)
    return EXIT_OK


def cmd_versus(args) -> int:
    """Run two commands and compare what each would cost to read.

    The only honest counterfactual: not a model of what the other tool WOULD
    have returned, but the bytes it actually returns, right now.
    """
    tok = get_tokenizer(prefer_estimate=args.estimate)
    results = []
    for label, cmd in (("A", args.a), ("B", args.b)):
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                # Without an explicit codec the child's output is decoded with
                # the LOCALE one (cp1252 on Windows), and a UnicodeDecodeError
                # is a ValueError -- which the OSError/SubprocessError guard
                # below does NOT catch, so the tool crashes rather than
                # reporting that it could not judge.
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"awtoll: {label} timed out after {args.timeout}s", file=sys.stderr)
            return _dead("a command did not finish, so its toll is unknown")
        out = (proc.stdout or "") + (proc.stderr or "")
        results.append((label, cmd, tok.count(out), len(out), proc.returncode))

    print(f"tokenizer: {tok.describe()}")
    print()
    for label, cmd, toks, chars, rc in results:
        note = "" if rc == 0 else f"  [exit {rc}]"
        print(f"{label}: {_fmt(toks):>9} tokens  ({_fmt(chars)} chars){note}")
        print(f"   {cmd}")
    a, b = results[0][2], results[1][2]
    print()
    if min(a, b) == 0:
        print("One side returned NOTHING. That is not a win -- it is a tool that did not")
        print("answer. Compare only commands that both actually produced the answer.")
        return EXIT_VIOLATION

    # A FAILED command is cheap for the same reason an empty one is, and this
    # was live: a demo run with the wrong working directory exited 2 on BOTH
    # sides and still printed a confident "A is 37% cheaper". That is the rule
    # this whole tool is built on -- a tool that answers nothing is the cheapest
    # tool there is -- violated in the one command that had not applied it.
    failed = [(label, rc) for label, _cmd, _t, _c, rc in results if rc != 0]
    if failed and not args.allow_failure:
        for label, rc in failed:
            print(f"{label} exited {rc} -- it did not answer, so its toll is not a price.")
        print("Refusing to name a winner. Fix the command, or pass --allow-failure if a")
        print("non-zero exit is the expected shape of this comparison (a strict scorer")
        print("refusing a candidate, say) and you have read both outputs yourself.")
        return EXIT_VIOLATION
    if a == b:
        print("Identical toll.")
    else:
        cheaper, dearer = ("B", "A") if b < a else ("A", "B")
        print(
            f"{cheaper} is {abs(a - b):,} tokens cheaper "
            f"({(1 - min(a, b) / max(a, b)):.0%} less than {dearer})."
        )
    print()
    print("Read this as cost only. It says nothing about whether either answer was")
    print("CORRECT -- and a cheaper wrong answer costs you the re-run plus the detour.")
    return EXIT_OK


def cmd_check(args) -> int:
    _, report, roots = _collect(args)
    dead = _guard(report, roots)
    if dead is not None:
        return dead

    path = Path(args.ledger) if args.ledger else Path.cwd() / LEDGER_NAME
    try:
        ledger = load(path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _dead(f"ledger {path} could not be read: {exc}")

    if args.init:
        ledger.data["waste_ratio_pin"] = suggest_pin(report)
        ledger.data["decide_above_tokens"] = suggest_threshold(report, ledger.budget)
        save(ledger)
        n, toks = unjudged(ledger, report)
        print(f"wrote {path}")
        print(f"  waste_ratio_pin      = {ledger.data['waste_ratio_pin']}")
        print(
            f"  decide_above_tokens  = {_fmt(ledger.data['decide_above_tokens'])}"
            f"  ({ledger.budget} shape(s) to decide)"
        )
        print(
            f"  NOT judged: {n} shape(s) totalling {_fmt(toks)} tokens -- lower the"
        )
        print("  threshold to widen coverage as decisions land.")
        return EXIT_OK

    if not ledger.existed:
        return _dead(
            f"no ledger at {path}. Run `awtoll check --init` to write one pinned to "
            "today's measurement, then decide the shapes it lists."
        )

    findings = check(ledger, report)
    _header(report)
    print()
    print(f"ledger: {path}")
    print(f"decisions: {len(ledger.decisions)}  threshold: {_fmt(ledger.decide_above)} tokens")
    n_un, tok_un = unjudged(ledger, report)
    print(
        f"NOT judged: {n_un} shape(s) below the threshold, {_fmt(tok_un)} tokens "
        "-- bounded coverage, stated"
    )
    pin = ledger.waste_pin
    if pin is not None:
        print(f"repeat waste: {report.waste_ratio:.1%} against pin {pin:.1%}")
    print()
    # Append a history record before returning.
    hist_path = (
        Path(args.history) if hasattr(args, "history") and args.history
        else default_history_path(path)
    )
    try:
        rec = record_from_check(report, ledger, hist_path)
        append_record(rec, hist_path)
    except Exception as exc:
        print(f"warning: could not append history: {exc}", file=sys.stderr)

    if not findings:
        print("AWTOLL: OK")
        return EXIT_OK
    print("AWTOLL: VIOLATION")
    for f in findings:
        print(f"  x {f.rule} {f.detail}")
    return EXIT_VIOLATION


def cmd_trend(args) -> int:
    """Show movement in toll metrics across runs.

    Only ratios move independently of session mix. Absolute tokens drift
    with load, so a trend is valid only when session counts are stable
    or explicitly normalized by session count.
    """
    hist_path = (
        Path(args.history) if hasattr(args, "history") and args.history
        else Path.cwd() / "awtoll-history.jsonl"
    )

    records, unreadable = load_history(hist_path)
    if unreadable:
        print(f"  ({_fmt(unreadable)} unreadable line(s) in history -- skipped)")

    if not records:
        return _dead(
            f"no history found at {hist_path}. Run `awtoll check` to start recording."
        )

    stats = validate_comparability(records)
    if not stats.comparable:
        print(f"awtoll trend: {stats.comparable_reason}", file=sys.stderr)
        return EXIT_VIOLATION

    print(f"history: {hist_path}")
    print(f"  {len(records)} run(s)")
    print()

    if args.json:
        import json as json_module
        print(json_module.dumps(
            {
                "records": [
                    {
                        "timestamp": r.timestamp,
                        "waste_ratio": r.waste_ratio,
                        "wasted_tokens": r.wasted_tokens,
                        "total_ok_tokens": r.total_ok_tokens,
                        "sessions": r.sessions,
                        "calls": r.calls,
                        "ledger_pass": r.ledger_pass,
                        "ledger_waste_violation": r.ledger_waste_violation,
                    }
                    for r in records
                ],
            },
            indent=2,
        ))
        return EXIT_OK

    # Readable trend: show waste_ratio and pass/fail history.
    print(f"{'time':<25} {'waste':>8} {'tokens':>12} {'pass?':>6}")
    print("-" * 55)
    for rec in records:
        status = "OK" if rec.ledger_pass else "FAIL"
        print(
            f"{rec.timestamp[:19]:<25} {rec.waste_ratio:>7.1%} "
            f"{_fmt(rec.wasted_tokens):>12} {status:>6}"
        )

    if records:
        first = records[0]
        last = records[-1]
        delta = last.waste_ratio - first.waste_ratio
        direction = "↓" if delta < -0.001 else "↑" if delta > 0.001 else "→"
        print()
        print(
            f"{direction} waste ratio: {first.waste_ratio:.1%} (first) → "
            f"{last.waste_ratio:.1%} (last) [{delta:+.1%}]"
        )

    return EXIT_OK


def _as_dict(report: Report) -> dict:
    return {
        "tokenizer": report.tokenizer,
        "estimated": report.estimated,
        "sessions": report.sessions,
        "calls": report.calls,
        "paired": report.paired,
        "total_ok_tokens": report.total_ok_tokens,
        "runtime_prompt_tokens": report.runtime_prompt_tokens,
        "resend_factor": round(report.resend_factor, 2),
        "wasted_tokens": report.wasted_tokens,
        "waste_ratio": round(report.waste_ratio, 4),
        "unreadable_lines": report.unreadable_lines,
        "undecidable_shapes": report.undecidable_shapes,
        "shapes": [
            {
                "shape": s.shape,
                "calls": s.calls,
                "ok_calls": s.ok_calls,
                "ok_tokens": s.ok_tokens,
                "median": s.median,
                "p90": s.p90,
                "ok_ratio": round(s.ok_ratio, 4),
                "judged": s.judged,
                "opaque": s.opaque,
                "suspicious": s.suspicious,
                "by_outcome": s.by_outcome,
                "sessions": len(s.sessions),
            }
            for s in report.shapes
        ],
        "repeats": [
            {
                "shape": r.shape,
                "target": r.target,
                "times": r.times,
                "tokens_each": r.tokens_each,
                "wasted": r.wasted,
                "session": r.session_id,
            }
            for r in report.repeats
        ],
    }


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", action="append", help="transcript file or directory (repeatable)")
    p.add_argument("--limit", type=int, default=None, help="only the N newest transcripts")
    p.add_argument(
        "--since-days", type=float, default=None, help="only transcripts newer than N days"
    )
    p.add_argument("--estimate", action="store_true", help="force the zero-dep estimator")
    p.add_argument("--top", type=int, default=25, help="rows to print")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def main(argv: Optional[List[str]] = None) -> int:
    # GENERATED doctor intercept (gen_aw_doctor.py) -- do not edit
    _dv = locals().get("argv")
    if (_dv if _dv is not None else __import__("sys").argv[1:])[:1] == ["doctor"]:
        from ._doctor import report
        return report()
    # Handle --self-test before argparse, as it can be a global flag
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in ("--self-test", "self-test"):
        from .selftest import run_self_test
        return run_self_test()

    ap = argparse.ArgumentParser(
        prog="awtoll",
        description="What every tool call costs you in context, measured from your transcripts.",
    )
    ap.add_argument("--version", action="version", version=f"awtoll {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("scan", help="toll table: what each tool shape cost")
    _add_common(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("repeats", help="toll you paid more than once")
    _add_common(p)
    p.set_defaults(func=cmd_repeats)

    p = sub.add_parser("versus", help="run two commands, compare what reading each costs")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--estimate", action="store_true")
    p.add_argument(
        "--allow-failure",
        action="store_true",
        help="compare even when a command exits non-zero (you have read both outputs)",
    )
    p.set_defaults(func=cmd_versus)

    p = sub.add_parser("check", help="gate: decisions recorded, waste ratcheting down")
    _add_common(p)
    p.add_argument("--ledger", help=f"path to {LEDGER_NAME} (default: ./{LEDGER_NAME})")
    p.add_argument(
        "--history",
        help="path to history file (default: next to ledger)",
    )
    p.add_argument("--init", action="store_true", help="write a ledger pinned to today")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("trend", help="show movement in waste_ratio across runs")
    p.add_argument(
        "--history",
        help="path to history file (default: ./awtoll-history.jsonl)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_trend)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return EXIT_DEAD
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

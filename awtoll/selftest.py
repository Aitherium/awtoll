"""Prove every rule can still fail -- and prove none of them cries wolf.

Both halves are required. A rule that always fires is as useless as one that
never does: the first floods and gets switched off, the second is decoration.
Each arm below therefore asserts a POSITIVE (the rule fires on the broken
shape) and, where the rule could over-fire, a NEGATIVE (it stays quiet on the
healthy one).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import List

from .analyze import analyze
from .ledger import check, load
from .shapes import shape_of, shape_of_bash, target_of
from .toll import MEASURED_CHARS_PER_TOKEN, get_tokenizer
from .transcripts import parse_session

_FAILURES: List[str] = []


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        _FAILURES.append(name)


def _tu(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _tr(tid, content, is_error=False):
    return {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}


def _write(path: Path, records) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def _assistant(blocks, usage=None):
    msg = {"content": blocks}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "message": msg}


def _user(blocks):
    return {"type": "user", "message": {"content": blocks}}


def run_self_test() -> int:
    print("awtoll self-test")
    tok = get_tokenizer(prefer_estimate=True)

    # --- shapes -----------------------------------------------------------
    print("\nshape signature")
    # Hoisted, not inlined into the f-string. A backslash inside an f-string
    # expression is PEP 701 and needs Python 3.12; this package declares
    # requires-python >=3.10, so the inline form was a SyntaxError at IMPORT
    # on the versions it claims to support -- and `ast.parse(feature_version=
    # (3,10))` reports it CLEAN, because feature_version does not enforce the
    # pre-3.12 f-string tokenizer rules. Only ruff --target-version py310 sees
    # it. Same class as an unguarded new-stdlib import: fine on every dev box,
    # dead on the one that gates.
    _cd_shape = shape_of_bash('cd /repo && awgraph query "who calls X"')
    _ok(
        "cd + && does not sign as `cd`",
        _cd_shape == "awgraph query",
        f"got {_cd_shape!r}",
    )
    _ok(
        "wrapper stripped (timeout 300 python -m x)",
        shape_of_bash("timeout 300 python -m pytest") == "python",
        f"got {shape_of_bash('timeout 300 python -m pytest')!r}",
    )
    _ok(
        "two queries of one verb share a shape",
        shape_of_bash('awgraph query "a"') == shape_of_bash('awgraph query "b"'),
    )
    _ok(
        "two verbs of one binary do NOT share a shape (does not over-collapse)",
        shape_of_bash("awgraph query x") != shape_of_bash("awgraph callers x"),
    )
    _ok(
        "inline code is ONE bucket, not dropped",
        shape_of_bash("python - <<'PY'\nprint(1)\nPY") == "python -c (inline)",
        f"got {shape_of_bash(chr(10).join(['python - <<PY', 'print(1)', 'PY']))!r}",
    )
    _ok(
        "a path argument does not become part of the shape",
        shape_of_bash("git log /some/path.py") == "git log",
        f"got {shape_of_bash('git log /some/path.py')!r}",
    )
    _ok(
        "non-Bash tool signs as its own name",
        shape_of("Read", {"file_path": "/x/y.py"}) == "Read",
    )
    _ok(
        "target normalises a path for repeat detection",
        target_of("Read", {"file_path": "C:\\A\\B.py"}) == "c:/a/b.py",
        f"got {target_of('Read', {'file_path': 'C:/A/B.py'})!r}",
    )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # --- pairing ------------------------------------------------------
        print("\npairing and outcomes")
        # Two calls issued together, results arriving in REVERSED order. Pairing
        # by adjacency would swap their tolls and look perfectly plausible.
        big, small = "X" * 4000, "y" * 40
        _write(
            root / "pair.jsonl",
            [
                _assistant([_tu("t1", "Bash", {"command": "awgraph query a"}),
                            _tu("t2", "Bash", {"command": "grep -r foo ."})]),
                _user([_tr("t2", small), _tr("t1", big)]),
            ],
        )
        sess = parse_session(root / "pair.jsonl")
        by_id = {c.inp["command"]: c for c in sess.calls}
        _ok(
            "results pair by id, not adjacency",
            by_id["awgraph query a"].result_chars == len(big)
            and by_id["grep -r foo ."].result_chars == len(small),
            "the reversed-order results were swapped",
        )

        _write(
            root / "unpaired.jsonl",
            [_assistant([_tu("u1", "Bash", {"command": "podman ps"})])],
        )
        s2 = parse_session(root / "unpaired.jsonl")
        _ok("a call with no result is kept as `unpaired`", s2.calls[0].outcome == "unpaired")
        _ok("...and is NOT counted as a zero-cost ok call", s2.calls[0].paired is False)

        _write(
            root / "outcomes.jsonl",
            [
                _assistant([_tu("e1", "Bash", {"command": "awfind x"})]),
                _user([_tr("e1", "boom", is_error=True)]),
                _assistant([_tu("e2", "Bash", {"command": "awfind x"})]),
                _user([_tr("e2", "   ")]),
                _assistant([_tu("e3", "Bash", {"command": "awfind x"})]),
                _user([_tr("e3", "data " * 500 + "\n[truncated]")]),
                _assistant([_tu("e4", "Bash", {"command": "awfind x"})]),
                _user([_tr("e4", "real answer " * 100)]),
            ],
        )
        s3 = parse_session(root / "outcomes.jsonl")
        got = sorted(c.outcome for c in s3.calls)
        _ok(
            "error / empty / truncated / ok are distinguished",
            got == ["empty", "error", "ok", "truncated"],
            f"got {got}",
        )
        rep = analyze([s3], tok)
        st = rep.shapes[0]
        _ok("only ok calls contribute toll", st.ok_calls == 1 and st.calls == 4)
        _ok(
            "a mostly-not-ok shape is FLAGGED, not crowned cheap",
            st.suspicious is True,
        )

        # ...and the negative: a healthy shape must not be flagged.
        _write(
            root / "healthy.jsonl",
            [
                rec
                for i in range(4)
                for rec in (
                    _assistant([_tu(f"h{i}", "Bash", {"command": "awgraph query q"})]),
                    _user([_tr(f"h{i}", "answer " * 200)]),
                )
            ],
        )
        s4 = parse_session(root / "healthy.jsonl")
        rep4 = analyze([s4], tok)
        _ok(
            "a healthy shape is NOT flagged (does not cry wolf)",
            rep4.shapes[0].suspicious is False,
        )

        # --- repeats ------------------------------------------------------
        print("\nrepeat detection")
        body = "line of file " * 400
        _write(
            root / "repeat.jsonl",
            [
                _assistant([_tu("r1", "Read", {"file_path": "/a/b.py"})]),
                _user([_tr("r1", body)]),
                _assistant([_tu("r2", "Read", {"file_path": "/a/b.py"})]),
                _user([_tr("r2", body)]),
                _assistant([_tu("r3", "Read", {"file_path": "/a/OTHER.py"})]),
                _user([_tr("r3", body)]),
            ],
        )
        s5 = parse_session(root / "repeat.jsonl")
        rep5 = analyze([s5], tok)
        _ok("the same target read twice is one repeat", len(rep5.repeats) == 1)
        _ok(
            "wasted counts the EXTRA fetch only, not both",
            rep5.repeats and rep5.repeats[0].times == 2
            and rep5.repeats[0].wasted == rep5.repeats[0].tokens_each,
        )
        _ok(
            "a different target is not a repeat (does not cry wolf)",
            all(r.target == "/a/b.py" for r in rep5.repeats),
        )
        # Repeats are session-scoped: the same file read ONCE in each of two
        # sessions is two questions days apart, not waste. Scoping this
        # globally would invent a repeat out of every file anyone revisits --
        # the flood that gets a rule switched off.
        for name in ("solo_a.jsonl", "solo_b.jsonl"):
            _write(
                root / name,
                [
                    _assistant([_tu("s1", "Read", {"file_path": "/a/b.py"})]),
                    _user([_tr("s1", body)]),
                ],
            )
        rep6 = analyze(
            [parse_session(root / "solo_a.jsonl"), parse_session(root / "solo_b.jsonl")], tok
        )
        _ok(
            "one read in each of two sessions is NOT a repeat",
            len(rep6.repeats) == 0,
            f"got {len(rep6.repeats)}",
        )

        # --- ledger -------------------------------------------------------
        print("\nledger rules")
        lp = root / "awtoll.json"

        def _check(data):
            lp.write_text(json.dumps(data), encoding="utf-8")
            return {f.rule for f in check(load(lp), rep5)}

        cheap = {"decide_above_tokens": 10**9, "decisions": {}}
        _ok("a clean ledger passes", _check(cheap) == set())
        _ok(
            "TL001 fires on an unknown status",
            "TL001" in _check({**cheap, "decisions": {"Read": {"status": "maybe"}}}),
        )
        _ok(
            "TL002 fires on a reasonless keep",
            "TL002" in _check({**cheap, "decisions": {"Read": {"status": "keep"}}}),
        )
        _ok(
            "TL002 clears once a reason is given",
            "TL002"
            not in _check({**cheap, "decisions": {"Read": {"status": "keep", "reason": "needed"}}}),
        )
        _ok(
            "TL003 fires on an expensive undecided shape",
            "TL003" in _check({"decide_above_tokens": 1, "decisions": {}}),
        )
        _ok(
            "TL003 clears once the shape is decided",
            "TL003"
            not in _check(
                {
                    "decide_above_tokens": 1,
                    "decisions": {
                        s.shape: {"status": "keep", "reason": "x"} for s in rep5.shapes
                    },
                }
            ),
        )
        _ok(
            "TL005 fires when waste exceeds the pin",
            "TL005" in _check({**cheap, "waste_ratio_pin": 0.0}),
        )
        _ok(
            "TL005 stays quiet at or under the pin",
            "TL005" not in _check({**cheap, "waste_ratio_pin": 1.0}),
        )
        broken = root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        try:
            load(broken)
            _ok("a malformed ledger RAISES rather than defaulting to empty", False)
        except Exception:
            _ok("a malformed ledger RAISES rather than defaulting to empty", True)

        # --- could-not-judge ------------------------------------------------
        print("\nrefusal to pass on silence")
        empty_dir = root / "nothing"
        empty_dir.mkdir()
        from .cli import main as cli_main

        rc = cli_main(["scan", "--root", str(empty_dir)])
        _ok("no transcripts exits 2, never 0", rc == 2, f"got {rc}")
        _write(root / "notools.jsonl", [_assistant([{"type": "text", "text": "hi"}])])
        rc = cli_main(["scan", "--root", str(root / "notools.jsonl")])
        _ok("transcripts with no tool calls exit 2", rc == 2, f"got {rc}")

    # --- versus refuses to price a failure ---------------------------------
    print()
    print("versus")
    from .cli import main as cli_main

    # The command must FAIL and still PRINT: a silent failure is caught one
    # branch earlier by the returned-nothing rule, so `exit 3` alone would
    # exercise the wrong guard and pass for the wrong reason.
    # PORTABLE on purpose: shell=True runs cmd.exe on Windows and sh elsewhere,
    # so `echo x; exit 3` is a bash-ism that cmd.exe echoes verbatim and exits 0
    # -- the arm then passes for the wrong reason on one OS and fails on the
    # other. Measured: it did exactly that here.
    failing = (
        '"' + sys.executable + '" -c "'
        + "import sys; print('partial output'); sys.exit(3)" + '"'
    )
    rc = cli_main(["versus", failing, "echo hello"])
    _ok("a non-zero exit is refused, not priced", rc == 1, f"got {rc}")
    rc = cli_main(["versus", failing, "echo hello", "--allow-failure"])
    _ok("--allow-failure permits it deliberately", rc == 0, f"got {rc}")
    rc = cli_main(["versus", "echo one", "echo two"])
    _ok("two healthy commands still compare (does not cry wolf)", rc == 0, f"got {rc}")

    # --- history -----------------------------------------------------------
    print("\nhistory")
    from .history import (
        HISTORY_VERSION,
        HistoryRecord,
        append_record,
        load_history,
        validate_comparability,
    )

    # Arm: a record is appended and can be read back.
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_file = Path(tmpdir) / "awtoll-history.jsonl"
        rec = HistoryRecord(
            timestamp="2026-08-23T00:00:00Z",
            version=HISTORY_VERSION,
            sessions=5,
            calls=100,
            waste_ratio=0.05,
            wasted_tokens=500,
            total_ok_tokens=10000,
            shapes_count=10,
            repeats_count=5,
            ledger_pass=True,
            ledger_findings_count=None,
            ledger_waste_pin=0.1,
            ledger_waste_violation=False,
        )
        append_record(rec, hist_file)
        records, unreadable = load_history(hist_file)
        _ok(
            "record appended and loaded",
            len(records) == 1 and records[0].waste_ratio == 0.05,
            f"got {len(records)} records",
        )
        _ok(
            "no unreadable lines on a valid JSONL",
            unreadable == 0,
            f"got {unreadable} unreadable",
        )

    # Arm: corrupt/partial history line does not crash but is counted/reported.
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_file = Path(tmpdir) / "awtoll-history.jsonl"
        hist_file.write_text(
            '{"version": 1, "waste_ratio": 0.05}\n'
            "{not json}\n"
            '{"version": 1, "waste_ratio": 0.04}\n',
            encoding="utf-8",
        )
        records, unreadable = load_history(hist_file)
        _ok(
            "corrupt line is skipped, not fatal",
            len(records) == 2 and unreadable == 1,
            f"got {len(records)} records, {unreadable} unreadable",
        )

    # Arm: validate_comparability refuses to compare incomparable runs.
    recs = [
        HistoryRecord(
            timestamp="2026-08-23T00:00:00Z",
            version=HISTORY_VERSION,
            sessions=1,
            calls=100,
            waste_ratio=0.05,
            wasted_tokens=500,
            total_ok_tokens=10000,
            shapes_count=10,
            repeats_count=5,
            ledger_pass=True,
            ledger_findings_count=None,
            ledger_waste_pin=0.1,
            ledger_waste_violation=False,
        ),
        HistoryRecord(
            timestamp="2026-08-24T00:00:00Z",
            version=HISTORY_VERSION,
            sessions=10,
            calls=200,
            waste_ratio=0.04,
            wasted_tokens=400,
            total_ok_tokens=10000,
            shapes_count=12,
            repeats_count=4,
            ledger_pass=True,
            ledger_findings_count=None,
            ledger_waste_pin=0.1,
            ledger_waste_violation=False,
        ),
    ]
    stats = validate_comparability(recs)
    _ok(
        "incomparable runs (session count varies >2x) are refused",
        not stats.comparable,
        f"comparable: {stats.comparable}, reason: {stats.comparable_reason}",
    )

    # Arm: comparable runs are accepted.
    recs2 = [
        HistoryRecord(
            timestamp="2026-08-23T00:00:00Z",
            version=HISTORY_VERSION,
            sessions=5,
            calls=100,
            waste_ratio=0.05,
            wasted_tokens=500,
            total_ok_tokens=10000,
            shapes_count=10,
            repeats_count=5,
            ledger_pass=True,
            ledger_findings_count=None,
            ledger_waste_pin=0.1,
            ledger_waste_violation=False,
        ),
        HistoryRecord(
            timestamp="2026-08-24T00:00:00Z",
            version=HISTORY_VERSION,
            sessions=6,
            calls=120,
            waste_ratio=0.04,
            wasted_tokens=400,
            total_ok_tokens=10000,
            shapes_count=11,
            repeats_count=4,
            ledger_pass=True,
            ledger_findings_count=None,
            ledger_waste_pin=0.1,
            ledger_waste_violation=False,
        ),
    ]
    stats2 = validate_comparability(recs2)
    _ok(
        "comparable runs (session count <2x variation) are accepted",
        stats2.comparable,
        f"reason: {stats2.comparable_reason}",
    )

    # --- tokenizer ---------------------------------------------------------
    print("\ntokenizer")
    est = get_tokenizer(prefer_estimate=True)
    _ok("the fallback is labelled ESTIMATED", est.estimated and "ESTIMATED" in est.describe())
    n = est.count("x" * 3459)
    _ok(
        f"the estimator uses the measured {MEASURED_CHARS_PER_TOKEN} chars/token",
        990 <= n <= 1010,
        f"got {n}",
    )
    exact = get_tokenizer()
    if not exact.estimated:
        sample = "def f(x):\n    return x + 1\n" * 20
        a, b = exact.count(sample), est.count(sample)
        _ok(
            "estimator is within 35% of the exact tokenizer on code",
            abs(a - b) / max(a, 1) < 0.35,
            f"exact={a} est={b}",
        )
    else:
        print("  note tiktoken not installed -- exact-vs-estimate arm not run")

    print()
    if _FAILURES:
        print(f"SELF-TEST FAILED: {len(_FAILURES)} arm(s): {', '.join(_FAILURES)}")
        return 1
    print("SELF-TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(run_self_test())

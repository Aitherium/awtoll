"""Behavioural pins for awtoll.

The `--self-test` is the shipped proof that every rule can still fail; this is
the CI half, and it deliberately pins the two properties that a refactor is
most likely to quietly break: pairing by id, and the refusal to treat a
non-answer as a cheap answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from awtoll.analyze import analyze
from awtoll.ledger import check, load
from awtoll.selftest import run_self_test
from awtoll.shapes import shape_of, shape_of_bash, target_of
from awtoll.toll import get_tokenizer
from awtoll.transcripts import parse_session


def _tu(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _tr(tid, content, is_error=False):
    return {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}


def _write(path: Path, records):
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def _a(blocks):
    return {"type": "assistant", "message": {"content": blocks}}


def _u(blocks):
    return {"type": "user", "message": {"content": blocks}}


@pytest.fixture
def tok():
    return get_tokenizer(prefer_estimate=True)


def test_self_test_passes():
    assert run_self_test() == 0


def test_results_pair_by_id_not_adjacency(tmp_path):
    big, small = "X" * 4000, "y" * 40
    p = _write(
        tmp_path / "s.jsonl",
        [
            _a([_tu("t1", "Bash", {"command": "awgraph query a"}),
                _tu("t2", "Bash", {"command": "grep -r foo ."})]),
            _u([_tr("t2", small), _tr("t1", big)]),  # reversed on purpose
        ],
    )
    calls = {c.inp["command"]: c for c in parse_session(p).calls}
    assert calls["awgraph query a"].result_chars == len(big)
    assert calls["grep -r foo ."].result_chars == len(small)


def test_opaque_result_is_not_a_failure(tmp_path):
    """A textless structured result is a cost we cannot see, not a non-answer.

    Mutation guard: rendering these blocks to "" classifies them as `empty`,
    which flagged 36 healthy tool-loading calls as broken on the first run.
    """
    p = _write(
        tmp_path / "s.jsonl",
        [
            _a([_tu("o1", "ToolSearch", {"query": "x"})]),
            _u([_tr("o1", [{"type": "tool_reference", "tool_name": "WebFetch"}])]),
        ],
    )
    call = parse_session(p).calls[0]
    assert call.outcome == "opaque"
    assert call.nontext_blocks == 1


def test_opaque_calls_do_not_make_a_shape_suspicious(tmp_path, tok):
    records = []
    for i in range(6):
        records += [
            _a([_tu(f"o{i}", "ToolSearch", {"query": "x"})]),
            _u([_tr(f"o{i}", [{"type": "tool_reference", "tool_name": "T"}])]),
        ]
    rep = analyze([parse_session(_write(tmp_path / "s.jsonl", records))], tok)
    assert rep.shapes[0].suspicious is False


def test_a_mostly_failing_shape_is_flagged(tmp_path, tok):
    records = []
    for i in range(4):
        records += [
            _a([_tu(f"e{i}", "Bash", {"command": "awfind x"})]),
            _u([_tr(f"e{i}", "boom", is_error=True)]),
        ]
    records += [_a([_tu("g", "Bash", {"command": "awfind x"})]), _u([_tr("g", "answer " * 100)])]
    rep = analyze([parse_session(_write(tmp_path / "s.jsonl", records))], tok)
    assert rep.shapes[0].suspicious is True
    assert rep.shapes[0].ok_calls == 1


def test_only_ok_calls_contribute_toll(tmp_path, tok):
    p = _write(
        tmp_path / "s.jsonl",
        [
            _a([_tu("a", "Bash", {"command": "awfind x"})]),
            _u([_tr("a", "x" * 5000, is_error=True)]),
            _a([_tu("b", "Bash", {"command": "awfind x"})]),
            _u([_tr("b", "ok " * 10)]),
        ],
    )
    rep = analyze([parse_session(p)], tok)
    # The 5000-char ERROR must not appear in the toll.
    assert rep.total_ok_tokens < 100


def test_unpaired_call_is_not_a_free_call(tmp_path, tok):
    p = _write(tmp_path / "s.jsonl", [_a([_tu("u", "Bash", {"command": "podman ps"})])])
    call = parse_session(p).calls[0]
    assert call.outcome == "unpaired"
    assert not call.paired


@pytest.mark.parametrize(
    "command,expected",
    [
        ("cd /repo && awgraph query x", "awgraph query"),
        ("timeout 300 python -m pytest", "python"),
        ("sudo podman ps", "podman ps"),
        ("FOO=1 git log --oneline", "git log"),
        ("git log /some/path.py", "git log"),
    ],
)
def test_shape_signatures(command, expected):
    assert shape_of_bash(command) == expected


def test_inline_code_is_one_bucket_not_dropped():
    assert shape_of_bash("python - <<'PY'\nprint(1)\nPY") == "python -c (inline)"
    assert shape_of_bash('python -c "print(1)"') == "python -c (inline)"


def test_distinct_verbs_do_not_collapse():
    assert shape_of_bash("awgraph query x") != shape_of_bash("awgraph callers x")


def test_target_normalises_paths():
    assert target_of("Read", {"file_path": "C:\\A\\B.py"}) == "c:/a/b.py"
    assert shape_of("Read", {"file_path": "/x.py"}) == "Read"


def test_repeats_are_session_scoped(tmp_path, tok):
    body = "line " * 400
    for name in ("a.jsonl", "b.jsonl"):
        _write(
            tmp_path / name,
            [_a([_tu("r", "Read", {"file_path": "/a/b.py"})]), _u([_tr("r", body)])],
        )
    rep = analyze(
        [parse_session(tmp_path / "a.jsonl"), parse_session(tmp_path / "b.jsonl")], tok
    )
    assert rep.repeats == []


def test_repeat_counts_only_the_extra_fetch(tmp_path, tok):
    body = "line " * 400
    p = _write(
        tmp_path / "s.jsonl",
        [
            _a([_tu("r1", "Read", {"file_path": "/a/b.py"})]), _u([_tr("r1", body)]),
            _a([_tu("r2", "Read", {"file_path": "/a/b.py"})]), _u([_tr("r2", body)]),
            _a([_tu("r3", "Read", {"file_path": "/a/b.py"})]), _u([_tr("r3", body)]),
        ],
    )
    rep = analyze([parse_session(p)], tok)
    assert len(rep.repeats) == 1
    r = rep.repeats[0]
    assert r.times == 3
    assert r.wasted == 2 * r.tokens_each


def test_malformed_ledger_raises_rather_than_defaulting(tmp_path):
    bad = tmp_path / "awtoll.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception):
        load(bad)


def test_ledger_rules(tmp_path, tok):
    p = _write(
        tmp_path / "s.jsonl",
        [_a([_tu("r", "Read", {"file_path": "/a/b.py"})]), _u([_tr("r", "x" * 4000)])],
    )
    rep = analyze([parse_session(p)], tok)
    lp = tmp_path / "awtoll.json"

    def rules(data):
        lp.write_text(json.dumps(data), encoding="utf-8")
        return {f.rule for f in check(load(lp), rep)}

    assert rules({"decide_above_tokens": 10**9, "decisions": {}}) == set()
    assert "TL001" in rules({"decide_above_tokens": 10**9,
                             "decisions": {"Read": {"status": "nope"}}})
    assert "TL002" in rules({"decide_above_tokens": 10**9,
                             "decisions": {"Read": {"status": "keep"}}})
    assert "TL003" in rules({"decide_above_tokens": 1, "decisions": {}})
    assert "TL005" in rules({"decide_above_tokens": 10**9, "waste_ratio_pin": 0.0,
                             "decisions": {}}) or rep.waste_ratio == 0


def test_cli_exits_2_when_nothing_to_judge(tmp_path):
    from awtoll.cli import main

    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["scan", "--root", str(empty)]) == 2


def test_estimator_is_labelled_and_uses_the_measured_constant():
    est = get_tokenizer(prefer_estimate=True)
    assert est.estimated
    assert "ESTIMATED" in est.describe()
    assert 990 <= est.count("x" * 3459) <= 1010


def test_history_records_append_and_load(tmp_path):
    from awtoll.history import (
        HISTORY_VERSION,
        HistoryRecord,
        append_record,
        load_history,
    )

    hist_file = tmp_path / "awtoll-history.jsonl"
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
    assert len(records) == 1
    assert records[0].waste_ratio == 0.05
    assert unreadable == 0


def test_history_skips_corrupt_lines(tmp_path):
    from awtoll.history import load_history

    hist_file = tmp_path / "awtoll-history.jsonl"
    good_rec = (
        '{"version": 1, "waste_ratio": 0.05, "sessions": 5, '
        '"calls": 100, "wasted_tokens": 500, "total_ok_tokens": 10000, '
        '"shapes_count": 10, "repeats_count": 5, "ledger_pass": true, '
        '"ledger_findings_count": null, "ledger_waste_pin": 0.1, '
        '"ledger_waste_violation": false}'
    )
    good_rec2 = (
        '{"version": 1, "waste_ratio": 0.04, "sessions": 5, '
        '"calls": 100, "wasted_tokens": 400, "total_ok_tokens": 10000, '
        '"shapes_count": 10, "repeats_count": 5, "ledger_pass": true, '
        '"ledger_findings_count": null, "ledger_waste_pin": 0.1, '
        '"ledger_waste_violation": false}'
    )
    hist_file.write_text(
        good_rec + "\n" + "{not json}\n" + good_rec2 + "\n",
        encoding="utf-8",
    )
    records, unreadable = load_history(hist_file)
    assert len(records) == 2
    assert unreadable == 1


def test_validate_comparability_refuses_different_versions(tmp_path):
    from awtoll.history import HistoryRecord, validate_comparability

    recs = [
        HistoryRecord(
            timestamp="2026-08-23T00:00:00Z",
            version=1,
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
            version=2,  # Different version
            sessions=5,
            calls=100,
            waste_ratio=0.04,
            wasted_tokens=400,
            total_ok_tokens=10000,
            shapes_count=10,
            repeats_count=5,
            ledger_pass=True,
            ledger_findings_count=None,
            ledger_waste_pin=0.1,
            ledger_waste_violation=False,
        ),
    ]
    stats = validate_comparability(recs)
    assert not stats.comparable


def test_validate_comparability_refuses_too_much_session_variation(tmp_path):
    from awtoll.history import HISTORY_VERSION, HistoryRecord, validate_comparability

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
            sessions=10,  # >2x variation
            calls=200,
            waste_ratio=0.04,
            wasted_tokens=400,
            total_ok_tokens=10000,
            shapes_count=10,
            repeats_count=5,
            ledger_pass=True,
            ledger_findings_count=None,
            ledger_waste_pin=0.1,
            ledger_waste_violation=False,
        ),
    ]
    stats = validate_comparability(recs)
    assert not stats.comparable


def test_trend_command_on_history(tmp_path):
    from awtoll.cli import main
    from awtoll.history import HISTORY_VERSION, HistoryRecord, append_record

    hist_file = tmp_path / "awtoll-history.jsonl"
    for i, waste in enumerate([0.05, 0.04, 0.03]):
        rec = HistoryRecord(
            timestamp=f"2026-08-{23+i:02d}T00:00:00Z",
            version=HISTORY_VERSION,
            sessions=5,
            calls=100,
            waste_ratio=waste,
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

    result = main(["trend", "--history", str(hist_file)])
    assert result == 0

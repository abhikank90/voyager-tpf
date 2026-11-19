"""Tests for scripts/reconcile_billing.py — synthetic Console export fixtures."""
import json
from pathlib import Path

import pytest

from scripts import reconcile_billing as rb


def _export(path: Path, rows=None):
    if rows is None:
        rows = [
            ("2026-11-18", "claude-sonnet-4-6", "1,000,000", "2,000,000", "45.00"),
            ("2026-11-18", "claude-haiku-4-5-20251001", "500,000", "300,000", "1.80"),
        ]
    import csv

    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Model", "Input tokens", "Output tokens", "Cost (USD)"])
        w.writerows(rows)
    return path


def _session(model, in_tokens, out_tokens, cost, timestamp="2026-11-18T10:00:00+00:00"):
    return {
        "session_id": f"session-{model}",
        "timestamp": timestamp,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "estimated_cost_usd": cost,
        "per_model_tokens": {model: {"input": in_tokens, "output": out_tokens}},
    }


def _write_sessions(root: Path, sessions):
    (root / "runs" / "synthetic").mkdir(parents=True)
    with open(root / "runs" / "synthetic" / "sessions.jsonl", "w") as fh:
        for s in sessions:
            fh.write(json.dumps(s) + "\n")


def test_exact_match_passes(tmp_path):
    export = _export(tmp_path / "usage.csv")
    sessions = [
        _session("claude-sonnet-4-6", 1_000_000, 2_000_000, 45.00),
        _session("claude-haiku-4-5-20251001", 500_000, 300_000, 1.80),
    ]
    _write_sessions(tmp_path, sessions)

    doc = rb._reconcile(
        rb._load_billed([export], rb._to_date("2026-01-01"), rb._to_date("2030-01-01")),
        rb._load_tracked(tmp_path, rb._to_date("2026-01-01"), rb._to_date("2030-01-01")),
    )
    assert doc["pass"] is True
    assert doc["cost_delta_pct"] == 0.0
    assert doc["models"]["claude-sonnet-4-6"]["input_delta"] == 0


def test_under_tracked_cost_fails(tmp_path):
    export = _export(tmp_path / "usage.csv")
    sessions = [_session("claude-sonnet-4-6", 1_000_000, 2_000_000, 40.00)]
    _write_sessions(tmp_path, sessions)

    doc = rb._reconcile(
        rb._load_billed([export], rb._to_date("2026-01-01"), rb._to_date("2030-01-01")),
        rb._load_tracked(tmp_path, rb._to_date("2026-01-01"), rb._to_date("2030-01-01")),
    )
    assert doc["pass"] is False
    assert doc["cost_delta_pct"] > 1.0


def test_window_filters_out_billed_rows(tmp_path):
    export = _export(tmp_path / "usage.csv", rows=[
        ("2026-11-01", "claude-sonnet-4-6", "100", "200", "1.00"),
        ("2026-11-18", "claude-sonnet-4-6", "1,000,000", "2,000,000", "45.00"),
    ])
    billed = rb._load_billed([export], rb._to_date("2026-11-10"), rb._to_date("2026-11-20"))
    assert billed["totals"]["input_tokens"] == 1_000_000


def test_window_from_model_manifest(tmp_path):
    (tmp_path / "model_manifest.json").write_text(json.dumps({"snapshots": [
        {"label": "start", "taken_at": "2026-11-17T09:00:00Z"},
        {"label": "end", "taken_at": "2026-11-19T09:00:00Z"},
    ]}))
    start, end, source = rb._window(_argparse_args(), tmp_path)
    assert (start.isoformat(), end.isoformat(), source) == ("2026-11-17", "2026-11-19", "model_manifest")


def _argparse_args():
    class Args:
        start = None
        end = None

    return Args()


def test_missing_columns_fail_with_headers(tmp_path):
    (tmp_path / "bad.csv").write_text("When,What,How much\n2026-11-18,claude,45\n")
    with pytest.raises(SystemExit) as exc:
        rb._parse_export(tmp_path / "bad.csv")
    assert "Headers seen" in str(exc.value)


def test_main_writes_json_and_md(tmp_path, monkeypatch):
    export = _export(tmp_path / "usage.csv")
    sessions = [
        _session("claude-sonnet-4-6", 1_000_000, 2_000_000, 45.00),
        _session("claude-haiku-4-5-20251001", 500_000, 300_000, 1.80),
    ]
    _write_sessions(tmp_path, sessions)
    out = tmp_path / "out"
    rc = rb.main(["--exports", str(export), "--root", str(tmp_path),
                  "--start", "2026-01-01", "--end", "2030-01-01", "--out", str(out)])
    assert rc == 0
    doc = json.loads(Path(str(out) + ".json").read_text())
    assert doc["pass"] is True
    assert "pass:" in Path(str(out) + ".md").read_text()

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
    (root / "runs" / "synthetic" / "shard_1").mkdir(parents=True)
    with open(root / "runs" / "synthetic" / "shard_1" / "sessions.jsonl", "w") as fh:
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


# ── Two-file Console format (Task 16b) ───────────────────────────────────────

COST_HEADERS = ("usage_date_utc,model,workspace,api_key,usage_type,context_window,"
                "token_type,cost_usd,list_price_usd,cost_type,inference_geo,speed,"
                "api_key_id,api_key_status")

TOKEN_HEADERS = ("usage_date_utc,model_version,api_key,workspace,usage_type,context_window,"
                 "usage_input_tokens_no_cache,usage_input_tokens_cache_write_5m,"
                 "usage_input_tokens_cache_write_1h,usage_input_tokens_cache_read,"
                 "usage_output_tokens,web_search_count,inference_geo,speed")


def _write_csv(path: Path, header: str, rows: list[str]):
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def _cost_row(model, key, cost, usage_type="message", token_type="input_no_cache",
              key_id="apikey_x"):
    return (f"2026-11-19,{model},Default,{key},{usage_type},≤ 200k,{token_type},"
            f"{cost},{cost},token,,standard,{key_id},")


def _token_row(model, key, no_cache, w5, w1, read, out):
    return (f"2026-11-19,{model},{key},Default,standard,≤ 200k,"
            f"{no_cache},{w5},{w1},{read},{out},0,,standard")


def test_two_file_totals(tmp_path):
    cost = tmp_path / "cost.csv"
    token = tmp_path / "tokens.csv"
    _write_csv(cost, COST_HEADERS, [
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "9.08"),
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "223.94", token_type="output"),
        _cost_row("Claude Haiku 4.5", "voyager-v1.5-window", "0.34"),
        _cost_row("Claude Haiku 4.5", "voyager-v1.5-window", "6.15", token_type="output"),
        _cost_row("--", "voyager-v1.5-window", "0.00", usage_type="code_execution"),
    ])
    _write_csv(token, TOKEN_HEADERS, [
        _token_row("claude-sonnet-4-6", "voyager-v1.5-window", 100, 10, 20, 30, 2000),
        _token_row("claude-haiku-4-5-20251001", "voyager-v1.5-window", 40, 0, 0, 0, 500),
    ])

    billed = rb._load_billed([cost, token], rb._to_date("2026-11-18"), rb._to_date("2026-11-20"))
    assert billed["totals"]["cost_usd"] == pytest.approx(233.02 + 6.49)
    assert billed["totals"]["input_tokens"] == 160 + 40
    assert billed["totals"]["output_tokens"] == 2000 + 500
    assert billed["models"]["claude-sonnet-4-6"]["cost"] == pytest.approx(233.02)
    assert billed["models"]["claude-sonnet-4-6"]["input"] == 160
    assert billed["models"]["claude-sonnet-4-6"]["output"] == 2000
    assert billed["models"]["claude-haiku-4-5-20251001"]["cost"] == pytest.approx(6.49)
    assert billed["token_parts"] == {"no_cache": 140, "cache_write_5m": 10,
                                     "cache_write_1h": 20, "cache_read": 30}
    assert billed["ignored_rows"] == 1


def test_unknown_cost_model_fails(tmp_path):
    cost = tmp_path / "cost.csv"
    _write_csv(cost, COST_HEADERS, [
        _cost_row("Claude Opus 4", "voyager-v1.5-window", "5.00"),
    ])
    with pytest.raises(SystemExit) as exc:
        rb._load_billed([cost], rb._to_date("2026-11-18"), rb._to_date("2026-11-20"))
    assert "unknown non-zero-cost model" in str(exc.value)


def test_key_filter_excludes_other_keys(tmp_path):
    cost = tmp_path / "cost.csv"
    token = tmp_path / "tokens.csv"
    _write_csv(cost, COST_HEADERS, [
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "9.08"),
        _cost_row("Claude Sonnet 4.6", "abhishek-onboarding-api-key", "0.58"),
    ])
    _write_csv(token, TOKEN_HEADERS, [
        _token_row("claude-sonnet-4-6", "voyager-v1.5-window", 100, 0, 0, 0, 2000),
        _token_row("claude-sonnet-4-6", "abhishek-onboarding-api-key", 5000, 0, 0, 0, 9000),
    ])

    billed = rb._load_billed([cost, token], rb._to_date("2026-11-18"), rb._to_date("2026-11-20"),
                             key_filter="voyager-v1.5-window")
    assert billed["totals"]["cost_usd"] == pytest.approx(9.08)
    assert billed["totals"]["input_tokens"] == 100
    assert billed["totals"]["output_tokens"] == 2000


def test_key_id_filter_splits_two_same_name_keys(tmp_path):
    cost = tmp_path / "cost.csv"
    token = tmp_path / "tokens.csv"
    _write_csv(cost, COST_HEADERS, [
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "9.00", key_id="keyA"),
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "222.00",
                  token_type="output", key_id="keyA"),
        _cost_row("Claude Haiku 4.5", "voyager-v1.5-window", "0.30", key_id="keyA"),
        _cost_row("Claude Haiku 4.5", "voyager-v1.5-window", "6.00",
                  token_type="output", key_id="keyA"),
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "0.30", key_id="keyB"),
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "4.50",
                  token_type="output", key_id="keyB"),
        _cost_row("Claude Haiku 4.5", "voyager-v1.5-window", "0.01", key_id="keyB"),
        _cost_row("Claude Haiku 4.5", "voyager-v1.5-window", "0.10",
                  token_type="output", key_id="keyB"),
    ])
    _write_csv(token, TOKEN_HEADERS, [
        _token_row("claude-sonnet-4-6", "voyager-v1.5-window", 3_000_000, 0, 0, 0, 14_800_000),
        _token_row("claude-sonnet-4-6", "voyager-v1.5-window", 100_000, 0, 0, 0, 300_000),
        _token_row("claude-haiku-4-5-20251001", "voyager-v1.5-window", 300_000, 0, 0, 0, 1_200_000),
        _token_row("claude-haiku-4-5-20251001", "voyager-v1.5-window", 10_000, 0, 0, 0, 20_000),
    ])

    billed = rb._load_billed([cost, token], rb._to_date("2026-11-18"), rb._to_date("2026-11-20"),
                             key_filter="voyager-v1.5-window", key_id_filter="keyA")
    assert billed["key_ids_seen"] == ["keyA", "keyB"]
    assert billed["totals"]["cost_usd"] == pytest.approx(231.00 + 6.30)
    assert billed["totals"]["input_tokens"] == 3_000_000 + 300_000
    assert billed["totals"]["output_tokens"] == 14_800_000 + 1_200_000
    assert billed["models"]["claude-sonnet-4-6"]["cost"] == pytest.approx(231.00)
    assert billed["models"]["claude-sonnet-4-6"]["output"] == 14_800_000
    assert billed["models"]["claude-haiku-4-5-20251001"]["cost"] == pytest.approx(6.30)
    assert len(billed["token_mapping"]) == 4
    assert all(m["key_id"] for m in billed["token_mapping"])


def test_key_id_filter_ambiguous_token_row_fails(tmp_path):
    cost = tmp_path / "cost.csv"
    token = tmp_path / "tokens.csv"
    _write_csv(cost, COST_HEADERS, [
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "9.00", key_id="keyA"),
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "222.00",
                  token_type="output", key_id="keyA"),
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "9.00", key_id="keyB"),
        _cost_row("Claude Sonnet 4.6", "voyager-v1.5-window", "222.00",
                  token_type="output", key_id="keyB"),
    ])
    # One Sonnet token row whose list-price cost matches both keyA and keyB.
    _write_csv(token, TOKEN_HEADERS, [
        _token_row("claude-sonnet-4-6", "voyager-v1.5-window", 3_000_000, 0, 0, 0, 14_800_000),
    ])
    with pytest.raises(SystemExit) as exc:
        rb._load_billed([cost, token], rb._to_date("2026-11-18"), rb._to_date("2026-11-20"),
                        key_filter="voyager-v1.5-window", key_id_filter="keyA")
    assert "ambiguous token row" in str(exc.value)

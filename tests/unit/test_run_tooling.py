"""Run tooling (§8, §10): failure log, rerun-failures and sharding."""
import argparse
import asyncio
import csv
import json
import threading
from unittest.mock import patch

from scripts import benchmark_queries as bq
from scripts import failure_log as fl
from scripts import run_shards as rs


def _write_manifest(path, n=25, prefix="SYN"):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["query_id", "pair_id", "set", "query_text"])
        for i in range(1, n + 1):
            w.writerow([f"{prefix}-{i:02d}", f"P{i:02d}", "synthetic", f"query {i}"])
    return path


def _plan(tmp_path, n=2, shards=2, conditions=("baseline", "targeted")):
    manifest = _write_manifest(tmp_path / "manifest.csv", n=n)
    out = tmp_path / "out"
    rs.cmd_plan(argparse.Namespace(
        set="synthetic", shards=shards, repeats=1, out=str(out),
        conditions=list(conditions), manifest=str(manifest), inventory=None,
    ))
    return out


def _session(qid, cond, run=1, marker="", duration=None):
    s = {
        "session_id": f"{qid}-{cond}-{run}-{marker or 'x'}",
        "session_tags": {"query_id": qid, "condition": cond, "run_index": run},
        "marker": marker,
    }
    if duration is not None:
        s["duration_s"] = duration
    return s


def _write_failures(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fl.FAILURES_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fl.FAILURES_COLUMNS})


# ── plan ──────────────────────────────────────────────────────────────────────

def test_plan_covers_every_query_exactly_once_with_balanced_shards(tmp_path):
    manifest = _write_manifest(tmp_path / "manifest.csv", n=25)
    out = tmp_path / "out"
    rc = rs.cmd_plan(argparse.Namespace(
        set="synthetic", shards=4, repeats=5, out=str(out),
        conditions=None, manifest=str(manifest), inventory=None,
    ))
    assert rc == 0
    plan = json.loads((out / "plan.json").read_text())
    ids = [qid for shard in plan["shards"] for qid in shard["query_ids"]]
    assert len(ids) == 25 and len(set(ids)) == 25
    sizes = [len(shard["query_ids"]) for shard in plan["shards"]]
    assert max(sizes) - min(sizes) <= 1
    assert [shard["offset"] for shard in plan["shards"]] == [0, 7, 13, 19]
    assert [shard["limit"] for shard in plan["shards"]] == [7, 6, 6, 6]
    # contiguous whole-query assignment: offsets/limits tile the query list
    assert ids == [f"SYN-{i:02d}" for i in range(1, 26)]


def test_shard_env_defaults_failures_and_plans_paths(tmp_path, monkeypatch):
    out = _plan(tmp_path)
    plan = json.loads((out / "plan.json").read_text())
    shard = plan["shards"][0]
    monkeypatch.delenv("VOYAGER_FAILURES_PATH", raising=False)
    monkeypatch.delenv("VOYAGER_PLANS_DIR", raising=False)
    env = rs._shard_env(plan, shard, out)
    assert env["VOYAGER_FAILURES_PATH"] == "results/v1.5/failures.csv"
    assert env["VOYAGER_PLANS_DIR"] == "results/v1.5/plans"


def test_shard_env_honours_env_overrides(tmp_path, monkeypatch):
    out = _plan(tmp_path)
    plan = json.loads((out / "plan.json").read_text())
    shard = plan["shards"][0]
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", "/tmp/custom-failures.csv")
    monkeypatch.setenv("VOYAGER_PLANS_DIR", "/tmp/custom-plans")
    env = rs._shard_env(plan, shard, out)
    assert env["VOYAGER_FAILURES_PATH"] == "/tmp/custom-failures.csv"
    assert env["VOYAGER_PLANS_DIR"] == "/tmp/custom-plans"


def test_merge_reads_failures_path_recorded_in_launch_json(tmp_path, monkeypatch):
    out = _plan(tmp_path)
    (out / "shard_1").mkdir()
    (out / "shard_2").mkdir()
    (out / "shard_1" / "sessions.jsonl").write_text(
        json.dumps(_session("SYN-01", "baseline", 1)) + "\n"
        + json.dumps(_session("SYN-01", "targeted", 1)) + "\n"
        + json.dumps(_session("SYN-02", "targeted", 1)) + "\n"
    )
    (out / "shard_2" / "sessions.jsonl").write_text("")
    custom = tmp_path / "custom-failures.csv"
    _write_failures(custom, [
        {"set": "synthetic", "query_id": "SYN-02", "condition": "baseline",
         "run_index": 1, "reason": "x", "rerun_ok": "false"},
    ])
    (out / "launch.json").write_text(json.dumps({"failures_path": str(custom)}) + "\n")
    monkeypatch.delenv("VOYAGER_FAILURES_PATH", raising=False)

    rc = rs.cmd_merge(argparse.Namespace(out=str(out)))
    assert rc == 0


# ── launch refusal ────────────────────────────────────────────────────────────

def test_refuses_shards_over_one_in_capture():
    assert rs._shard_refusal(4, "capture", False) is not None
    assert rs._shard_refusal(1, "capture", False) is None


def test_refuses_shards_over_one_with_replay_record_missing():
    assert rs._shard_refusal(4, "replay", True) is not None
    assert rs._shard_refusal(1, "replay", True) is None
    assert rs._shard_refusal(4, "mock", False) is None


# ── failure log under concurrency ─────────────────────────────────────────────

def test_failures_writer_under_concurrent_writers(tmp_path):
    path = tmp_path / "failures.csv"

    def worker(tag):
        for i in range(50):
            fl.append_failure({
                "set": "synthetic", "query_id": f"Q-{tag}-{i:03d}",
                "condition": "baseline", "run_index": 1,
                "reason": "RuntimeError: boom", "rerun_ok": "",
            }, path=path)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = fl.load_failures(path)
    assert len(rows) == 200
    assert all(set(r.keys()) == set(fl.FAILURES_COLUMNS) for r in rows)
    assert all(r["reason"] == "RuntimeError: boom" for r in rows)
    assert path.read_text().count("\n") == 201  # header + 200 intact rows


def test_reason_is_class_and_message_truncated():
    text = fl.reason_for(RuntimeError("x" * 2000))
    assert text.startswith("RuntimeError: ")
    assert len(text) == fl.REASON_MAX_CHARS


# ── run_conditions logs failures (§8) ─────────────────────────────────────────

def test_run_conditions_appends_a_failure_row(tmp_path, monkeypatch):
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(tmp_path / "failures.csv"))

    async def fake_run(query, **kwargs):
        if kwargs["session_tags"]["query_id"] == "SYN-02":
            raise RuntimeError("boom")

    with patch("graph.travel_graph.run_collaborative_travel_query", fake_run):
        asyncio.run(bq.run_conditions(
            [("SYN-01", "q1"), ("SYN-02", "q2")], ["baseline"], 1,
            query_set="synthetic",
        ))

    rows = fl.load_failures(tmp_path / "failures.csv")
    assert len(rows) == 1
    assert rows[0]["set"] == "synthetic"
    assert rows[0]["query_id"] == "SYN-02"
    assert rows[0]["condition"] == "baseline"
    assert rows[0]["run_index"] == "1"
    assert rows[0]["reason"].startswith("RuntimeError: boom")
    assert rows[0]["rerun_ok"] == ""


# ── rerun-failures: once + idempotent ─────────────────────────────────────────

def test_rerun_failures_once_then_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "failures.csv"
    fl.append_failure({"set": "synthetic", "query_id": "SYN-01",
                       "condition": "baseline", "run_index": 1,
                       "reason": "x", "rerun_ok": ""}, path=path)
    fl.append_failure({"set": "synthetic", "query_id": "SYN-02",
                       "condition": "targeted", "run_index": 2,
                       "reason": "y", "rerun_ok": ""}, path=path)
    manifest = _write_manifest(tmp_path / "manifest.csv", n=2)

    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(path))
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(tmp_path / "sessions.jsonl"))

    calls = []

    async def fake_run(query, **kwargs):
        calls.append((query, kwargs["session_tags"]))

    with patch("graph.travel_graph.run_collaborative_travel_query", fake_run):
        asyncio.run(bq.rerun_failures(str(manifest)))

    assert len(calls) == 2
    assert calls[0][1] == {"query_id": "SYN-01", "condition": "baseline", "run_index": 1}
    assert calls[1][1] == {"query_id": "SYN-02", "condition": "targeted", "run_index": 2}
    rows = fl.load_failures(path)
    assert all(r["rerun_ok"] == "true" for r in rows)

    # A second rerun must do nothing (every row already has rerun_ok set).
    calls.clear()
    with patch("graph.travel_graph.run_collaborative_travel_query", fake_run):
        asyncio.run(bq.rerun_failures(str(manifest)))
    assert calls == []


def test_rerun_failure_marks_rerun_ok_false(tmp_path, monkeypatch):
    path = tmp_path / "failures.csv"
    fl.append_failure({"set": "synthetic", "query_id": "SYN-01",
                       "condition": "baseline", "run_index": 1,
                       "reason": "x", "rerun_ok": ""}, path=path)
    manifest = _write_manifest(tmp_path / "manifest.csv", n=1)
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(path))
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(tmp_path / "sessions.jsonl"))

    async def fake_run(query, **kwargs):
        raise RuntimeError("still down")

    with patch("graph.travel_graph.run_collaborative_travel_query", fake_run):
        asyncio.run(bq.rerun_failures(str(manifest)))
    assert fl.load_failures(path)[0]["rerun_ok"] == "false"


# ── merge ─────────────────────────────────────────────────────────────────────

def test_merge_keeps_latest_session_for_a_rerun_failure(tmp_path, monkeypatch):
    out = _plan(tmp_path)
    (out / "shard_1").mkdir()
    (out / "shard_2").mkdir()
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(out / "failures.csv"))
    old = _session("SYN-02", "baseline", 1, marker="old")
    new = _session("SYN-02", "baseline", 1, marker="new")
    (out / "shard_1" / "sessions.jsonl").write_text(
        json.dumps(_session("SYN-01", "baseline", 1)) + "\n"
        + json.dumps(_session("SYN-01", "targeted", 1)) + "\n"
        + json.dumps(old) + "\n"
    )
    (out / "shard_2" / "sessions.jsonl").write_text(
        json.dumps(_session("SYN-02", "targeted", 1)) + "\n"
    )
    (out / "reruns.jsonl").write_text(json.dumps(new) + "\n")
    _write_failures(out / "failures.csv", [
        {"set": "synthetic", "query_id": "SYN-02", "condition": "baseline",
         "run_index": 1, "reason": "x", "rerun_ok": "true"},
    ])

    rc = rs.cmd_merge(argparse.Namespace(out=str(out)))
    assert rc == 0
    merged = [json.loads(line) for line in (out / "sessions.jsonl").read_text().splitlines()]
    assert len(merged) == 4
    by_key = {(_session_key(s)): s for s in merged}
    assert by_key[("SYN-02", "baseline", 1)]["marker"] == "new"


def _session_key(s):
    tags = s["session_tags"]
    return (tags["query_id"], tags["condition"], tags["run_index"])


def test_merge_catches_duplicates_and_missing_cells(tmp_path, monkeypatch):
    out = _plan(tmp_path)
    (out / "shard_1").mkdir()
    (out / "shard_2").mkdir()
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(out / "failures.csv"))
    dup = _session("SYN-01", "baseline", 1)
    (out / "shard_1" / "sessions.jsonl").write_text(
        json.dumps(dup) + "\n" + json.dumps(dup) + "\n"
        + json.dumps(_session("SYN-01", "targeted", 1)) + "\n"
    )
    (out / "shard_2" / "sessions.jsonl").write_text("")

    rc = rs.cmd_merge(argparse.Namespace(out=str(out)))
    assert rc == 2


def test_merge_excludes_failures_marked_false_as_dropped(tmp_path, monkeypatch):
    out = _plan(tmp_path)
    (out / "shard_1").mkdir()
    (out / "shard_2").mkdir()
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(out / "failures.csv"))
    (out / "shard_1" / "sessions.jsonl").write_text(
        json.dumps(_session("SYN-01", "baseline", 1)) + "\n"
        + json.dumps(_session("SYN-01", "targeted", 1)) + "\n"
        + json.dumps(_session("SYN-02", "targeted", 1)) + "\n"
    )
    (out / "shard_2" / "sessions.jsonl").write_text("")
    # SYN-02 baseline re-run also failed → dropped, so it must not be an error.
    _write_failures(out / "failures.csv", [
        {"set": "synthetic", "query_id": "SYN-02", "condition": "baseline",
         "run_index": 1, "reason": "x", "rerun_ok": "false"},
    ])

    rc = rs.cmd_merge(argparse.Namespace(out=str(out)))
    assert rc == 0


# ── status ────────────────────────────────────────────────────────────────────

def test_status_parses_fixture_log(tmp_path, capsys, monkeypatch):
    out = _plan(tmp_path)
    (out / "shard_1").mkdir()
    (out / "shard_2").mkdir()
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(out / "failures.csv"))
    (out / "shard_1" / "sessions.jsonl").write_text(
        json.dumps(_session("SYN-01", "baseline", 1, duration=10)) + "\n"
        + json.dumps(_session("SYN-01", "targeted", 1, duration=20)) + "\n"
    )
    (out / "shard_1" / "run.log").write_text(
        "2026-11-18 request\n"
        "2026-11-18 retrying after 429\n"
        "2026-11-18 overloaded, backing off\n"
    )
    _write_failures(out / "failures.csv", [
        {"set": "synthetic", "query_id": "SYN-01", "condition": "baseline",
         "run_index": 1, "reason": "x", "rerun_ok": "false"},
    ])

    rc = rs.cmd_status(argparse.Namespace(out=str(out)))
    assert rc == 0
    captured = capsys.readouterr().out
    assert " 2 " in captured      # shard_1 expected
    assert " 1 " in captured      # shard_1 failures
    assert " 2 " in captured      # 429 + overloaded
    assert "15.0" in captured     # mean session time

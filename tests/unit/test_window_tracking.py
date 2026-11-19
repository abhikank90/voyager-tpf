"""Every window call is tracked (§8): eval usage, canary usage, failure partials, reconcile."""
import asyncio
import csv
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from metrics import token_tracker
from scripts import benchmark_queries as bq
from scripts import failure_log as fl
from scripts import reconcile_billing as rb

# ── eval_hybrid_detection: usage in both reports ─────────────────────────────

@pytest.fixture
def probe_file(tmp_path):
    doc = {
        "version": 1,
        "types": ["location_mismatch", "timing_inefficiency", "weather_activity_mismatch",
                  "checkin_impossible", "budget_violation", "visa_timing", "preference_mismatch"],
        "probes": [{
            "id": "T-01", "name": "clean", "group": "test",
            "construction": {"location_mismatch": "silent", "timing_inefficiency": "silent",
                             "weather_activity_mismatch": "silent"},
            "state": {
                "intent": {"destination": "Greece", "budget_usd": 2000,
                           "interests": ["beaches"], "travel_month": "July 2026"},
                "collaboration_round": 1,
                "selected_hotel": {"name": "Santorini Resort", "location": "Santorini",
                                   "total_usd": 700, "lat": 36.39, "lon": 25.46},
                "experiences": [{"name": "Red Beach", "category": "beach", "location": "Santorini",
                                 "lat": 36.36, "lon": 25.4}],
                "selected_flight": {"airline": "LH", "arrival": "2026-07-01T13:00:00",
                                    "price_usd": 890},
                "weather": {"avg_temp_c": 28, "summary": "warm", "precipitation_mm": 2},
                "visa_safety": {"visa_required": False, "safety_level": "safe"},
                "flights": [], "hotels": [], "agent_messages": [], "conflicts": [],
            },
            "gold": None,
            "pilot_truth_v141": None,
        }],
    }
    path = tmp_path / "rq4_probes.json"
    path.write_text(json.dumps(doc))
    return path


def test_isolation_report_records_usage(tmp_path, monkeypatch, probe_file):
    import scripts.eval_hybrid_detection as ev
    from agents.collaboration_hub import CollaborationHubAgent

    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="[]")]
    fake_response.usage = MagicMock(input_tokens=100, output_tokens=50)
    fake_client.messages.create.return_value = fake_response
    rules_hub = CollaborationHubAgent.__new__(CollaborationHubAgent)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ci-test-key")
    monkeypatch.setattr(ev, "make_client", lambda: fake_client)
    monkeypatch.setattr(ev, "build_rules_hub", lambda: rules_hub)

    out = tmp_path / "report.json"
    ev.main(["--probes", str(probe_file), "--out", str(out)])

    usage = json.loads(out.read_text())["usage"]
    assert usage["component"] == "rq4_isolation"
    assert usage["input_tokens"] == 200  # two detector runs × 100
    assert usage["output_tokens"] == 100
    assert usage["cost_usd"] > 0
    assert len(usage["calls"]) == 2
    assert all(c["component"] == "rq4_isolation" for c in usage["calls"])


def test_shipped_report_records_usage(tmp_path, monkeypatch, probe_file):
    import scripts.eval_hybrid_detection as ev

    hub = ev.build_rules_hub()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=json.dumps({"candidates": []}))]
    fake_response.usage = MagicMock(input_tokens=2, output_tokens=3)
    hub.client.messages.create.return_value = fake_response

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ci-test-key")
    monkeypatch.setattr(ev, "make_client", lambda: MagicMock())
    monkeypatch.setattr(ev, "build_rules_hub", lambda: hub)

    out = tmp_path / "shipped.json"
    ev.main(["--probes", str(probe_file), "--variant", "shipped", "--out", str(out)])

    usage = json.loads(out.read_text())["usage"]
    assert usage["component"] == "rq4_shipped"
    assert usage["input_tokens"] == 2 * 3  # llm_detector_repetitions = 3
    assert usage["output_tokens"] == 3 * 3
    assert len(usage["calls"]) == 3


# ── model_manifest: per-snapshot canary usage ────────────────────────────────

def test_take_snapshot_records_canary_usage():
    from scripts import model_manifest as mm

    class FakeUsage:
        input_tokens = 10
        output_tokens = 20

    class FakeResponse:
        usage = FakeUsage()
        content = [SimpleNamespace(text="1, 2, 3, 5, 7, 11, 13, 17, 19, 23")]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeModels:
        def retrieve(self, model_id):
            return {"id": model_id, "created_at": "2026-01-01T00:00:00Z",
                    "display_name": model_id}

    class FakeClient:
        models = FakeModels()
        messages = FakeMessages()

    snapshot = mm.take_snapshot(FakeClient(), "start", ["claude-haiku-4-5-20251001"])
    usage = snapshot["usage"]
    assert usage["component"] == "canary"
    assert usage["input_tokens"] == 10 * mm.CANARY_REPEATS * len(mm.CANARIES)
    assert usage["output_tokens"] == 20 * mm.CANARY_REPEATS * len(mm.CANARIES)
    assert usage["cost_usd"] > 0
    assert len(usage["calls"]) == mm.CANARY_REPEATS * len(mm.CANARIES)


# ── failure log: partial usage trailing columns ──────────────────────────────

def test_failure_row_carries_partial_usage(tmp_path):
    path = tmp_path / "f.csv"
    fl.append_failure({
        "set": "synthetic", "query_id": "SYN-01", "condition": "baseline",
        "run_index": 1, "reason": "x", "rerun_ok": "",
        "input_tokens": 123, "output_tokens": 45, "cost_usd": 0.5,
    }, path=path)
    header = path.read_text().splitlines()[0]
    assert header == "set,query_id,condition,run_index,reason,rerun_ok,input_tokens,output_tokens,cost_usd"
    row = fl.load_failures(path)[0]
    assert row["input_tokens"] == "123" and row["cost_usd"] == "0.5"


def test_failure_writer_tolerates_old_six_column_shape(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("set,query_id,condition,run_index,reason,rerun_ok\n"
                    "synthetic,SYN-01,baseline,1,x,\n")
    fl.append_failure({
        "set": "synthetic", "query_id": "SYN-02", "condition": "targeted",
        "run_index": 1, "reason": "y", "rerun_ok": "",
        "input_tokens": 9, "output_tokens": 8, "cost_usd": 0.1,
    }, path=path)
    assert path.read_text().splitlines()[0] == "set,query_id,condition,run_index,reason,rerun_ok"
    rows = fl.load_failures(path)
    assert len(rows) == 2
    assert "input_tokens" not in rows[1]


def test_set_rerun_ok_preserves_old_shape(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("set,query_id,condition,run_index,reason,rerun_ok\n"
                    "synthetic,SYN-01,baseline,1,x,\n")
    assert fl.set_rerun_ok("synthetic", "SYN-01", "baseline", 1, "true", path=path)
    assert path.read_text().splitlines()[0] == "set,query_id,condition,run_index,reason,rerun_ok"
    assert fl.load_failures(path)[0]["rerun_ok"] == "true"


# ── reconcile: every tracked source reported, filters applied ────────────────

def test_tracked_side_counts_all_sources(tmp_path):
    (tmp_path / "runs" / "synthetic" / "shard_1").mkdir(parents=True)
    (tmp_path / "runs" / "synthetic" / "shard_1" / "sessions.jsonl").write_text(json.dumps({
        "timestamp": "2026-11-18T10:00:00Z", "input_tokens": 1000, "output_tokens": 2000,
        "estimated_cost_usd": 10.0,
        "per_model_tokens": {"claude-sonnet-4-6": {"input": 1000, "output": 2000}},
    }) + "\n")
    (tmp_path / "runs" / "synthetic" / "reruns.jsonl").write_text(json.dumps({
        "timestamp": "2026-11-18T11:00:00Z", "input_tokens": 100, "output_tokens": 200,
        "estimated_cost_usd": 1.0,
        "per_model_tokens": {"claude-sonnet-4-6": {"input": 100, "output": 200}},
    }) + "\n")
    (tmp_path / "failures.csv").write_text(
        "set,query_id,condition,run_index,reason,rerun_ok,input_tokens,output_tokens,cost_usd\n"
        "synthetic,SYN-01,baseline,1,x,,50,50,0.5\n")
    (tmp_path / "rq4").mkdir()
    (tmp_path / "rq4" / "isolation.json").write_text(json.dumps(
        {"usage": {"component": "rq4_isolation", "input_tokens": 10,
                   "output_tokens": 20, "cost_usd": 0.15}}))
    (tmp_path / "model_manifest.json").write_text(json.dumps({"snapshots": [
        {"taken_at": "2026-11-18T09:00:00Z",
         "usage": {"component": "canary", "input_tokens": 5, "output_tokens": 5, "cost_usd": 0.05}},
    ]}))

    tracked = rb._load_tracked(tmp_path, rb._to_date("2026-01-01"), rb._to_date("2030-01-01"))
    sources = tracked["sources"]
    assert sources["sessions"]["input_tokens"] == 1000
    assert sources["rerun_sessions"]["input_tokens"] == 100
    assert sources["failed_sessions"]["input_tokens"] == 50
    assert sources["rq4"]["input_tokens"] == 10
    assert sources["canary"]["input_tokens"] == 5
    assert tracked["totals"]["input_tokens"] == 1165
    assert tracked["totals"]["cost_usd"] == pytest.approx(11.7)


def _raw_session(input_tokens, output_tokens, cost_usd, session_id):
    return {
        "session_id": session_id,
        "timestamp": "2026-11-18T10:00:00Z",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": cost_usd,
        "per_model_tokens": {"claude-sonnet-4-6": {"input": input_tokens, "output": output_tokens}},
    }


def test_raw_files_counted_once_and_merged_ignored(tmp_path):
    root = tmp_path
    # Raw shard sessions (the only sessions source).
    (root / "runs" / "synthetic" / "shard_1").mkdir(parents=True)
    (root / "runs" / "synthetic" / "shard_2").mkdir(parents=True)
    (root / "runs" / "synthetic" / "shard_1" / "sessions.jsonl").write_text(
        json.dumps(_raw_session(10, 20, 0.10, "s1")) + "\n")
    (root / "runs" / "synthetic" / "shard_2" / "sessions.jsonl").write_text(
        json.dumps(_raw_session(30, 40, 0.30, "s2")) + "\n")
    # A rerun session (recursive, under a replay dir).
    (root / "runs" / "real" / "replay_1").mkdir(parents=True)
    (root / "runs" / "real" / "replay_1" / "reruns.jsonl").write_text(
        json.dumps(_raw_session(5, 6, 0.05, "r1")) + "\n")
    # Merged + combined files that MUST be ignored (same keys as the raw ones).
    (root / "runs" / "synthetic" / "sessions.jsonl").write_text(
        json.dumps(_raw_session(999, 999, 9.99, "s-merged")) + "\n")
    (root / "runs" / "real" / "sessions.jsonl").write_text(
        json.dumps(_raw_session(999, 999, 9.99, "s-combined")) + "\n")

    tracked = rb._load_tracked(root, rb._to_date("2026-01-01"), rb._to_date("2030-01-01"))
    sources = tracked["sources"]
    assert sources["sessions"]["n"] == 2
    assert sources["sessions"]["input_tokens"] == 40
    assert sources["rerun_sessions"]["n"] == 1
    assert sources["rerun_sessions"]["input_tokens"] == 5
    assert tracked["totals"]["input_tokens"] == 45
    assert tracked["totals"]["cost_usd"] == pytest.approx(0.45)
    # The file list reports the raw files used.
    assert "runs/synthetic/shard_1/sessions.jsonl" in sources["sessions"]["files"]
    assert "runs/synthetic/shard_2/sessions.jsonl" in sources["sessions"]["files"]
    assert "runs/real/replay_1/reruns.jsonl" in sources["rerun_sessions"]["files"]
    assert "runs/synthetic/sessions.jsonl" not in sources["sessions"]["files"]
    assert "runs/real/sessions.jsonl" not in sources["sessions"]["files"]


def test_key_filter_isolates_rows(tmp_path):
    export = tmp_path / "usage.csv"
    with open(export, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Model", "Input tokens", "Output tokens", "Cost (USD)", "API key"])
        w.writerow(["2026-11-18", "claude-sonnet-4-6", "100", "200", "1.00", "sk-ant-A"])
        w.writerow(["2026-11-18", "claude-sonnet-4-6", "50", "50", "0.50", "sk-ant-B"])
    rows = rb._parse_export(export, key_filter="sk-ant-A")
    assert len(rows) == 1
    assert rows[0]["input"] == 100


def test_key_filter_without_key_column_fails(tmp_path):
    export = tmp_path / "usage.csv"
    export.write_text("Date,Model,Input tokens,Output tokens,Cost (USD)\n"
                      "2026-11-18,claude-sonnet-4-6,100,200,1.00\n")
    with pytest.raises(SystemExit):
        rb._parse_export(export, key_filter="sk-ant-A")


# ── failed re-run: partial usage is summed, never overwritten ────────────────

def test_rerun_failure_sums_partial_usage(tmp_path, monkeypatch):
    path = tmp_path / "failures.csv"
    fl.append_failure({
        "set": "synthetic", "query_id": "SYN-01", "condition": "baseline",
        "run_index": 1, "reason": "first crash", "rerun_ok": "",
        "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.75,
    }, path=path)
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("query_id,pair_id,set,query_text\nSYN-01,P01,synthetic,q1\n")
    monkeypatch.setenv("VOYAGER_FAILURES_PATH", str(path))
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(tmp_path / "sessions.jsonl"))

    async def fake_run(query, **kwargs):
        token_tracker.start_session()
        token_tracker.track_usage(20, 10, model="claude-sonnet-4-6", component="intent_parser")
        raise RuntimeError("still down")

    with patch("graph.travel_graph.run_collaborative_travel_query", fake_run):
        asyncio.run(bq.rerun_failures(str(manifest)))

    row = fl.load_failures(path)[0]
    assert row["rerun_ok"] == "false"
    assert row["input_tokens"] == "120"      # 100 + 20
    assert row["output_tokens"] == "60"      # 50 + 10
    expected_cost = 0.75 + token_tracker.compute_cost(20, 10, "claude-sonnet-4-6")
    assert float(row["cost_usd"]) == pytest.approx(round(expected_cost, 6))

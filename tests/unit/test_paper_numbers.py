"""Tests for scripts/paper_numbers.py — small in-memory fixtures, no live data."""
import csv
import json
from pathlib import Path

import pytest

from scripts import paper_numbers as pn


def _session(query_id, condition, run_index=1, *, final=0, r1_types=None,
             lifecycle=None, convergence=None, invocations=5, cost=0.2, wall=10.0,
             nodes=None, per_component=None, recipients=None, r2=(), r3=(),
             unresolved=None, tags=None):
    return {
        "query": f"{query_id} {condition} {run_index}",
        "query_id": query_id,
        "condition": condition,
        "run_index": run_index,
        "session_tags": tags or {"query_id": query_id, "condition": condition,
                                 "run_index": run_index},
        "final_conflict_count": final,
        "round_1_conflict_types": r1_types or [],
        "conflict_lifecycle": lifecycle or [],
        "convergence_summary": convergence or {"converged_round": None},
        "actual_agent_calls": invocations,
        "estimated_cost_usd": cost,
        "duration_s": wall,
        "node_durations_s": nodes or {},
        "per_component_usage": per_component or {},
        "round_1_message_recipients": recipients or [],
        "round_2_agents_rerun": list(r2),
        "round_3_agents_rerun": list(r3),
        "round_3_agents_rerun_count": len(r3),
        "unresolved_reason_counts": unresolved or {},
    }


def _lifecycle_entry(fp, status, first_seen=1, agents=("flight", "hotel"), type_="location_mismatch"):
    return {
        "fingerprint": fp, "status": status, "first_seen_round": first_seen,
        "last_seen_round": 1, "agents": list(agents), "type": type_,
        "resolved_in_round": None, "persistence_count": 0, "introduced_after_agents": None,
    }


# ── Pairing by tags, not text ─────────────────────────────────────────────────

def test_session_key_uses_tags_over_text():
    s = {"query_id": "WRONG", "session_tags": {"query_id": "SYN-01",
                                                "condition": "targeted", "run_index": 2}}
    assert pn.session_key(s) == ("SYN-01", "targeted", 2)


def test_pair_queries_aligned_on_common_ids():
    a = {"Q1": 1.0, "Q2": 2.0, "Q3": 3.0}
    b = {"Q1": 9.0, "Q3": 7.0}
    x, y, ids = pn.pair_queries(a, b)
    assert ids == ["Q1", "Q3"]
    assert x == [1.0, 3.0] and y == [9.0, 7.0]


def test_h1c_cost_is_descriptive(tmp_path):
    syn = []
    for q in ("SYN-01", "SYN-02"):
        for r in (1, 2):
            syn.append(_session(q, "targeted", r, cost=0.20))
            syn.append(_session(q, "broadcast", r, cost=0.30))
    real = []
    for q in ("REAL-01", "REAL-02"):
        real.append(_session(q, "targeted", 1, cost=0.25))
        real.append(_session(q, "broadcast", 1, cost=0.35))

    row = pn.row_h1(tmp_path, {"synthetic": syn, "real": real}, allow_missing=True)
    c = row["h1_c_cost"]
    assert c["synthetic"]["n"] == 2
    assert c["synthetic"]["estimate"] == pytest.approx(-0.10)
    assert c["synthetic"]["equal_or_lower"] is True
    assert c["real"]["n"] == 2
    assert c["real"]["estimate"] == pytest.approx(-0.10)
    assert "not in the Holm family" in c["method"]


# ── §8 drop removes a query from every condition ─────────────────────────────

def test_apply_drops_removes_query_from_all_conditions():
    sessions = [
        _session("Q1", "baseline"), _session("Q1", "targeted"), _session("Q1", "two_way"),
        _session("Q2", "baseline"), _session("Q2", "targeted"),
    ]
    dropped = pn.dropped_queries([{"set": "synthetic", "query_id": "Q1", "rerun_ok": "false"}])
    kept = pn.apply_drops(sessions, "synthetic", dropped)
    assert {pn.session_key(s)[0] for s in kept} == {"Q2"}
    assert {pn.session_key(s)[1] for s in kept} == {"baseline", "targeted"}


# ── Per-query median across runs ─────────────────────────────────────────────

def test_per_query_median_across_runs():
    sessions = [
        _session("Q1", "targeted", 1, final=1),
        _session("Q1", "targeted", 2, final=3),
        _session("Q1", "targeted", 3, final=2),
        _session("Q2", "targeted", 1, final=0),
    ]
    out = pn.per_query(sessions, pn.final_conflicts)
    assert out == {"Q1": 2, "Q2": 0}


# ── Per-session metrics on a hand-built lifecycle ────────────────────────────

def test_lifecycle_metrics():
    s = _session("Q1", "targeted", lifecycle=[
        _lifecycle_entry("a", "resolved", first_seen=1),
        _lifecycle_entry("b", "persisting", first_seen=1),
        _lifecycle_entry("c", "new", first_seen=2),
        _lifecycle_entry("d", "reopened", first_seen=1),
    ])
    assert pn.resolution_rate_conflict(s) == pytest.approx(1 / 3)
    assert pn.introductions(s) == 1
    assert pn.reopened(s) == 1
    assert pn.final_conflicts(s) == 0


# ── H2c U handling and contingency ───────────────────────────────────────────

def _write_h2c(root, author_rows, rater_rows):
    (root / "h2c" / "author").mkdir(parents=True)
    (root / "h2c" / "rater").mkdir(parents=True)
    import csv

    with open(root / "h2c" / "author" / "author_scores.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["plan_id"] + list(pn.H2C_DIMENSIONS)
                           + ["fully_coherent", "notes"])
        w.writeheader()
        w.writerows(author_rows)
    with open(root / "h2c" / "rater" / "rater_scores.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["plan_id"] + list(pn.H2C_DIMENSIONS)
                           + ["fully_coherent", "notes"])
        w.writeheader()
        w.writerows(rater_rows)
    (root / "h2c" / "KEY_DO_NOT_SHARE.csv").write_text("plan_id,session_id\n")


def test_h2c_u_handling_and_contingency(tmp_path):
    author = [
        {"plan_id": "p1", "D1_temporal": "P", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "Y", "notes": ""},
        {"plan_id": "p2", "D1_temporal": "F", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "N", "notes": ""},
        {"plan_id": "p3", "D1_temporal": "U", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "Y", "notes": ""},
    ]
    rater = [
        {"plan_id": "p1", "D1_temporal": "P", "D2_budget": "P", "D3_preference": "F",
         "D4_logistics": "P", "fully_coherent": "Y", "notes": ""},
    ]
    _write_h2c(tmp_path, author, rater)
    row = pn.row_h2c(tmp_path, allow_missing=True)
    assert row["status"] == "ok"
    assert row["assessable_n"] == 2  # p3 (U) excluded
    assert row["fully_coherent_rate"] == pytest.approx(0.5)  # p1 only
    assert row["pass"] is False
    assert row["u_rate"] == pytest.approx(1 / 3)
    assert row["contingency_triggered"] is True  # D3 agreement = 0.0 < 0.80


def test_h2c_round2_and_combined(tmp_path):
    author = [
        {"plan_id": "p1", "D1_temporal": "P", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "Y", "notes": ""},
        {"plan_id": "p2", "D1_temporal": "F", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "N", "notes": ""},
        {"plan_id": "p3", "D1_temporal": "P", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "Y", "notes": ""},
    ]
    rater1 = [
        {"plan_id": "p1", "D1_temporal": "P", "D2_budget": "P", "D3_preference": "F",
         "D4_logistics": "P", "fully_coherent": "Y", "notes": ""},
    ]
    rater2 = [
        {"plan_id": "p2", "D1_temporal": "F", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "N", "notes": ""},
    ]
    _write_h2c(tmp_path, author, rater1)
    import csv
    with open(tmp_path / "h2c" / "rater" / "rater_scores_round2.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["plan_id"] + list(pn.H2C_DIMENSIONS)
                           + ["fully_coherent", "notes"])
        w.writeheader()
        w.writerows(rater2)

    row = pn.row_h2c(tmp_path, allow_missing=True)
    assert row["rounds"]["round1"]["n_plans"] == 1
    assert row["rounds"]["round2"]["n_plans"] == 1
    assert row["rounds"]["combined"]["n_plans"] == 2
    assert row["rounds"]["round2"]["double_scored"]["D1_temporal"]["percent_agreement"] == 1.0
    assert row["rounds"]["round2"]["contingency_triggered"] is False
    assert row["contingency_triggered"] is True


def test_h2c_post_hoc_sensitivity(tmp_path):
    author = [
        {"plan_id": "p1", "D1_temporal": "F", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "N", "notes": ""},
        {"plan_id": "p2", "D1_temporal": "P", "D2_budget": "F", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "N", "notes": ""},
        {"plan_id": "p3", "D1_temporal": "P", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "Y", "notes": ""},
    ]
    rater = [
        {"plan_id": "p1", "D1_temporal": "F", "D2_budget": "P", "D3_preference": "P",
         "D4_logistics": "P", "fully_coherent": "N", "notes": ""},
    ]
    _write_h2c(tmp_path, author, rater)
    row = pn.row_h2c(tmp_path, allow_missing=True)
    ph = row["post_hoc_sensitivity"]
    assert ph["fully_coherent_rate_excluding_d1"] == pytest.approx(2 / 3)
    assert ph["f_counts"] == {"D1_temporal": 1, "D2_budget": 1,
                              "D3_preference": 0, "D4_logistics": 0}


# ── H3a mismatch detection ────────────────────────────────────────────────────

def test_h3a_signature_detects_mismatch():
    a = _session("Q1", "targeted", 1, r1_types=["location_mismatch"],
                 recipients=["hotel", "flight"], r2=("hotel",), r3=())
    b = _session("Q1", "targeted", 2, r1_types=["location_mismatch", "timing_inefficiency"],
                 recipients=["hotel", "flight"], r2=("hotel",), r3=())
    assert pn._h3a_signature(a) != pn._h3a_signature(b)


# ── H3b involved / not-involved attribution ──────────────────────────────────

def test_h3b_attributes_variance(tmp_path, monkeypatch):
    (tmp_path / "runs" / "real").mkdir(parents=True)
    sessions = []
    for run in (1, 2, 3):
        sessions.append(_session(
            "Q1", "targeted", run,
            lifecycle=[_lifecycle_entry("a", "resolved", agents=["hotel", "experience"])],
            per_component={
                "flight_agent": {"model": "h", "input": 0, "output": 0, "cost_usd": 0.1 + 0.1 * run},
                "option_generator": {"model": "s", "input": 0, "output": 0, "cost_usd": 0.05},
            },
        ))
    import json as _json
    path = tmp_path / "runs" / "real" / "sessions.jsonl"
    with open(path, "w") as fh:
        for s in sessions:
            fh.write(_json.dumps(s) + "\n")
    monkeypatch.chdir(tmp_path)
    row = pn.row_h3b(tmp_path, {"real": sessions}, allow_missing=True)
    assert row["status"] == "ok"
    # flight is not involved (conflict is hotel/experience); option_generator never is.
    assert row["variance_share_non_conflict"] == pytest.approx(1.0)
    assert row["pass"] is True


# ── H4 precision/recall on a tiny gold set ────────────────────────────────────

def test_precision_recall_tiny_gold():
    types = ["location_mismatch"]
    predicted = {"Q1": {"location_mismatch"}, "Q2": {"location_mismatch"}, "Q3": set()}
    gold = {"Q1": {"location_mismatch"}, "Q2": set(), "Q3": {"location_mismatch"}}
    res = pn._precision_recall(predicted, gold, types)["location_mismatch"]
    assert (res["tp"], res["fp"], res["fn"]) == (1, 1, 1)


def test_h4_controls_report_sessions_and_queries():
    manifest = {"CTL-01": {"set": "control", "pair_id": "", "gold": []},
                "CTL-02": {"set": "control", "pair_id": "", "gold": []}}
    sessions = {"synthetic": [], "control": [
        _session("CTL-01", "targeted", 1, r1_types=["location_mismatch"]),
        _session("CTL-01", "targeted", 2, r1_types=["location_mismatch"]),
        _session("CTL-02", "targeted", 1, r1_types=[]),
    ]}
    row = pn.row_h4(Path("__missing__"), manifest, sessions, allow_missing=True)
    controls = row["rules"]["controls"]
    assert controls["spurious_sessions"] == 2
    assert controls["spurious_queries"] == 1  # only CTL-01 fired
    assert controls["per_type_fires"] == {"location_mismatch": 2}
    assert controls["pass"] is False


def test_h4_recall_gain_fp_cost_and_detector(tmp_path):
    (tmp_path / "rq4").mkdir(parents=True)
    probes = {"version": 1, "types": ["location_mismatch"],
              "probes": [
                  {"id": "P1", "name": "x", "group": "g", "construction": {}, "state": {},
                   "gold": ["checkin_impossible"]},
                  {"id": "P2", "name": "y", "group": "g", "construction": {}, "state": {},
                   "gold": ["location_mismatch"]},
                  {"id": "P3", "name": "z", "group": "g", "construction": {}, "state": {},
                   "gold": []},
              ]}
    isolation = {"results": [
        {"probe": "P1", "rules": [], "llm_runs": [
            {"run": 1, "candidates": [{"type": "checkin_impossible", "agents": ["flight"]}]},
            {"run": 2, "candidates": [{"type": "checkin_impossible", "agents": ["flight"]}]},
        ]},
        {"probe": "P2", "rules": [{"type": "location_mismatch", "fingerprint": "f"}],
         "llm_runs": [
            {"run": 1, "candidates": [{"type": "location_mismatch", "agents": ["hotel"]}]},
            {"run": 2, "candidates": [{"type": "location_mismatch", "agents": ["hotel"]}]},
        ]},
        {"probe": "P3", "rules": [], "llm_runs": [
            {"run": 1, "candidates": []}, {"run": 2, "candidates": []},
        ]},
    ]}
    shipped = {"results": [
        {"probe": "P1", "rules": [],
         "candidates": [{"conflict_type": "checkin_impossible", "agents": ["flight"]}],
         "validated": [{"type": "checkin_impossible", "agents": ["flight"], "fingerprint": "v",
                        "data": {}, "confidence": 0.9, "suggested_rule": None}]},
        {"probe": "P2", "rules": [{"type": "location_mismatch", "fingerprint": "f"}],
         "candidates": [], "validated": []},
        {"probe": "P3", "rules": [],
         "candidates": [{"conflict_type": "timing_inefficiency", "agents": ["flight"]}],
         "validated": [{"type": "timing_inefficiency", "agents": ["flight"], "fingerprint": "v2",
                        "data": {}, "confidence": 0.9, "suggested_rule": None}]},
    ]}
    (tmp_path / "rq4_probes.json").write_text(json.dumps(probes))
    (tmp_path / "rq4" / "isolation.json").write_text(json.dumps(isolation))
    (tmp_path / "rq4" / "shipped.json").write_text(json.dumps(shipped))

    manifest = {"SYN-01": {"set": "synthetic", "pair_id": "P01", "gold": ["location_mismatch"]},
                "CTL-01": {"set": "control", "pair_id": "", "gold": []}}
    syn = [_session("SYN-01", "targeted", 1, r1_types=["location_mismatch"], per_component={
        "flight_agent": {"model": "h", "input": 0, "output": 0, "cost_usd": 0.30},
        "collaboration_hub_llm_detector": {"model": "s", "input": 0, "output": 0, "cost_usd": 0.05},
    })]
    ctl = [_session("CTL-01", "targeted", 1, r1_types=[])]
    sessions = {"synthetic": syn, "control": ctl}

    row = pn.row_h4(tmp_path, manifest, sessions, allow_missing=True)
    assert row["status"] == "ok"
    assert row["recall_gain"]["isolation"]["overall"] == pytest.approx(0.5)
    assert row["recall_gain"]["shipped"]["overall"] == pytest.approx(0.5)
    assert row["fp_rerun_cost_usd"]["total"] == pytest.approx(0.30)
    assert row["fp_rerun_cost_usd"]["no_resolver_count"] == 0
    assert row["detector_cost_usd"] == pytest.approx(0.05)
    assert row["rules"]["controls"]["spurious_sessions"] == 0
    assert row["rules"]["controls"]["pass"] is True


# ── --allow-missing and deterministic JSON ────────────────────────────────────

def test_missing_inputs_exit_nonzero_without_flag(tmp_path):
    with pytest.raises(SystemExit):
        pn.build_report(tmp_path, allow_missing=False)


def test_missing_inputs_rows_with_flag(tmp_path):
    doc, missing = pn.build_report(tmp_path, allow_missing=True)
    assert missing
    assert all(row.get("status") == "missing_input" for row in doc["rows"].values())


# ── RQ4 inter-labeler agreement ──────────────────────────────────────────────

def _write_rq4_agreement_inputs(root, *, kalyan_rows=None, probes=None, key=None):
    """Write rq4_probes.json, rq4_sheet_key.csv and rq4_labels_kalyan.csv fixtures."""
    if probes is None:
        probes = {"version": 1,
                  "types": ["location_mismatch", "timing_inefficiency",
                            "weather_activity_mismatch", "checkin_impossible",
                            "budget_violation", "visa_timing", "preference_mismatch"],
                  "probes": [
                      {"id": "P1", "gold": ["timing_inefficiency"]},
                      {"id": "P2", "gold": []},
                      {"id": "P3", "gold": ["budget_violation", "visa_timing"]},
                  ]}
    if key is None:
        key = [("S01", "P1"), ("S02", "P2"), ("S03", "P3")]
    if kalyan_rows is None:
        kalyan_rows = [("S01", "timing_inefficiency"), ("S02", "none"),
                       ("S03", "budget_violation;visa_timing")]

    (root / "rq4_probes.json").write_text(json.dumps(probes))
    with open(root / "rq4_sheet_key.csv", "w", newline="") as fh:
        fh.write("# Key mapping sheet_id -> probe_id.\n")
        w = csv.writer(fh)
        w.writerow(["sheet_id", "probe_id"])
        w.writerows(key)
    with open(root / "rq4_labels_kalyan.csv", "w", newline="") as fh:
        fh.write("# instructions\n")
        w = csv.writer(fh)
        w.writerow(["sheet_id", "state_summary", "gold_types", "notes"])
        for sid, gold in kalyan_rows:
            w.writerow([sid, "state", gold, ""])


def test_rq4_gold_agreement(tmp_path):
    _write_rq4_agreement_inputs(tmp_path)
    row = pn.row_rq4_gold_agreement(tmp_path, allow_missing=True)
    assert row["status"] == "ok"
    assert row["n_probes"] == 3
    assert row["exact_set_agreement_rate"] == pytest.approx(1.0)
    assert row["disagreements"] == []
    assert row["per_type"]["timing_inefficiency"]["percent_agreement"] == 1.0
    assert row["per_type"]["budget_violation"]["cohen_kappa"] == 1.0


def test_rq4_gold_agreement_lists_disagreements(tmp_path):
    _write_rq4_agreement_inputs(tmp_path, kalyan_rows=[
        ("S01", "timing_inefficiency"), ("S02", "none"),
        ("S03", "visa_timing"),  # Abhishek: budget_violation + visa_timing
    ])
    row = pn.row_rq4_gold_agreement(tmp_path, allow_missing=True)
    assert row["exact_set_agreement_rate"] == pytest.approx(2 / 3)
    assert ("P3", ["budget_violation", "visa_timing"], ["visa_timing"]) in row["disagreements"]
    assert row["per_type"]["budget_violation"]["percent_agreement"] == pytest.approx(2 / 3)


def test_rq4_gold_agreement_raw_first_pass(tmp_path):
    import csv
    _write_rq4_agreement_inputs(tmp_path)  # final: S03 = budget_violation;visa_timing
    (tmp_path / "rq4_kalyan").mkdir()
    with open(tmp_path / "rq4_kalyan" / "rq4_labels_kalyan_raw_export.csv", "w", newline="") as fh:
        fh.write("# instructions\n")
        w = csv.writer(fh)
        w.writerow(["sheet_id", "state_summary", "gold_types", "notes"])
        w.writerow(["", "", "", ""])  # stray blank row from the raw export
        w.writerow(["S01", "state", "timing_inefficiency", ""])
        w.writerow(["S02", "state", "none", ""])
        w.writerow(["S03", "state", "visa_timing", ""])  # raw pass missed budget_violation

    row = pn.row_rq4_gold_agreement(tmp_path, allow_missing=True)
    assert row["exact_set_agreement_rate"] == pytest.approx(1.0)  # self-checked file
    raw = row["raw_first_pass"]
    assert raw["exact_set_agreement_rate"] == pytest.approx(2 / 3)
    assert ("P3", ["budget_violation", "visa_timing"], ["visa_timing"]) in raw["disagreements"]
    assert raw["per_type"]["budget_violation"]["percent_agreement"] == pytest.approx(2 / 3)


def test_rq4_gold_agreement_missing_kalyan_file(tmp_path):
    (tmp_path / "rq4_probes.json").write_text(json.dumps({"types": [], "probes": []}))
    (tmp_path / "rq4_sheet_key.csv").write_text("sheet_id,probe_id\n")
    row = pn.row_rq4_gold_agreement(tmp_path, allow_missing=True)
    assert row["status"] == "missing_input"
    assert "rq4_labels_kalyan.csv" in row["missing"]


def test_rq4_gold_agreement_unknown_type_fails(tmp_path):
    _write_rq4_agreement_inputs(tmp_path, kalyan_rows=[
        ("S01", "timing_inefficiency"), ("S02", "none"), ("S03", "not_a_type"),
    ])
    with pytest.raises(SystemExit):
        pn.row_rq4_gold_agreement(tmp_path, allow_missing=True)


def test_rq4_gold_agreement_blank_row_fails(tmp_path):
    _write_rq4_agreement_inputs(tmp_path, kalyan_rows=[
        ("S01", "timing_inefficiency"), ("S02", "none"), ("S03", ""),
    ])
    with pytest.raises(SystemExit):
        pn.row_rq4_gold_agreement(tmp_path, allow_missing=True)


def test_json_deterministic(tmp_path):
    (tmp_path / "runs" / "synthetic").mkdir(parents=True)
    (tmp_path / "runs" / "control").mkdir(parents=True)
    (tmp_path / "runs" / "real").mkdir(parents=True)
    (tmp_path / "query_manifest.csv").write_text(
        "query_id,pair_id,set,gold_conflicts\n"
        "SYN-01,P01,synthetic,location_mismatch\n"
        "CTL-01,,control,none\n")
    (tmp_path / "runs" / "synthetic" / "sessions.jsonl").write_text(
        json.dumps(_session("SYN-01", "targeted", 1, final=0,
                            lifecycle=[_lifecycle_entry("a", "resolved")])) + "\n")
    (tmp_path / "runs" / "control" / "sessions.jsonl").write_text(
        json.dumps(_session("CTL-01", "targeted", 1, final=0)) + "\n")
    (tmp_path / "runs" / "real" / "sessions.jsonl").write_text(
        json.dumps(_session("SYN-01", "targeted", 1, final=0)) + "\n")

    a = pn.build_report(tmp_path, allow_missing=True)[0]
    b = pn.build_report(tmp_path, allow_missing=True)[0]
    assert json.dumps(a, indent=2, sort_keys=True) == json.dumps(b, indent=2, sort_keys=True)

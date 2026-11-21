"""Model manifest (§2) and H2c packet builder (§11)."""
import csv
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.build_h2c_packets import allocate, build, eligible, render_packet  # noqa: E402
from scripts.model_manifest import CANARIES, compare_snapshots, instability  # noqa: E402

# ── Model manifest ───────────────────────────────────────────────────────────

def _snap(label, created="2026-02-17T00:00:00Z", canary_hash="a"):
    return {
        "label": label,
        "canary_prompts": CANARIES,
        "models": {"claude-sonnet-4-6": {
            "entry": {"id": "claude-sonnet-4-6", "created_at": created, "display_name": "Sonnet 4.6"},
            "canaries": {name: {"sha256": [canary_hash]} for name in CANARIES},
        }},
    }


def test_identical_snapshots_compare_clean():
    assert compare_snapshots(_snap("start"), _snap("end")) == []


def test_created_at_change_is_reported():
    diffs = compare_snapshots(_snap("start"), _snap("end", created="2026-12-01T00:00:00Z"))
    assert any("created_at" in d for d in diffs)


def test_canary_drift_is_reported():
    diffs = compare_snapshots(_snap("start"), _snap("end", canary_hash="b"))
    assert len(diffs) == len(CANARIES)


def test_overlapping_hash_sets_are_not_drift():
    start, end = _snap("start"), _snap("end")
    start["models"]["claude-sonnet-4-6"]["canaries"]["json"]["sha256"] = ["a", "x"]
    end["models"]["claude-sonnet-4-6"]["canaries"]["json"]["sha256"] = ["x"]
    assert compare_snapshots(start, end) == []
    assert len(instability(start)) == 1 and instability(end) == []


def test_legacy_single_hash_snapshots_still_compare():
    start = _snap("start")
    start["models"]["claude-sonnet-4-6"]["canaries"]["json"]["sha256"] = "a"
    assert compare_snapshots(start, _snap("end")) == []


def test_changed_canary_prompts_invalidate_comparison():
    end = _snap("end")
    end["canary_prompts"] = {"other": "x"}
    assert any("invalid" in d for d in compare_snapshots(_snap("start"), end))


# ── H2c packets ──────────────────────────────────────────────────────────────

def _session(sid, cond="targeted", inv="mock", r1=2, final=0, **extra):
    return {"session_id": sid, "refinement_mode": cond, "inventory_mode": inv,
            "round_1_conflict_count": r1, "final_conflict_count": final, **extra}


def _plan(sid):
    return {
        "session_id": sid,
        "query": "Greece under $2000, beaches",
        "intent": {"destination": "Greece", "budget_usd": 2000, "interests": ["beaches"]},
        "weather": {"avg_temp_c": 29, "precipitation_mm": 8, "summary": "Hot and sunny."},
        "trip_options": [{
            "style": "balanced", "total_cost_usd": 1900,
            "flight": {"airline": "LH", "departure": "2027-06-15T06:00:00",
                       "arrival": "2027-06-15T13:00:00"},
            "hotel": {"name": "Plaka Stay", "location": "Plaka, Athens"},
            "experiences": [{"name": "Acropolis", "location": "Athens", "cost_usd": 30}],
            "day_by_day": [{"day": 1, "date": "Day 1", "afternoon": "Check in", "evening": "Dinner"}],
            "itinerary": {"breakdown": {"flight": 890, "hotel": 900}},
        }],
        "session_tags": {"condition": "targeted", "query_id": "SYN-01"},
    }


@pytest.fixture
def corpus(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    sessions = []
    for i in range(40):
        cond = ["targeted", "full_rerun", "broadcast", "two_way"][i % 4]
        inv = "mock" if i < 28 else "replay"
        sid = f"s{i:03d}"
        sessions.append(_session(sid, cond, inv, query_id=f"Q{i}", run_index=1))
        (plans / f"{sid}.json").write_text(json.dumps(_plan(sid)))
    sessions += [
        _session("base", cond="baseline"),
        _session("unresolved", final=1),
        _session("clean", r1=0),
        _session("noplan"),
    ]
    return sessions, plans, tmp_path


def test_eligibility_filters(corpus):
    sessions, plans, _ = corpus
    candidates, exclusions = eligible(sessions, plans)
    ids = {s["session_id"] for s in candidates}
    assert len(ids) == 40
    assert not ids & {"base", "unresolved", "clean", "noplan"}
    assert len(exclusions["baseline"]) == 1
    assert len(exclusions["unresolved_final"]) == 1
    assert len(exclusions["no_round1_conflict"]) == 1
    assert len(exclusions["no_plan_file"]) == 1


def test_no_balanced_option_is_excluded_and_counted(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    sessions = [_session("s000", query_id="SYN-01", run_index=1)]
    (plans / "s000.json").write_text(json.dumps(
        {**_plan("s000"), "trip_options": [{"style": "budget"}]}))
    candidates, exclusions = eligible(sessions, plans)
    assert candidates == []
    assert [s["session_id"] for s in exclusions["no_balanced_option"]] == ["s000"]


def test_allocation_is_proportional_and_covers_every_stratum():
    alloc = allocate({("mock", "a"): 70, ("mock", "b"): 20, ("real", "a"): 9, ("real", "b"): 1}, 30)
    assert sum(alloc.values()) == 30
    assert all(v >= 1 for v in alloc.values())
    assert alloc[("mock", "a")] > alloc[("mock", "b")] > alloc[("real", "b")]


def test_allocation_takes_everything_when_small():
    assert allocate({("a", "b"): 3, ("c", "d"): 2}, 30) == {("a", "b"): 3, ("c", "d"): 2}


def test_build_is_seeded_and_writes_blinded_packets(corpus):
    sessions, plans, tmp = corpus
    out_a, out_b = tmp / "a", tmp / "b"
    r = build(sessions, plans, out_a, seed=7, n=30, n_double=10)
    build(sessions, plans, out_b, seed=7, n=30, n_double=10)
    assert r == {"sampled": 30, "double": 10, "eligible": 40}
    key_a = (out_a / "KEY_DO_NOT_SHARE.csv").read_text()
    assert key_a == (out_b / "KEY_DO_NOT_SHARE.csv").read_text()   # same seed, same draw

    rater = sorted(p.name for p in (out_a / "rater").glob("plan_*.md"))
    assert len(rater) == 10
    with open(out_a / "rater" / "rater_scores.csv") as fh:
        assert [row["plan_id"] + ".md" for row in csv.DictReader(fh)] == rater

    uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    for packet in (out_a / "author").glob("plan_*.md"):
        text = packet.read_text().lower()
        for leak in ("targeted", "full_rerun", "broadcast", "two_way", "baseline",
                     "session", "s0", "round", "conflict", "fingerprint", "run_index"):
            assert leak not in text, (packet.name, leak)
        assert not uuid_re.search(text), packet.name


def test_same_seed_gives_same_sample(corpus):
    sessions, plans, tmp = corpus
    build(sessions, plans, tmp / "a", seed=20261117, n=20, n_double=6)
    build(sessions, plans, tmp / "b", seed=20261117, n=20, n_double=6)
    assert (tmp / "a" / "KEY_DO_NOT_SHARE.csv").read_text() == \
        (tmp / "b" / "KEY_DO_NOT_SHARE.csv").read_text()


def test_different_seed_draws_differently(corpus):
    sessions, plans, tmp = corpus
    build(sessions, plans, tmp / "a", seed=1, n=10, n_double=5)
    build(sessions, plans, tmp / "b", seed=2, n=10, n_double=5)
    assert (tmp / "a" / "KEY_DO_NOT_SHARE.csv").read_text() != (tmp / "b" / "KEY_DO_NOT_SHARE.csv").read_text()


def test_packet_shows_traveller_facts():
    text = render_packet(_plan("x"))
    for needle in ("Greece under $2000", "arrives 2027-06-15 13:00", "Plaka, Athens",
                   "Average high: 29°C", "Acropolis (Athens), $30", "Total: $1900"):
        assert needle in text


def test_save_plan_writes_only_when_dir_set(tmp_path, monkeypatch):
    from graph.travel_graph import _save_plan

    state = {"session_id": "abc", "trip_options": [{"style": "balanced"}], "agent_messages": ["x"]}
    monkeypatch.delenv("VOYAGER_PLANS_DIR", raising=False)
    _save_plan(state, "q", {})
    assert not list(tmp_path.iterdir())

    monkeypatch.setenv("VOYAGER_PLANS_DIR", str(tmp_path))
    _save_plan(state, "q", {"condition": "targeted"})
    saved = json.loads((tmp_path / "abc.json").read_text())
    assert saved["query"] == "q" and saved["trip_options"] == [{"style": "balanced"}]
    assert "agent_messages" not in saved


# ── Condition runs keep v1.4 results untouched ───────────────────────────────

def test_conditions_summary_goes_next_to_session_log(tmp_path, monkeypatch):
    from scripts import benchmark_queries as bq
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(tmp_path / "smoke" / "sessions.jsonl"))
    assert Path(bq._conditions_results_dir()) == (tmp_path / "smoke").resolve()


def test_conditions_summary_default_is_under_v15(monkeypatch):
    from scripts import benchmark_queries as bq
    monkeypatch.delenv("VOYAGER_METRICS_PATH", raising=False)
    assert Path(bq._conditions_results_dir()) == Path("results") / "v1.5" / "summary"


def test_write_results_honours_results_dir(tmp_path, monkeypatch):
    from scripts import benchmark_queries as bq
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(tmp_path / "sessions.jsonl"))
    monkeypatch.setenv("VOYAGER_RESULTS_DIR", str(tmp_path / "out"))
    (tmp_path / "sessions.jsonl").write_text("")
    bq._write_results("mock")
    assert (tmp_path / "out" / "run_summary.json").exists()

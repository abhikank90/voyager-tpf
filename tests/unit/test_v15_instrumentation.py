"""Climatology weather (§5.2), unresolved reasons (§7) and run logging (§2, §7)."""
import asyncio

import pytest

from agents.conflicts import CONFLICT_RESOLVER
from agents.unresolved import UNRESOLVED_REASONS, classify_unresolved, reason_counts

# ── Climatology ──────────────────────────────────────────────────────────────

def _daily(month: int, years=range(2016, 2026), tmax=30.0, precip=2.0, days=30):
    times, t, p = [], [], []
    for y in years:
        for d in range(1, days + 1):
            times.append(f"{y}-{month:02d}-{d:02d}")
            t.append(tmax)
            p.append(precip)
    # A different month that must be ignored.
    times.append(f"{years[0]}-01-01")
    t.append(-40.0)
    p.append(500.0)
    return times, t, p


def test_monthly_climatology_means():
    from agents.weather_agent import monthly_climatology

    out = monthly_climatology(*_daily(7, tmax=33.0, precip=2.0), "July")
    assert out["avg_temp_c"] == 33.0
    assert out["precipitation_mm"] == 60.0      # 30 days x 2 mm, every year
    assert out["summary"] == ""
    assert out["years"] == 10
    assert out["source"] == "open_meteo_era5_2016_2025"


def test_monthly_climatology_skips_short_years_and_missing_values():
    from agents.weather_agent import monthly_climatology

    times, t, p = _daily(3, years=range(2016, 2018))
    times += [f"2018-03-{d:02d}" for d in range(1, 11)]   # 10 days only: excluded
    t += [None] * 10
    p += [9.0] * 10
    out = monthly_climatology(times, t, p, "march")
    assert out["years"] == 2
    assert out["precipitation_mm"] == 60.0


def test_monthly_climatology_rejects_empty_month():
    from agents.weather_agent import monthly_climatology

    with pytest.raises(ValueError):
        monthly_climatology(*_daily(7), "January")


@pytest.fixture
def inv(monkeypatch, tmp_path):
    from config.settings import reload_settings
    monkeypatch.setenv("VOYAGER_INVENTORY_DIR", str(tmp_path))
    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", "mock")
    reload_settings()
    yield tmp_path
    reload_settings()


def test_capture_then_replay_climatology(inv, monkeypatch):
    from agents import inventory
    from agents.weather_agent import WeatherAgent
    from config.settings import reload_settings

    agent = WeatherAgent()

    async def fake_fetch(destination, month, api_key):
        return {"avg_temp_c": 34.0, "precipitation_mm": 10.0, "summary": "", "month": month}

    monkeypatch.setattr(agent, "_fetch_climatology", fake_fetch)
    monkeypatch.setattr(agent, "_inventory_mode", lambda: "capture")
    intent = {"destination": "Greece", "travel_month": "July"}
    captured = asyncio.run(agent._execute({"intent": intent}))
    assert captured["weather"]["avg_temp_c"] == 34.0
    assert inventory.load_manifest()["trip_window_rule"] == "month-v1.5"

    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", "replay")
    reload_settings()
    replayer = WeatherAgent()
    replayed = asyncio.run(replayer._execute({"intent": intent}))
    assert replayed["weather"] == captured["weather"]


def test_fetch_climatology_without_key_is_marked_fallback(inv):
    from agents.weather_agent import WeatherAgent

    out = asyncio.run(WeatherAgent()._fetch_climatology("Greece", "july", ""))
    assert out["source"] == "historical_average_fallback"


# ── Unresolved reasons ───────────────────────────────────────────────────────

def _conflict(ctype, fp):
    return {"type": ctype, "fingerprint": fp}


def _records(**by_fp):
    return {fp: {"status": st, "first_seen_round": fs} for fp, (st, fs) in by_fp.items()}


def test_baseline_conflicts_are_refinement_disabled():
    out = classify_unresolved([_conflict("timing_inefficiency", "a")], _records(a=("persisting", 1)),
                              {}, CONFLICT_RESOLVER, refinement_enabled=False)
    assert out[0]["reason"] == "refinement_disabled"


def test_reopened_and_introduced_late():
    out = classify_unresolved(
        [_conflict("location_mismatch", "a"), _conflict("location_mismatch", "b")],
        _records(a=("reopened", 1), b=("persisting", 2)),
        {"round_2_agents_rerun": ["hotel"]}, CONFLICT_RESOLVER, True,
    )
    assert [e["reason"] for e in out] == ["reopened", "introduced_late"]


def test_routing_miss_when_resolver_never_reran():
    out = classify_unresolved([_conflict("timing_inefficiency", "a")], _records(a=("persisting", 1)),
                              {"round_2_agents_rerun": ["hotel"]}, CONFLICT_RESOLVER, True)
    assert out[0]["reason"] == "routing_miss"
    assert out[0]["resolver"] == "flight"


def test_no_qualifying_option_uses_latest_feedback():
    metrics = {
        "round_2_agents_rerun": ["hotel"],
        "round_3_agents_rerun": ["hotel"],
        "feedback_by_round": {
            "2": {"hotel": {"fallback_to_original_selection": False}},
            "3": {"hotel": {"fallback_to_original_selection": True,
                            "reason": "no_qualifying_option"}},
        },
    }
    out = classify_unresolved([_conflict("location_mismatch", "a")], _records(a=("persisting", 1)),
                              metrics, CONFLICT_RESOLVER, True)
    assert out[0]["reason"] == "no_qualifying_option"


def test_round_limit_when_resolver_reran_and_satisfiable():
    metrics = {"round_2_agents_rerun": ["experience"]}
    out = classify_unresolved([_conflict("weather_activity_mismatch", "a")],
                              _records(a=("persisting", 1)), metrics, CONFLICT_RESOLVER, True)
    assert out[0]["reason"] == "round_limit"


def test_reason_counts_cover_every_reason():
    counts = reason_counts([{"reason": "round_limit"}, {"reason": "round_limit"}])
    assert set(counts) == set(UNRESOLVED_REASONS)
    assert counts["round_limit"] == 2


# ── Run logging ──────────────────────────────────────────────────────────────

def test_record_temperature_dedupes_per_component():
    from metrics import token_tracker as tt

    tt.start_session()
    tt.record_temperature("hub", 0.3)
    tt.record_temperature("hub", 0.3)
    tt.record_temperature("hub", 0.0)
    tt.record_temperature("x", None)
    assert tt.get_current().to_dict()["temperatures"] == {"hub": [0.0, 0.3]}


def test_agent_callbacks_carry_name_and_temperature():
    from agents.experience_agent import ExperienceAgent

    agent = ExperienceAgent()
    cb = agent.llm.callbacks[0]
    assert cb.component == "experience_agent"
    assert cb.temperature == agent.llm.temperature


def test_rounds_record_per_agent_durations_and_feedback(monkeypatch):
    from graph import travel_graph as tg

    for name, agent in tg._RUNNERS.items():
        async def fake(state, _n=name):
            if _n == "hotel":
                return {"feedback_metrics": {"fallback_to_original_selection": True}}
            return {}
        monkeypatch.setattr(agent, "run", fake)

    out = asyncio.run(tg.research_round_1({"run_metrics": {}}))
    rm = out["run_metrics"]
    assert set(rm["round_1_agent_durations_s"]) == set(tg._RUNNERS)
    assert rm["feedback_by_round"]["1"] == {"hotel": {"fallback_to_original_selection": True}}


def test_failing_agent_is_recorded_not_dropped(monkeypatch):
    from graph import travel_graph as tg

    async def boom(state):
        raise RuntimeError("fixture missing")

    async def ok(state):
        return {}

    for name, agent in tg._RUNNERS.items():
        monkeypatch.setattr(agent, "run", boom if name == "flight" else ok)
    out = asyncio.run(tg.research_round_1({"run_metrics": {}}))
    assert any("fixture missing" in v for v in out["errors"].values())


def test_timed_node_merges_metrics_and_adds_duration():
    from graph.travel_graph import _timed_node

    async def node(state):
        return {"run_metrics": {"b": 2}}

    out = asyncio.run(_timed_node("n", node)({"run_metrics": {"a": 1}}))
    assert out["run_metrics"]["a"] == 1 and out["run_metrics"]["b"] == 2
    assert "n" in out["run_metrics"]["node_durations_s"]


# ── Option generator uses the coordinated selections ─────────────────────────

def _option_agent():
    from unittest.mock import patch

    from agents.option_generator import OptionGeneratorAgent
    with patch("agents.option_generator.Anthropic"):
        return OptionGeneratorAgent()


@pytest.mark.parametrize("style", ["budget", "balanced", "premium"])
def test_every_option_uses_coordinated_flight_and_hotel(style):
    agent = _option_agent()
    late = {"airline": "UA", "price_usd": 680, "arrival": "2027-03-15T22:30:00"}
    early = {"airline": "LH", "price_usd": 890, "arrival": "2027-03-15T13:00:00"}
    beach = {"name": "Beach", "location": "Beachfront", "total_usd": 1100, "rating": 4.8}
    near = {"name": "Plaka Stay", "location": "Plaka", "total_usd": 1300, "rating": 4.0}
    assert agent._select_flight([late, early], style, early) is early
    assert agent._select_hotel([beach, near], style, near) is near


def test_price_helpers_read_agent_fields():
    from agents.option_generator import (
        _experience_cost,
        _flight_price,
        _hotel_per_night,
        _hotel_total,
    )
    assert _flight_price({"price_usd": 680}) == 680
    assert _hotel_total({"total_usd": 1105}, 13) == 1105
    assert _hotel_total({"price_per_night_usd": 85}, 13) == 85 * 13
    assert _hotel_per_night({"price_per_night_usd": 85}) == 85
    assert _experience_cost({"cost_usd": 40}) == 40
    assert _experience_cost({"price": 12}) == 12  # legacy hand-built states


def test_itinerary_prompt_facts():
    agent = _option_agent()
    assert "22:30" in agent._format_arrival_for_prompt({"airline": "UA", "arrival": "2027-03-15T22:30:00"})
    text = agent._format_weather_for_prompt({"avg_temp_c": 33.8, "precipitation_mm": 10.2, "summary": ""})
    assert "33.8" in text and "10.2" in text

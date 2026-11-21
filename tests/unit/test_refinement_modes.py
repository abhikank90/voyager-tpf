"""Refinement conditions (protocol §3) and parallel-safety helpers."""
import asyncio
import json

import pytest

from agents.conflicts import CONFLICT_RESOLVER
from agents.refinement_modes import (
    CRITIQUE_MESSAGE_TYPE,
    REFINEMENT_MODES,
    render_critique,
    round_3_agents,
    transform_messages,
    validate_mode,
)

LOC = {"type": "location_mismatch", "agents": ["hotel", "experience"]}
TIME = {"type": "timing_inefficiency", "agents": ["flight"]}
WEATHER = {"type": "weather_activity_mismatch", "agents": ["weather", "experience"]}

TYPED = [
    {"from_agent": "collaboration_hub", "to_agent": "hotel", "message_type": "constraint",
     "content": "Top experiences are located far from selected hotels.",
     "data": {"activity_locations": ["Plaka, Athens"]}, "round": 1},
    {"from_agent": "collaboration_hub", "to_agent": "flight", "message_type": "insight",
     "content": "Flight arrives late (20:00+), wasting Day 1.",
     "data": {"preferred_arrival": "before 14:00"}, "round": 1},
]
STATE = {"selected_hotel": {"location": "Beachfront"}}


# ── Message transforms ───────────────────────────────────────────────────────

def test_targeted_leaves_messages_unchanged():
    assert transform_messages("targeted", TYPED, [LOC, TIME], STATE, 1) == TYPED


def test_full_rerun_keeps_typed_data_and_addresses_everyone():
    out = transform_messages("full_rerun", TYPED, [LOC, TIME], STATE, 1)
    assert [m["to_agent"] for m in out] == ["all", "all"]
    assert [m["data"] for m in out] == [m["data"] for m in TYPED]


def test_broadcast_is_one_prose_message_with_no_data():
    out = transform_messages("broadcast", TYPED, [LOC, TIME], STATE, 1)
    assert len(out) == 1
    msg = out[0]
    assert msg["to_agent"] == "all"
    assert msg["message_type"] == CRITIQUE_MESSAGE_TYPE
    assert msg["data"] == {}
    assert "far from selected hotels" in msg["content"]
    assert "arrives late" in msg["content"]


def test_broadcast_with_no_messages_sends_nothing():
    assert transform_messages("broadcast", [], [], STATE, 1) == []


def test_two_way_adds_experience_message_for_location_conflict():
    out = transform_messages("two_way", TYPED, [LOC, TIME], STATE, 1)
    assert out[:2] == TYPED
    extra = out[2]
    assert extra["to_agent"] == "experience"
    assert extra["data"] == {"near_location": "Beachfront"}


def test_two_way_without_location_conflict_matches_targeted():
    assert transform_messages("two_way", TYPED[1:], [TIME], STATE, 1) == TYPED[1:]


def test_render_critique_deduplicates():
    msgs = [{"content": "A."}, {"content": "A."}, {"content": "B."}, {"content": ""}]
    assert render_critique(msgs) == "A. B."


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        validate_mode("debate")
    assert set(REFINEMENT_MODES) == {"targeted", "full_rerun", "broadcast", "two_way"}


# ── Round 3 re-run sets ──────────────────────────────────────────────────────

@pytest.mark.parametrize("mode, conflicts, expected", [
    ("targeted", [LOC, TIME], {"hotel", "flight"}),
    ("targeted", [WEATHER], {"experience"}),
    ("two_way", [LOC], {"hotel", "experience"}),
    ("two_way", [TIME], {"flight"}),
    ("full_rerun", [TIME], {"flight", "hotel", "experience", "weather", "visa_safety"}),
    ("broadcast", [LOC], {"flight", "hotel", "experience", "weather", "visa_safety"}),
    ("broadcast", [], set()),
])
def test_round_3_agents(mode, conflicts, expected):
    assert round_3_agents(mode, conflicts, CONFLICT_RESOLVER) == expected


# ── Round 2 re-run sets through the graph node ───────────────────────────────

@pytest.mark.parametrize("mode, expected", [
    ("targeted", ["flight", "hotel"]),
    ("two_way", ["experience", "flight", "hotel"]),
    ("full_rerun", ["experience", "flight", "hotel", "visa_safety", "weather"]),
    ("broadcast", ["experience", "flight", "hotel", "visa_safety", "weather"]),
])
def test_round_2_reruns_follow_mode(monkeypatch, mode, expected):
    from graph import travel_graph as tg

    called: list[str] = []
    for name, agent in [("flight", tg._flight), ("hotel", tg._hotel), ("experience", tg._experience),
                        ("weather", tg._weather), ("visa_safety", tg._visa_safety)]:
        async def fake(state, _n=name):
            called.append(_n)
            return {}
        monkeypatch.setattr(agent, "run", fake)

    messages = transform_messages(mode, TYPED, [LOC, TIME], STATE, 1)
    result = asyncio.run(tg.research_round_2({"agent_messages": messages, "refinement_mode": mode}))
    assert sorted(called) == expected
    assert result["run_metrics"]["round_2_agents_rerun"] == expected


# ── Agents reading the new message types ─────────────────────────────────────

@pytest.fixture
def mock_mode(monkeypatch, tmp_path):
    from config.settings import reload_settings
    monkeypatch.setenv("VOYAGER_INVENTORY_DIR", str(tmp_path))
    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", "mock")
    reload_settings()
    yield tmp_path
    reload_settings()


def _capture_prompt_inputs(monkeypatch, agent):
    seen: dict = {}

    class FakeChain:
        async def ainvoke(self, inputs):
            seen.update(inputs)

            class R:
                content = "[]"
            return R()

    class FakePrompt:
        def __or__(self, other):
            return FakeChain()

    monkeypatch.setattr(agent, "prompt", FakePrompt())
    return seen


@pytest.mark.parametrize("message, needle", [
    ({"to_agent": "all", "message_type": CRITIQUE_MESSAGE_TYPE, "content": "Hotel is far.",
      "data": {}, "round": 1}, "Coordinator feedback on the whole plan: Hotel is far."),
    ({"to_agent": "experience", "message_type": "constraint", "content": "",
      "data": {"near_location": "Plaka"}, "round": 1}, "near Plaka"),
])
def test_experience_prompt_carries_critique_and_two_way_hint(mock_mode, monkeypatch, message, needle):
    from agents.experience_agent import ExperienceAgent

    agent = ExperienceAgent()
    seen = _capture_prompt_inputs(monkeypatch, agent)
    state = {"intent": {"destination": "Greece", "interests": ["beaches"]},
             "collaboration_round": 2, "agent_messages": [message]}
    asyncio.run(agent._execute(state))
    assert needle in seen["location_focus"]


def _critique_state(**extra):
    return {
        "collaboration_round": 2,
        "agent_messages": [{"to_agent": "all", "message_type": CRITIQUE_MESSAGE_TYPE,
                            "content": "Flight arrives late. Hotel is far from activities.",
                            "data": {}, "round": 1}],
        **extra,
    }


def test_flight_applies_interpreted_arrival_hour(mock_mode, monkeypatch):
    from agents.flight_agent import FlightAgent, _arrival_hour

    agent = FlightAgent()

    async def fake_interpret(state, task, plan):
        return {"arrive_before_hour": 14}

    monkeypatch.setattr(agent, "_interpret_critique", fake_interpret)
    state = _critique_state(intent={"destination": "Greece", "origin": "New York",
                                    "budget_usd": 3000, "travel_month": "June", "group_size": 1})
    result = asyncio.run(agent._execute(state))
    assert _arrival_hour(result["selected_flight"]["arrival"]) < 14


def test_flight_ignores_bad_interpretation(mock_mode, monkeypatch):
    from agents.flight_agent import FlightAgent, _arrival_hour

    agent = FlightAgent()

    async def fake_interpret(state, task, plan):
        return {"arrive_before_hour": "early"}

    monkeypatch.setattr(agent, "_interpret_critique", fake_interpret)
    state = _critique_state(intent={"destination": "Greece", "origin": "New York",
                                    "budget_usd": 3000, "travel_month": "June", "group_size": 1})
    result = asyncio.run(agent._execute(state))
    assert _arrival_hour(result["selected_flight"]["arrival"]) >= 20


def test_hotel_applies_interpreted_target_area(mock_mode, monkeypatch):
    from agents.hotel_agent import HotelAgent

    agent = HotelAgent()
    seen_plan: dict = {}

    async def fake_interpret(state, task, plan):
        seen_plan.update(plan)
        return {"target_area": "Plaka, Athens"}

    monkeypatch.setattr(agent, "_interpret_critique", fake_interpret)
    state = _critique_state(
        intent={"destination": "Greece", "budget_usd": 3000, "travel_month": "June", "group_size": 1},
        experiences=[{"location": "Plaka, Athens"}, {"location": "Oia, Santorini"}],
        selected_hotel={"location": "Beachfront"},
    )
    result = asyncio.run(agent._execute(state))
    assert result["selected_hotel"]["location"] == "Plaka, Athens"
    assert seen_plan["top_activity_locations"] == ["Plaka, Athens", "Oia, Santorini"]


def test_typed_constraint_skips_interpretation(mock_mode, monkeypatch):
    from agents.hotel_agent import HotelAgent

    agent = HotelAgent()

    async def boom(*a, **k):
        raise AssertionError("must not interpret when a typed constraint is present")

    monkeypatch.setattr(agent, "_interpret_critique", boom)
    state = {"collaboration_round": 2, "agent_messages": [TYPED[0]],
             "intent": {"destination": "Greece", "budget_usd": 3000, "travel_month": "June"}}
    asyncio.run(agent._execute(state))


# ── _interpret_critique itself ───────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, text):
        self.text, self.calls = text, 0

    async def ainvoke(self, prompt):
        self.calls += 1

        class R:
            content = self.text
        return R()


def test_interpret_critique_empty_inbox_makes_no_call(mock_mode):
    from agents.flight_agent import FlightAgent

    agent = FlightAgent()
    agent.llm = _FakeLLM('{"arrive_before_hour": 14}')
    assert asyncio.run(agent._interpret_critique({"collaboration_round": 2}, "t", {})) == {}
    assert agent.llm.calls == 0


def test_interpret_critique_parses_json_with_surrounding_text(mock_mode):
    from agents.flight_agent import FlightAgent

    agent = FlightAgent()
    agent.llm = _FakeLLM('Sure: {"arrive_before_hour": 13} done')
    out = asyncio.run(agent._interpret_critique(_critique_state(), "t", {}))
    assert out == {"arrive_before_hour": 13}


def test_interpret_critique_recorded_and_replayed(mock_mode, monkeypatch):
    from agents.flight_agent import FlightAgent
    from config.settings import reload_settings

    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", "replay")
    monkeypatch.setenv("VOYAGER_REPLAY_RECORD_MISSING", "1")
    reload_settings()
    agent = FlightAgent()
    agent.llm = _FakeLLM('{"arrive_before_hour": 12}')
    first = asyncio.run(agent._interpret_critique(_critique_state(), "t", {"a": 1}))
    second = asyncio.run(agent._interpret_critique(_critique_state(), "t", {"a": 1}))
    assert first == second == {"arrive_before_hour": 12}
    assert agent.llm.calls == 1  # second answer came from the recorded fixture


# ── Parallel safety ──────────────────────────────────────────────────────────

def _capture_many(args):
    import os

    from agents import inventory
    from config.settings import reload_settings

    tmp, worker = args
    os.environ["VOYAGER_INVENTORY_DIR"] = tmp
    reload_settings()
    for i in range(15):
        qid = inventory.inventory_query_id("serpapi", worker=worker, i=i)
        inventory.capture("serpapi", {"flights": [], "w": worker, "i": i}, qid, run_label="t")


def test_concurrent_captures_keep_every_manifest_entry(tmp_path):
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    with ctx.Pool(4) as pool:
        pool.map(_capture_many, [(str(tmp_path), w) for w in range(4)])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["fixtures"]) == 60


def test_metrics_path_override(monkeypatch, tmp_path):
    from metrics import collector

    target = tmp_path / "run_a.jsonl"
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(target))
    assert collector._sessions_file() == target
    monkeypatch.delenv("VOYAGER_METRICS_PATH")
    assert collector._sessions_file() == collector._SESSIONS_FILE

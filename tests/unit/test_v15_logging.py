"""Round-1 message logging, per-round feedback and itinerary fallbacks (§7, §11)."""
import asyncio
import json
from unittest.mock import MagicMock, patch

from agents.conflicts import CONFLICT_ROUTE
from metrics.collector import load_sessions, record_session


def _hub_msg(to, mtype, data):
    return {"from_agent": "collaboration_hub", "to_agent": to, "message_type": mtype,
            "round": 1, "data": data}


def _record(tmp_path, monkeypatch, final_state):
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(tmp_path / "s.jsonl"))
    record_session(final_state, "q", 1.0, mode="full", refinement_mode="broadcast")
    return load_sessions()[-1]


def test_round_1_messages_come_from_the_snapshot(tmp_path, monkeypatch):
    hub = [_hub_msg(*CONFLICT_ROUTE["location_mismatch"], {"activity_locations": ["Plaka"]}),
           _hub_msg(*CONFLICT_ROUTE["timing_inefficiency"], {"preferred_arrival": "before 18:00"})]
    delivered = [_hub_msg("all", "critique", {})]
    state = {
        "agent_messages": [],  # the last hub sent nothing: everything resolved
        "conflicts": [],
        "run_metrics": {
            "round_1_conflicts": [{"type": "location_mismatch"}, {"type": "timing_inefficiency"}],
            "round_1_hub_messages": hub,
            "round_1_messages": delivered,
            "feedback_by_round": {"2": {"hotel": {"reason": "qualifying_option_found"}}},
        },
    }
    rec = _record(tmp_path, monkeypatch, state)
    assert rec["round_1_messages_sent"] == 1
    assert rec["round_1_message_recipients"] == ["all"]
    assert rec["round_1_agents_targeted"] == []
    evidence = {e["type"]: e["data"] for e in rec["round_1_conflicts_evidence"]}
    assert evidence["location_mismatch"] == {"activity_locations": ["Plaka"]}
    assert rec["feedback_by_round"] == {"2": {"hotel": {"reason": "qualifying_option_found"}}}


def test_pre_v15_states_still_use_agent_messages(tmp_path, monkeypatch):
    msgs = [_hub_msg(*CONFLICT_ROUTE["location_mismatch"], {"x": 1})]
    state = {"agent_messages": msgs, "conflicts": [],
             "run_metrics": {"round_1_conflicts": [{"type": "location_mismatch"}]}}
    rec = _record(tmp_path, monkeypatch, state)
    assert rec["round_1_messages_sent"] == 1
    assert rec["round_1_agents_targeted"] == ["hotel"]


def test_fallback_styles_are_logged(tmp_path, monkeypatch):
    state = {"agent_messages": [], "conflicts": [], "run_metrics": {},
             "trip_options": [{"style": "budget", "itinerary_fallback": "max_tokens"},
                              {"style": "balanced", "itinerary_fallback": None}]}
    assert _record(tmp_path, monkeypatch, state)["itinerary_fallback_styles"] == ["budget"]


def _option_agent(text, stop_reason):
    from agents.option_generator import OptionGeneratorAgent
    with patch("agents.option_generator.Anthropic"):
        agent = OptionGeneratorAgent()
    reply = MagicMock()
    reply.content = [MagicMock(text=text)]
    reply.stop_reason = stop_reason
    agent.client = MagicMock()
    agent.client.messages.create.return_value = reply
    return agent


def _state():
    flight = {"airline": "LH", "price_usd": 900, "arrival": "2027-03-15T13:00:00"}
    hotel = {"name": "Plaka Stay", "location": "Plaka", "price_per_night_usd": 100}
    return {"intent": {"destination": "Athens", "budget_usd": 6000, "duration_days": 3},
            "flights": [flight], "hotels": [hotel], "selected_flight": flight,
            "selected_hotel": hotel, "experiences": [], "weather": {}, "visa_safety": {}}


def test_truncated_itinerary_is_marked_fallback():
    agent = _option_agent('[{"day": 1, "morning": "Acrop', "max_tokens")
    out = asyncio.run(agent._execute(_state()))
    for option in out["trip_options"]:
        assert option["itinerary_fallback"] == "max_tokens"
        assert all(d["fallback_reason"] == "max_tokens" for d in option["day_by_day"])


def test_generated_itinerary_is_not_fallback_and_cap_is_raised():
    from agents.option_generator import ITINERARY_MAX_TOKENS
    agent = _option_agent(json.dumps([{"day": 1, "morning": "Acropolis"}]), "end_turn")
    out = asyncio.run(agent._execute(_state()))
    assert all(o["itinerary_fallback"] is None for o in out["trip_options"])
    assert agent.client.messages.create.call_args.kwargs["max_tokens"] == ITINERARY_MAX_TOKENS >= 8000


def test_hub_1_snapshots_typed_and_delivered_messages():
    from graph import travel_graph as tg
    hub_msgs = [_hub_msg(*CONFLICT_ROUTE["location_mismatch"], {"activity_locations": ["Plaka"]})]
    conflicts = [{"type": "location_mismatch", "agents": ["experience", "hotel"]}]

    async def fake_run(state):
        return {"agent_messages": list(hub_msgs), "conflicts": conflicts}

    with patch.object(tg._collaboration_hub, "run", fake_run):
        out = asyncio.run(tg.collaboration_hub_1_node(
            {"refinement_mode": "broadcast", "run_metrics": {}, "intent": {}}
        ))
    rm = out["run_metrics"]
    assert rm["round_1_hub_messages"] == hub_msgs
    assert [m["to_agent"] for m in rm["round_1_messages"]] == ["all"]

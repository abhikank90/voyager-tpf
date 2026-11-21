"""Trip-window rule (protocol §5.2) and replay keying (protocol §4)."""
from datetime import date

import pytest

from agents.trip_window import (
    RULE_LEGACY,
    RULE_MONTH,
    TripWindow,
    assert_after_anchor,
    legacy_trip_window,
    parse_month,
    resolve_trip_window,
)

ANCHOR = date(2026, 12, 1)


# ── Month-to-year resolution ─────────────────────────────────────────────────

@pytest.mark.parametrize("month, expected", [
    ("March", date(2027, 3, 15)),       # earlier month -> next year
    ("November", date(2027, 11, 15)),   # month before anchor month -> next year
    ("December", date(2027, 12, 15)),   # same month as anchor -> next year
])
def test_month_resolves_to_next_occurrence_after_anchor_month(month, expected):
    window = resolve_trip_window(month, 13, ANCHOR)
    assert window.departure == expected
    assert window.rule == RULE_MONTH
    assert window.source == "parsed_month"


def test_later_month_in_same_year():
    window = resolve_trip_window("December", 10, date(2026, 11, 20))
    assert window.departure == date(2026, 12, 15)


def test_return_is_departure_plus_duration():
    window = resolve_trip_window("June", 7, ANCHOR)
    assert (window.return_date - window.departure).days == 7


@pytest.mark.parametrize("duration", [None, 0, -3, "x"])
def test_missing_or_bad_duration_defaults_to_13(duration):
    window = resolve_trip_window("June", duration, ANCHOR)
    assert (window.return_date - window.departure).days == 13


def test_any_month_falls_back_to_anchor_plus_90():
    window = resolve_trip_window(None, 5, ANCHOR)
    assert window.departure == date(2027, 3, 1)
    assert window.month == "march"
    assert window.source == "any_month_fallback"


def test_two_parsed_months_give_two_windows():
    a = resolve_trip_window("March", 13, ANCHOR)
    b = resolve_trip_window("April", 13, ANCHOR)
    assert a.departure != b.departure and a.month != b.month


@pytest.mark.parametrize("raw, num", [("march", 3), ("Mar", 3), (" JULY ", 7), ("", None),
                                      (None, None), ("summer", None)])
def test_parse_month(raw, num):
    assert parse_month(raw) == num


def test_parser_year_is_ignored():
    """The rule never reads travel_year; only the month and the anchor matter."""
    window = resolve_trip_window("March", 13, ANCHOR)
    assert window.departure.year == 2027


# ── Departure-after-anchor check ─────────────────────────────────────────────

def test_assert_after_anchor_rejects_past_departure():
    bad = TripWindow(date(2026, 11, 15), date(2026, 11, 28), "november",
                     RULE_MONTH, ANCHOR, "parsed_month")
    with pytest.raises(ValueError, match="not after anchor"):
        assert_after_anchor(bad)


# ── Legacy rule (replay of pre-v1.5 fixture sets) ────────────────────────────

def test_legacy_mock_is_july_of_parsed_year():
    window = legacy_trip_window("mock", 2027, 13, ANCHOR, "March")
    assert (window.departure, window.return_date) == (date(2027, 7, 1), date(2027, 7, 14))
    assert window.rule == RULE_LEGACY


def test_legacy_replay_is_anchor_plus_90():
    anchor = date(2026, 9, 4)
    window = legacy_trip_window("replay", 2026, 13, anchor, None)
    assert window.departure == date(2026, 12, 3)
    assert window.month == "july"


# ── Agent wiring ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_settings(monkeypatch, tmp_path):
    from config.settings import reload_settings
    monkeypatch.setenv("VOYAGER_INVENTORY_DIR", str(tmp_path))
    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", "mock")
    monkeypatch.setenv("VOYAGER_MOCK_ANCHOR_DATE", "2026-12-01")
    reload_settings()
    yield tmp_path
    reload_settings()


def test_mock_mode_uses_month_rule_and_fixed_anchor(mock_settings):
    from agents.flight_agent import FlightAgent
    window = FlightAgent()._trip_window({"travel_month": "March", "travel_year": 2026})
    assert window.departure == date(2027, 3, 15)
    assert window.anchor == ANCHOR


def test_replay_of_unmarked_manifest_uses_legacy_rule(mock_settings, monkeypatch):
    import json

    from agents.flight_agent import FlightAgent
    (mock_settings / "manifest.json").write_text(json.dumps(
        {"replay_anchor_date": "2026-09-04", "fixtures": {}}))
    agent = FlightAgent()
    monkeypatch.setattr(agent, "_inventory_mode", lambda: "replay")
    window = agent._trip_window({"travel_month": "March"})
    assert window.rule == RULE_LEGACY
    assert window.departure == date(2026, 12, 3)


def test_replay_of_marked_manifest_uses_month_rule(mock_settings, monkeypatch):
    import json

    from agents.flight_agent import FlightAgent
    (mock_settings / "manifest.json").write_text(json.dumps(
        {"replay_anchor_date": "2026-11-20", "trip_window_rule": RULE_MONTH,
         "fixtures": {}}))
    agent = FlightAgent()
    monkeypatch.setattr(agent, "_inventory_mode", lambda: "replay")
    window = agent._trip_window({"travel_month": "March"})
    assert window.rule == RULE_MONTH
    assert window.departure == date(2027, 3, 15)


def test_capture_writes_anchor_and_rule_once(mock_settings, monkeypatch):
    from agents import inventory
    from agents.hotel_agent import HotelAgent
    agent = HotelAgent()
    monkeypatch.setattr(agent, "_inventory_mode", lambda: "capture")
    first = agent._trip_window({"travel_month": "June"})
    manifest = inventory.load_manifest()
    assert manifest["trip_window_rule"] == RULE_MONTH
    assert manifest["replay_anchor_date"] == first.anchor.isoformat()
    assert agent._trip_window({"travel_month": "June"}).anchor == first.anchor


def test_capture_refuses_pre_v15_fixture_set(mock_settings, monkeypatch):
    import json

    from agents.flight_agent import FlightAgent
    (mock_settings / "manifest.json").write_text(json.dumps(
        {"replay_anchor_date": "2026-09-04", "fixtures": {"x_serpapi": {}}}))
    agent = FlightAgent()
    monkeypatch.setattr(agent, "_inventory_mode", lambda: "capture")
    with pytest.raises(RuntimeError, match="fresh VOYAGER_INVENTORY_DIR"):
        agent._trip_window({"travel_month": "June"})


def test_weather_month_follows_trip_window(mock_settings):
    import asyncio

    from agents.weather_agent import WeatherAgent
    result = asyncio.run(WeatherAgent()._execute(
        {"intent": {"destination": "Greece", "travel_month": None}}))
    # "any month" -> anchor + 90 = March 2027, so weather is March, not July.
    assert result["travel_month"] == "march"


# ── Replay keying (protocol §4) ──────────────────────────────────────────────

def _agent():
    from agents.experience_agent import ExperienceAgent
    return ExperienceAgent()


def test_inbox_digest_empty_in_round_1():
    assert _agent()._inbox_digest({"collaboration_round": 1, "agent_messages": []}) == ""


def test_two_different_inboxes_give_two_different_digests():
    agent = _agent()
    base = {"collaboration_round": 2}
    a = {**base, "agent_messages": [{"to_agent": "all", "round": 1, "content": "Geography is off."}]}
    b = {**base, "agent_messages": [{"to_agent": "all", "round": 1, "content": "Budget is tight."}]}
    assert agent._inbox_digest(a) and agent._inbox_digest(b)
    assert agent._inbox_digest(a) != agent._inbox_digest(b)


def test_inbox_digest_ignores_message_order():
    agent = _agent()
    m1 = {"to_agent": "all", "round": 1, "content": "one"}
    m2 = {"to_agent": "all", "round": 1, "content": "two"}
    s1 = {"collaboration_round": 2, "agent_messages": [m1, m2]}
    s2 = {"collaboration_round": 2, "agent_messages": [m2, m1]}
    assert agent._inbox_digest(s1) == agent._inbox_digest(s2)


def test_messages_for_other_agents_do_not_change_digest():
    agent = _agent()
    mine = {"to_agent": "all", "round": 1, "content": "x"}
    other = {"to_agent": "hotel", "round": 1, "content": "y"}
    s1 = {"collaboration_round": 2, "agent_messages": [mine]}
    s2 = {"collaboration_round": 2, "agent_messages": [mine, other]}
    assert agent._inbox_digest(s1) == agent._inbox_digest(s2)


def test_experience_fixture_key_changes_with_inbox(mock_settings, monkeypatch):
    """A re-run with feedback must not be served the Round-1 fixture."""
    import asyncio

    from agents import inventory

    keys: list[str] = []

    async def fake_replay_or_record(source, query_id, produce, *, run_label):
        keys.append(query_id)
        return {"experiences": []}

    monkeypatch.setattr(inventory, "replay_or_record", fake_replay_or_record)
    agent = _agent()
    monkeypatch.setattr(agent, "_inventory_mode", lambda: "replay")
    intent = {"destination": "Greece", "interests": ["beaches"], "budget_usd": 2000,
              "duration_days": 7}
    round1 = {"intent": intent, "collaboration_round": 1, "agent_messages": []}
    round2 = {"intent": intent, "collaboration_round": 2, "agent_messages": [
        {"to_agent": "all", "round": 1, "content": "Geography is off."}]}
    asyncio.run(agent._execute(round1))
    asyncio.run(agent._execute(round2))
    assert keys[0] != keys[1]


def test_round_1_experience_key_matches_v141_formula(mock_settings, monkeypatch):
    """Round-1 keys are unchanged, so existing fixtures stay valid."""
    import asyncio

    from agents import inventory

    keys: list[str] = []

    async def fake_replay_or_record(source, query_id, produce, *, run_label):
        keys.append(query_id)
        return {"experiences": []}

    monkeypatch.setattr(inventory, "replay_or_record", fake_replay_or_record)
    agent = _agent()
    monkeypatch.setattr(agent, "_inventory_mode", lambda: "replay")
    intent = {"destination": "Greece", "interests": ["beaches"], "budget_usd": 2000,
              "duration_days": 7}
    asyncio.run(agent._execute({"intent": intent, "collaboration_round": 1}))
    expected = inventory.inventory_query_id(
        "experiences", destination="Greece", interests=["beaches"], duration_days=7,
        activity_budget=200, location_focus="",
    )
    assert keys == [expected]

"""Capture guard (§8): stamp the anchor first, and fail sessions with agent errors."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents import inventory
from agents.trip_window import RULE_MONTH
from config.settings import reload_settings
from scripts import benchmark_queries as bq


@pytest.fixture(autouse=True)
def _restore_settings():
    yield
    reload_settings()


# ── Fix A: anchor stamped once, before any session ────────────────────────────

def test_stamp_capture_anchor_in_capture_mode():
    with patch("agents.inventory.ensure_capture_anchor") as anchor:
        bq._stamp_capture_anchor("capture")
        anchor.assert_called_once_with(RULE_MONTH)


def test_stamp_capture_anchor_skipped_in_mock():
    with patch("agents.inventory.ensure_capture_anchor") as anchor:
        bq._stamp_capture_anchor("mock")
        anchor.assert_not_called()


def test_anchor_first_then_intent_fixture_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("VOYAGER_INVENTORY_DIR", str(tmp_path))
    reload_settings()
    inventory.ensure_capture_anchor(RULE_MONTH)
    inventory.capture("intent", {"destination": "Greece"}, "q1_intent", run_label="t")
    manifest = inventory.load_manifest()
    assert manifest["trip_window_rule"] == RULE_MONTH
    assert "replay_anchor_date" in manifest


def test_intent_fixture_before_anchor_raises(tmp_path, monkeypatch):
    """The original bug: a fixture captured before the anchor stamps makes the guard fire."""
    monkeypatch.setenv("VOYAGER_INVENTORY_DIR", str(tmp_path))
    reload_settings()
    inventory.capture("intent", {"destination": "Greece"}, "q1_intent", run_label="t")
    with pytest.raises(RuntimeError, match="before trip_window_rule"):
        inventory.ensure_capture_anchor(RULE_MONTH)


# ── Fix B: agent errors are session failures outside mock ─────────────────────

def _run_state(monkeypatch, inventory_mode, errors, tmp_path):
    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", inventory_mode)
    monkeypatch.setenv("VOYAGER_METRICS_PATH", str(tmp_path / "sessions.jsonl"))
    reload_settings()
    from graph.travel_graph import run_collaborative_travel_query

    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"errors": errors})
    with patch("graph.travel_graph.collaborative_travel_graph", graph):
        return asyncio.run(run_collaborative_travel_query("q", record_metrics=True))


def test_capture_session_with_agent_errors_raises(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="agent errors"):
        _run_state(monkeypatch, "capture", {"flight_agent": "boom"}, tmp_path)


def test_replay_session_with_agent_errors_raises(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="agent errors"):
        _run_state(monkeypatch, "replay", {"option_generator": "boom"}, tmp_path)


def test_mock_session_with_agent_errors_does_not_raise(tmp_path, monkeypatch):
    out = _run_state(monkeypatch, "mock", {"flight_agent": "boom"}, tmp_path)
    assert out == {"errors": {"flight_agent": "boom"}}


# ── errors accumulate across nodes, so research errors reach the check ────────

def test_merge_errors_accumulates():
    from graph.state import _merge_errors

    assert _merge_errors({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert _merge_errors(None, {"b": 2}) == {"b": 2}
    assert _merge_errors({"a": 1}, {"a": 9}) == {"a": 9}


def test_errors_accumulate_across_graph_nodes():
    import asyncio as aio

    from langgraph.graph import END, StateGraph

    from graph.state import TravelState

    g = StateGraph(TravelState)

    async def research(state):
        return {"errors": {"flight_agent": "boom"}}

    async def options(state):
        return {"errors": {"option_generator": "boom2"}}

    g.add_node("research", research)
    g.add_node("options", options)
    g.set_entry_point("research")
    g.add_edge("research", "options")
    g.add_edge("options", END)
    out = aio.run(g.compile().ainvoke({"errors": {}}))
    assert out["errors"] == {"flight_agent": "boom", "option_generator": "boom2"}


@pytest.mark.asyncio
async def test_research_round_records_research_agent_error(monkeypatch):
    from graph import travel_graph

    async def err(state):
        return {"errors": {"flight_agent": "boom"}}

    async def ok(state):
        return {}

    for name in ("flight",):
        monkeypatch.setattr(getattr(travel_graph, f"_{name}"), "run", err)
    for name in ("hotel", "experience", "weather", "visa_safety"):
        monkeypatch.setattr(getattr(travel_graph, f"_{name}"), "run", ok)

    merged = await travel_graph.research_round_1({"run_metrics": {}})
    assert merged["errors"] == {"flight_agent": "boom"}


def test_legacy_v141_intent_keys_unchanged():
    """The 12 recorded v1.4.1 intent fixture keys still reproduce from their queries."""
    import csv

    from agents import inventory
    from scripts.benchmark_queries import QUERIES

    recorded = {
        r["query_id"]: r["v1_4_1_intent_fixture"]
        for r in csv.DictReader(open("results/v1.5/query_manifest.csv"))
        if r["set"] == "real" and r["in_v1_4_1_capture"] == "yes"
    }
    assert len(recorded) == 12
    for i, query in enumerate(QUERIES[:12], 1):
        key = inventory.inventory_query_id("intent", query=query)
        assert key == recorded[f"REAL-{i:02d}"], f"REAL-{i:02d}: intent key changed"

"""Machine-readable output of eval_hybrid_detection --out (no live LLM calls)."""
import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def probe_file(tmp_path):
    """A single-probe doc so the eval loop runs once, twice-repeated, no network."""
    doc = {
        "version": 1,
        "types": [
            "location_mismatch", "timing_inefficiency", "weather_activity_mismatch",
            "checkin_impossible", "budget_violation", "visa_timing", "preference_mismatch",
        ],
        "probes": [{
            "id": "T-01",
            "name": "clean",
            "group": "test",
            "construction": {
                "location_mismatch": "silent",
                "timing_inefficiency": "silent",
                "weather_activity_mismatch": "silent",
            },
            "state": {
                "intent": {"destination": "Greece", "budget_usd": 2000,
                           "interests": ["beaches"], "travel_month": "July 2026"},
                "collaboration_round": 1,
                "selected_hotel": {"name": "Santorini Resort", "location": "Santorini",
                                   "total_usd": 700, "lat": 36.39, "lon": 25.46},
                "experiences": [
                    {"name": "Red Beach", "category": "beach", "location": "Santorini",
                     "lat": 36.36, "lon": 25.4},
                ],
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


def test_out_writes_machine_readable_json(monkeypatch, tmp_path, probe_file):
    import scripts.eval_hybrid_detection as ev
    from agents.collaboration_hub import CollaborationHubAgent

    # No LLM: the isolation client returns one fixed candidate on every call.
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=json.dumps([
        {"type": "checkin_impossible", "agents": ["flight"], "description": "late desk"},
    ]))]
    fake_client.messages.create.return_value = fake_response

    rules_hub = CollaborationHubAgent.__new__(CollaborationHubAgent)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ci-test-key")
    monkeypatch.setattr(ev, "Anthropic", lambda **kwargs: fake_client)
    monkeypatch.setattr(ev, "build_rules_hub", lambda: rules_hub)

    out = tmp_path / "report.json"
    ev.main(["--probes", str(probe_file), "--out", str(out)])

    report = json.loads(out.read_text())
    assert report["variant"] == "isolation"
    assert report["model"] == "claude-sonnet-4-6"
    assert report["temperature"] == 0.0
    assert len(report["results"]) == 1

    result = report["results"][0]
    assert result["probe"] == "T-01"
    assert result["rules"] == []  # clean state: no deterministic conflicts
    assert len(result["llm_runs"]) == 2
    first = result["llm_runs"][0]["candidates"][0]
    assert first["type"] == "checkin_impossible"
    assert first["agents"] == ["flight"]
    # checkin_impossible has no deterministic validator → unverified
    assert result["validated"] == []
    assert any(u["reason"] for u in result["unverified"])


def test_main_loads_dotenv_before_key_check(monkeypatch, tmp_path, probe_file):
    """main() reads the key from .env — it must not need it on a command line."""
    import scripts.eval_hybrid_detection as ev
    from agents.collaboration_hub import CollaborationHubAgent

    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=dotenv-test-key\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="[]")]
    fake_client.messages.create.return_value = fake_response
    monkeypatch.setattr(ev, "Anthropic", lambda **kw: fake_client)
    monkeypatch.setattr(
        ev, "build_rules_hub",
        lambda: CollaborationHubAgent.__new__(CollaborationHubAgent),
    )

    out = tmp_path / "report.json"
    ev.main(["--probes", str(probe_file), "--out", str(out)])
    assert out.exists()


def test_out_writes_shipped_variant(monkeypatch, tmp_path, probe_file):
    """--variant shipped drives the hub's own candidate path (union of 3, validated)."""
    import scripts.eval_hybrid_detection as ev

    hub = ev.build_rules_hub()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=json.dumps({"candidates": [
        {"conflict_type": "checkin_impossible", "agents": ["flight"],
         "hypothesis": "late arrival after desk close", "evidence": ["arrival 23:40"],
         "suggested_rule": None, "confidence": 0.9},
    ]}))]
    fake_response.usage = MagicMock(input_tokens=1, output_tokens=1)
    hub.client.messages.create.return_value = fake_response

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ci-test-key")
    monkeypatch.setattr(ev, "Anthropic", lambda **kwargs: MagicMock())
    monkeypatch.setattr(ev, "build_rules_hub", lambda: hub)

    out = tmp_path / "shipped.json"
    ev.main(["--probes", str(probe_file), "--variant", "shipped", "--out", str(out)])

    report = json.loads(out.read_text())
    assert report["variant"] == "shipped"
    assert report["temperature"] == 0.0
    result = report["results"][0]
    assert result["probe"] == "T-01"
    assert result["rules"] == []  # clean probe: rules silent
    assert result["candidates"][0]["conflict_type"] == "checkin_impossible"
    assert result["validated"] == []
    assert any(u["reason"] for u in result["unverified"])
    # the union of 3 identical replies deduplicates to one candidate
    assert report["summary"]["candidates"] == 1

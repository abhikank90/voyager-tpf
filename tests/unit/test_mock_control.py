"""Control mock inventory (Task 6): satisfiable, conflict-free data for CTL-01..10."""
import asyncio

import pytest

from agents.mock_control import (
    control_profile,
    mock_control_flights,
    mock_control_hotels,
    normalize_destination,
)

# (destination, budget_usd, group_size, nights) parsed from the manifest text of
# results/v1.5/query_manifest.csv — explicit expected values, never an LLM.
CTL_ROWS = [
    ("Lisbon", 2500, 2, 5),
    ("Kyoto", 3500, 2, 6),
    ("Vancouver", 1800, 1, 4),
    ("Amsterdam", 2200, 1, 5),
    ("Cancun", 3000, 2, 6),
    ("Sydney", 4500, 2, 8),
    ("Edinburgh", 2000, 1, 5),
    ("Banff", 2800, 2, 6),
    ("San Diego", 3500, 4, 5),
    ("Montreal", 1500, 1, 4),
]

# Option generator's food/misc for the budget option (agents/option_generator.py).
FOOD_PER_NIGHT = 25
MISC = 100


def _arrival_hour(iso: str) -> int:
    return int(iso.split("T")[1].split(":")[0])


def test_destination_normalization_is_accent_and_case_tolerant():
    assert normalize_destination("  Montréal , Canada") == "montreal"
    assert normalize_destination("Cancún") == "cancun"
    assert normalize_destination("LISBON") == "lisbon"
    assert normalize_destination("San Diego") == "san diego"


def test_unknown_destination_raises():
    with pytest.raises(ValueError, match="no control profile"):
        control_profile("Atlantis")


@pytest.mark.parametrize("destination,budget,group,nights", CTL_ROWS)
def test_control_profile_is_satisfiable_and_conflict_free(destination, budget, group, nights):
    profile = control_profile(destination)
    flights = mock_control_flights("JFK", destination, "2027-06-15", "2027-06-20", group)["flights"]
    hotels = mock_control_hotels(destination, "2027-06-15", "2027-06-21", group, nights)["hotels"]

    cheapest = min(flights, key=lambda f: f["price_usd"])
    # The timing rule fires at arrival >= 20:00; controls land 10:00-15:00.
    assert 10 <= _arrival_hour(cheapest["arrival"]) <= 15, (
        f"{destination}: cheapest flight lands {cheapest['arrival']}"
    )
    # The affordable (first) hotel is in the city, so experience locations that
    # name the city substring-match and the location rule cannot fire.
    assert hotels[0]["location"] == profile["city"]
    assert hotels[0]["total_usd"] == hotels[0]["price_per_night_usd"] * nights

    # Budget option: flight + hotel + food + misc fit with >= 10% margin.
    total = cheapest["price_usd"] + hotels[0]["total_usd"] + FOOD_PER_NIGHT * nights + MISC
    assert total <= budget * 0.9, f"{destination}: {total} exceeds 90% of {budget}"

    # BudgetGuardrailAgent passes iff flight + hotel <= 0.70 x budget. The selected
    # hotel is the first affordable one (total_usd <= 0.45 x budget). Require the
    # guardrail sum to pass with >= 10% margin too.
    affordable = [h for h in hotels if h["total_usd"] <= 0.45 * budget]
    assert affordable, f"{destination}: no hotel within 45% of budget"
    guardrail_sum = cheapest["price_usd"] + affordable[0]["total_usd"]
    assert guardrail_sum <= 0.9 * 0.70 * budget, (
        f"{destination}: guardrail {guardrail_sum} exceeds 90% of 70% of {budget}"
    )


def test_benchmark_control_set_requires_mock(monkeypatch):
    from config.settings import reload_settings
    from scripts import benchmark_queries

    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", "replay")
    reload_settings()
    monkeypatch.setattr(
        "sys.argv",
        ["benchmark_queries.py", "--mode", "conditions",
         "--manifest", "results/v1.5/query_manifest.csv", "--set", "control"],
    )
    with pytest.raises(SystemExit, match="requires mock inventory mode"):
        benchmark_queries.main()


def test_control_profile_in_capture_mode_raises(monkeypatch, tmp_path):
    from agents.flight_agent import FlightAgent
    from config.settings import reload_settings

    monkeypatch.setenv("VOYAGER_INVENTORY_MODE", "capture")
    monkeypatch.setenv("VOYAGER_INVENTORY_DIR", str(tmp_path))
    reload_settings()

    agent = FlightAgent()
    state = {
        "intent": {"destination": "Lisbon", "travel_month": "May 2027", "travel_year": 2027,
                   "budget_usd": 2500, "group_size": 2, "duration_days": 5},
        "inventory_profile": "control",
    }
    with pytest.raises(ValueError, match="only valid in mock"):
        asyncio.run(agent._execute(state))


def test_default_profile_is_unchanged():
    """Without inventory_profile the planted-conflict mock data still applies."""
    from agents.flight_agent import _mock_flight_data

    default = _mock_flight_data("JFK", "Greece", "2027-06-15", "2027-06-20", 1)["flights"]
    assert _arrival_hour(default[0]["arrival"]) >= 20

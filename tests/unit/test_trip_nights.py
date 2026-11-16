"""One stay length across the window, hotel pricing, options and itinerary."""
import asyncio
from datetime import date
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agents.trip_window import (
    DEFAULT_DURATION_DAYS,
    RULE_MONTH,
    TripWindow,
    record_nights,
)


def _window(nights=DEFAULT_DURATION_DAYS):
    dep = date(2027, 3, 15)
    return TripWindow(
        departure=dep,
        return_date=date.fromordinal(dep.toordinal() + nights),
        month="march",
        rule=RULE_MONTH,
        anchor=date(2026, 12, 1),
        source="mock",
    )


def test_record_carries_nights():
    rec = _window().as_record()
    assert rec["nights"] == DEFAULT_DURATION_DAYS
    assert record_nights(rec) == DEFAULT_DURATION_DAYS


def test_record_nights_from_dates_and_missing():
    assert record_nights({"departure": "2027-03-15", "return": "2027-03-22"}) == 7
    assert record_nights({}) is None
    assert record_nights(None) is None
    assert record_nights({"departure": "bad", "return": "2027-03-22"}) is None


def _itinerary_agent(captured):
    from agents.itinerary_builder import ItineraryBuilderAgent
    with patch("agents.itinerary_builder.ChatAnthropic"):
        agent = ItineraryBuilderAgent()

    def fake_llm(prompt_value):
        captured.append(prompt_value.to_string())
        return AIMessage(content='{"days": []}')

    agent.llm = RunnableLambda(fake_llm)
    return agent


def test_itinerary_uses_trip_window_dates():
    captured = []
    agent = _itinerary_agent(captured)
    state = {
        "intent": {"destination": "Greece", "duration_days": None},
        "trip_window": _window().as_record(),
    }
    asyncio.run(agent._execute(state))
    assert "2027-03-15 to 2027-03-28" in captured[0]
    assert "July" not in captured[0]


def test_option_costs_use_window_nights():
    import json
    from unittest.mock import MagicMock

    from agents.option_generator import OptionGeneratorAgent
    with patch("agents.option_generator.Anthropic"):
        agent = OptionGeneratorAgent()
    client = MagicMock()
    reply = MagicMock()
    reply.content = [MagicMock(text=json.dumps([{"day": 1}]))]
    client.messages.create.return_value = reply
    agent.client = client

    flight = {"airline": "LH", "price_usd": 900, "arrival": "2027-03-15T13:00:00"}
    hotel = {"name": "Plaka Stay", "location": "Plaka", "price_per_night_usd": 100}
    state = {
        "intent": {"destination": "Athens", "budget_usd": 6000, "duration_days": None},
        "flights": [flight], "hotels": [hotel],
        "selected_flight": flight, "selected_hotel": hotel,
        "experiences": [], "weather": {}, "visa_safety": {},
        "trip_window": _window().as_record(),
    }
    out = asyncio.run(agent._execute(state))
    for option in out["trip_options"]:
        assert option["itinerary"]["num_days"] == DEFAULT_DURATION_DAYS
        assert option["itinerary"]["breakdown"]["hotel"] == 100 * DEFAULT_DURATION_DAYS

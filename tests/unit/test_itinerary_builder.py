"""ItineraryBuilderAgent handles requests that name no trip length or year."""
import asyncio
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda


def test_itinerary_handles_missing_duration_and_year():
    from agents.itinerary_builder import ItineraryBuilderAgent
    with patch("agents.itinerary_builder.ChatAnthropic"):
        agent = ItineraryBuilderAgent()
    captured = []

    def fake_llm(prompt_value):
        captured.append(prompt_value.to_string())
        return AIMessage(content='{"days": []}')

    agent.llm = RunnableLambda(fake_llm)
    state = {"intent": {"destination": "Greece", "duration_days": None, "travel_year": None}}
    out = asyncio.run(agent._execute(state))
    assert out["status"] == "complete"
    assert "July 1 - July 15, 2026" in captured[0]

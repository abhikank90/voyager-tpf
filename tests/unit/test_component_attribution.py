"""Every LLM-calling agent must attribute its tokens to a component (Task 10 finding).

The bug: `agents/intent_parser.py` wraps its ChatAnthropic in
``.with_structured_output()``, which hides the token callback from
``BaseAgent._label_token_callbacks``, so the parser's usage never reached
``per_component_usage``. The fix labels the callback at construction; these tests
guard that no agent's callback is left unlabelled.
"""
from unittest.mock import patch

import pytest

LLM_AGENTS = [
    ("agents.intent_parser", "IntentParserAgent", "intent_parser"),
    ("agents.flight_agent", "FlightAgent", "flight_agent"),
    ("agents.hotel_agent", "HotelAgent", "hotel_agent"),
    ("agents.experience_agent", "ExperienceAgent", "experience_agent"),
    ("agents.visa_safety_agent", "VisaSafetyAgent", "visa_safety_agent"),
    ("agents.itinerary_builder", "ItineraryBuilderAgent", "itinerary_builder"),
]


class _NoCallbacks:
    """Mimics the LangChain structured-output wrapper, which hides ``.callbacks``.

    Must be callable so ``self.prompt | self.llm`` (``coerce_to_runnable``) accepts it
    when the agent builds its chain during construction.
    """

    def __call__(self, *args, **kwargs):
        return self


def _fake_chat_anthropic(captured: list):
    class FakeChatAnthropic:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.callbacks = kwargs.get("callbacks") or []
            self.temperature = kwargs.get("temperature")

        def with_structured_output(self, *args, **kwargs):
            return _NoCallbacks()

    return FakeChatAnthropic


@pytest.mark.parametrize("module,classname,expected", LLM_AGENTS)
def test_llm_agent_callback_is_labelled(module, classname, expected):
    import importlib

    mod = importlib.import_module(module)
    cls = getattr(mod, classname)
    captured: list = []
    with patch(f"{module}.ChatAnthropic", _fake_chat_anthropic(captured)):
        cls()
    assert captured, f"{classname} built no LLM"
    callbacks = captured[0].get("callbacks") or []
    assert callbacks, f"{classname} built no TokenTrackingCallback"
    assert callbacks[0].component == expected, (
        f"{classname}: callback component {callbacks[0].component!r} != {expected!r}"
    )


def test_intent_parser_callback_temperature_is_set():
    import importlib

    mod = importlib.import_module("agents.intent_parser")
    captured: list = []
    with patch("agents.intent_parser.ChatAnthropic", _fake_chat_anthropic(captured)):
        mod.IntentParserAgent()
    callbacks = captured[0]["callbacks"]
    assert callbacks[0].temperature is not None

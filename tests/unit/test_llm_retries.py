"""§8 retry policy: every LLM construction site takes max_retries from one setting."""
import importlib
from unittest.mock import patch

import pytest

from config.settings import get_settings, reload_settings

SITES = [
    # (module, agent class, construction symbol, attribute holding the client)
    ("agents.flight_agent", "FlightAgent", "ChatAnthropic", "llm"),
    ("agents.hotel_agent", "HotelAgent", "ChatAnthropic", "llm"),
    ("agents.visa_safety_agent", "VisaSafetyAgent", "ChatAnthropic", "llm"),
    ("agents.experience_agent", "ExperienceAgent", "ChatAnthropic", "llm"),
    ("agents.intent_parser", "IntentParserAgent", "ChatAnthropic", "llm"),
    ("agents.itinerary_builder", "ItineraryBuilderAgent", "ChatAnthropic", "llm"),
    ("agents.collaboration_hub", "CollaborationHubAgent", "Anthropic", "client"),
    ("agents.option_generator", "OptionGeneratorAgent", "Anthropic", "client"),
]


class _FakeChat:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.kwargs = kwargs

    def with_structured_output(self, schema):
        self._structured_schema = schema
        return self

    def __call__(self, *args, **kwargs):
        return self


class _FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def _reset_retries():
    reload_settings()
    get_settings().llm_max_retries = 3


def test_default_llm_max_retries_is_three():
    reload_settings()
    assert get_settings().llm_max_retries == 3


@pytest.mark.parametrize("module_path,class_name,symbol,attr", SITES)
def test_every_llm_construction_site_uses_the_setting(module_path, class_name, symbol, attr):
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    captured = {}

    if symbol == "ChatAnthropic":
        def factory(**kwargs):
            fake = _FakeChat(**kwargs)
            captured["obj"] = fake
            captured["kwargs"] = kwargs
            return fake
    else:
        def factory(**kwargs):
            fake = _FakeClient(**kwargs)
            captured["obj"] = fake
            captured["kwargs"] = kwargs
            return fake

    with patch(f"{module_path}.{symbol}", factory):
        agent = cls()

    assert getattr(agent, attr) is captured["obj"]
    assert captured["kwargs"]["max_retries"] == get_settings().llm_max_retries


def test_setting_change_flows_through_to_constructions():
    get_settings().llm_max_retries = 7
    captured = {}

    def factory(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeChat(**kwargs)

    with patch("agents.flight_agent.ChatAnthropic", factory):
        import agents.flight_agent as fa
        fa.FlightAgent()
    assert captured["kwargs"]["max_retries"] == 7


def test_model_manifest_client_uses_the_setting():
    captured = {}

    def factory(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeClient(**kwargs)

    with patch("scripts.model_manifest.Anthropic", factory):
        from scripts import model_manifest
        model_manifest.make_client()
    assert captured["kwargs"]["max_retries"] == get_settings().llm_max_retries


def test_eval_hybrid_detection_client_uses_the_setting():
    captured = {}

    def factory(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeClient(**kwargs)

    with patch("scripts.eval_hybrid_detection.Anthropic", factory):
        from scripts import eval_hybrid_detection
        eval_hybrid_detection.make_client()
    assert captured["kwargs"]["max_retries"] == get_settings().llm_max_retries

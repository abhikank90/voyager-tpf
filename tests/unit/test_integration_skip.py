"""The integration tests must never bill by accident.

They require both ``VOYAGER_LIVE_TESTS=1`` and an ``ANTHROPIC_API_KEY``. The
``pytestmark`` condition in ``tests/integration/test_graph.py`` is evaluated at
import time, so these tests import the module under a controlled environment and
assert the resulting skip condition directly (no live calls are made).
"""
import importlib

import tests.integration.test_graph as test_graph


def test_integration_skips_without_live_flag(monkeypatch):
    monkeypatch.delenv("VOYAGER_LIVE_TESTS", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    importlib.reload(test_graph)

    condition = test_graph.pytestmark.args[0]
    assert bool(condition) is True


def test_integration_skips_without_key(monkeypatch):
    monkeypatch.setenv("VOYAGER_LIVE_TESTS", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    importlib.reload(test_graph)

    condition = test_graph.pytestmark.args[0]
    assert bool(condition) is True


def test_integration_runs_with_flag_and_key(monkeypatch):
    monkeypatch.setenv("VOYAGER_LIVE_TESTS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    importlib.reload(test_graph)

    condition = test_graph.pytestmark.args[0]
    assert bool(condition) is False

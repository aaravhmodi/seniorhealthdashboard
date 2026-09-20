"""Tests run deterministically, with the model switched off.

A real OPENAI_API_KEY in .env would otherwise make every check-in hit the
network: slow, costly, and flaky in a way that would have us debugging the
suite at 4am instead of the product. The LLM path is covered by faking the
transport in test_llm.py, and there is a live smoke test you run on purpose:

    pytest -m live --live
"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def deterministic_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    monkeypatch.setenv("MOCK_MODE", "true")
    # A fixed signing key, so tests exercise the real signed-link path rather
    # than the unconfigured fallback. Deterministic, and obviously not a secret.
    monkeypatch.setenv("CAREGIVER_LINK_SECRET", "test-link-secret")
    # A developer may have loaded the real warehouse under backend/data.
    # Tests must never change behavior based on that local, mutable state.
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "empty.duckdb"))
    get_settings.cache_clear()
    from app.datasets.lookup import clear_cache
    clear_cache()
    yield
    get_settings.cache_clear()
    clear_cache()


@pytest.fixture(autouse=True)
def clean_vector_index():
    """Each test starts with an empty index; tests that need one build it."""
    from app import retrieval

    retrieval.store.reset()
    yield
    retrieval.store.reset()


def pytest_addoption(parser):
    parser.addoption(
        "--live", action="store_true", default=False,
        help="run tests that call the real OpenAI API (costs money)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits the real OpenAI API")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (real API calls)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)

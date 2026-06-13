"""Tests for LLM configuration defaults."""

from __future__ import annotations

import importlib

import src.config as config


class FakeChatOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_get_llm_prefers_xai(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    monkeypatch.setenv("XAI_MODEL", "grok-3")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    mod = importlib.reload(config)
    llm = mod.get_llm()

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs["api_key"] == "xai-test"
    assert llm.kwargs["base_url"] == "https://api.x.ai/v1"
    assert llm.kwargs["model"] == "grok-3"


def test_get_llm_uses_openai_fallback_when_xai_missing(monkeypatch):
    # Ensure any .env sample value is overridden so config sees no XAI key
    monkeypatch.setenv("XAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    mod = importlib.reload(config)
    llm = mod.get_llm()

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs["api_key"] == "openai-test"
    assert llm.kwargs["model"] == "gpt-4o-mini"

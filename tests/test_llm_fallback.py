"""Tests for the Gemini -> Groq provider chain.

No network: the chain is exercised with stub runnables so failover behaviour is
verified deterministically rather than by waiting for a real rate limit.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda

from app.agent import llm as llm_module
from app.agent.llm import (
    NoProviderConfiguredError,
    build_chat_model,
    describe_chain,
    is_transient,
)


class _Boom(Exception):
    """Stands in for a provider SDK error."""


class RateLimitError(Exception):
    """Named to match a real provider exception class."""


@pytest.fixture
def two_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.settings.__class__, "provider_order",
                        property(lambda _self: ["google", "groq"]))


@pytest.fixture
def one_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.settings.__class__, "provider_order",
                        property(lambda _self: ["google"]))


@pytest.fixture
def no_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.settings.__class__, "provider_order",
                        property(lambda _self: []))


# --------------------------------------------------------------------------
# Which failures deserve a failover
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ConnectionError("connection reset"),
        RateLimitError("429 Too Many Requests"),
        Exception("Resource exhausted: quota exceeded"),
        Exception("503 Service Unavailable"),
        Exception("The model is overloaded"),
    ],
)
def test_transient_failures_are_recognised(error: Exception) -> None:
    assert is_transient(error)


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid schema field"),
        KeyError("missing key"),
        Exception("400 Bad Request: malformed function declaration"),
    ],
)
def test_our_own_bugs_are_not_treated_as_transient(error: Exception) -> None:
    """Failing over on a malformed request hides the bug and doubles the latency."""
    assert not is_transient(error)


# --------------------------------------------------------------------------
# Chain construction
# --------------------------------------------------------------------------


def test_no_configured_provider_is_an_actionable_error(no_providers: None) -> None:
    with pytest.raises(NoProviderConfiguredError, match="GOOGLE_API_KEY"):
        build_chat_model()


def test_single_provider_builds_without_a_fallback_wrapper(
    one_provider: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(llm_module._BUILDERS, "google", lambda: RunnableLambda(lambda x: "primary"))
    chain = build_chat_model()
    assert chain.invoke("q") == "primary"
    assert not hasattr(chain, "fallbacks")


def test_second_provider_serves_the_call_when_the_first_fails(
    two_providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(_: Any) -> str:
        raise RateLimitError("429 rate limit exceeded")

    monkeypatch.setitem(llm_module._BUILDERS, "google", lambda: RunnableLambda(failing))
    monkeypatch.setitem(
        llm_module._BUILDERS, "groq", lambda: RunnableLambda(lambda _: "served by groq")
    )

    chain = build_chat_model()
    assert chain.invoke("question") == "served by groq"


def test_primary_is_preferred_when_it_works(
    two_providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        llm_module._BUILDERS, "google", lambda: RunnableLambda(lambda _: "served by gemini")
    )
    monkeypatch.setitem(
        llm_module._BUILDERS, "groq", lambda: RunnableLambda(lambda _: "served by groq")
    )
    assert build_chat_model().invoke("question") == "served by gemini"


def test_failure_of_every_provider_still_raises(
    two_providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failover is not a way to swallow an outage."""

    def failing(_: Any) -> str:
        raise _Boom("provider down")

    monkeypatch.setitem(llm_module._BUILDERS, "google", lambda: RunnableLambda(failing))
    monkeypatch.setitem(llm_module._BUILDERS, "groq", lambda: RunnableLambda(failing))
    with pytest.raises(_Boom):
        build_chat_model().invoke("question")


def test_tools_are_bound_to_every_provider_not_the_wrapper(
    two_providers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binding after with_fallbacks would leave the fallback unable to call tools."""
    bound: list[str] = []

    class _Model(RunnableLambda):
        def __init__(self, label: str) -> None:
            super().__init__(lambda _: label)
            self.label = label

        def bind_tools(self, tools: list[Any]) -> _Model:
            bound.append(self.label)
            return self

    monkeypatch.setitem(llm_module._BUILDERS, "google", lambda: _Model("gemini"))
    monkeypatch.setitem(llm_module._BUILDERS, "groq", lambda: _Model("groq"))

    build_chat_model(tools=[object()])  # type: ignore[list-item]
    assert bound == ["gemini", "groq"]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_describe_chain_names_both_providers(two_providers: None) -> None:
    described = describe_chain()
    assert "google:" in described
    assert "groq:" in described
    assert "->" in described


def test_describe_chain_is_honest_when_nothing_is_configured(no_providers: None) -> None:
    assert describe_chain() == "none configured"

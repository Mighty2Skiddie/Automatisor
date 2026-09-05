"""LLM provider chain with transparent failover.

Gemini 2.5 Flash is primary. Its free tier is rate-limited per minute, and the
``/compare`` view issues three agent runs at once — the single most quota-exposed
request in the system, and the opening beat of the demo. So a second provider stands
behind it: when Gemini is throttled or erroring, the same call is retried against
Groq without the caller knowing.

Two decisions worth defending:

**Failover is at the model layer, not the graph layer.** ``Runnable.with_fallbacks``
wraps the chat model itself, so failover happens inside the tool-calling loop and a
mid-conversation rate limit does not discard the turns already completed. Retrying
the whole graph would re-run every MCP call and could produce a different answer.

**Only transient failures fail over.** A 429, a 5xx or a timeout means "ask someone
else". A malformed request or a schema-validation error means our own code is wrong,
and silently retrying it against a second provider would hide the bug and double the
latency. ``TRANSIENT_ERRORS`` draws that line explicitly.

The provider that actually served a request is recorded on the response, because
"which model wrote this?" is not answerable after the fact otherwise.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from app.config import settings

logger = logging.getLogger(__name__)


class NoProviderConfiguredError(RuntimeError):
    """Raised when no LLM provider has a usable API key."""


# Exception *names* rather than imported classes: the provider SDKs raise their own
# error types, and importing groq's exception hierarchy would make Groq a hard
# dependency of a Gemini-only deployment. Matching on the class name keeps the
# fallback decision honest without coupling the two.
TRANSIENT_ERROR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "RateLimitError",
        "ResourceExhausted",
        "TooManyRequests",
        "ServiceUnavailable",
        "InternalServerError",
        "DeadlineExceeded",
        "APIConnectionError",
        "APITimeoutError",
        "APIStatusError",
        "ConnectError",
        "ReadTimeout",
        "TimeoutError",
        # aiohttp, used by the Google client. Observed in practice: an intermittent
        # resolver failure surfaces as ClientConnectorDNSError mid-run.
        "ClientConnectorDNSError",
        "ClientConnectorError",
        "ClientConnectionError",
        "ClientOSError",
        "ServerDisconnectedError",
        "ServerTimeoutError",
    }
)


class TransientProviderError(Exception):
    """Marker type used to tell ``with_fallbacks`` that failover is appropriate."""


def is_transient(error: BaseException) -> bool:
    """Whether an error means "try another provider" rather than "this call is wrong".

    Must never raise. Some SDK exceptions build their message lazily from connection
    state and throw from ``__str__`` when that state is absent — an aiohttp
    ``ClientConnectorDNSError`` does exactly this. A transient-check that explodes
    would take down the recovery path it exists to serve.
    """
    if isinstance(error, TimeoutError | ConnectionError):
        return True
    if type(error).__name__ in TRANSIENT_ERROR_NAMES:
        return True
    try:
        text = str(error).lower()
    except Exception:  # noqa: BLE001 - an unreadable message is not a verdict
        return False
    return any(
        marker in text
        for marker in ("rate limit", "429", "quota", "resource exhausted", "503", "overloaded")
    )


def build_google_model() -> BaseChatModel:
    """Primary provider."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    if settings.google_api_key is None:
        raise NoProviderConfiguredError("GOOGLE_API_KEY is not set")
    return ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.google_api_key.get_secret_value(),
        temperature=settings.llm_temperature,
        # Retries inside the provider handle a single blip; with_fallbacks handles a
        # provider that is genuinely unavailable. Both layers are wanted.
        max_retries=settings.llm_max_retries,
        timeout=60,
    )


def build_groq_model() -> BaseChatModel:
    """Fallback provider."""
    from langchain_groq import ChatGroq

    if settings.groq_api_key is None:
        raise NoProviderConfiguredError("GROQ_API_KEY is not set")
    return ChatGroq(
        model=settings.groq_model,
        groq_api_key=settings.groq_api_key.get_secret_value(),
        temperature=settings.llm_temperature,
        max_retries=settings.llm_max_retries,
        request_timeout=60,
    )


_BUILDERS: Final[dict[str, Any]] = {
    "google": build_google_model,
    "groq": build_groq_model,
}

# How each provider must be asked for structured output. Verified empirically against
# the live models, not assumed: Groq's default selection raises "Tool choice is
# required" on openai/gpt-oss-120b, while json_schema succeeds. None means "use the
# integration's default", which is correct for Gemini.
STRUCTURED_OUTPUT_METHOD: Final[dict[str, str | None]] = {
    "google": None,
    "groq": "json_schema",
}


def active_providers() -> list[str]:
    """Providers that are configured, in the order they will be tried."""
    return settings.provider_order


def build_chat_model(tools: list[BaseTool] | None = None) -> Runnable:
    """Build the chat model, with any configured fallbacks chained behind it.

    Tools are bound to *each* provider before the chain is assembled: binding after
    ``with_fallbacks`` would attach tools to the wrapper rather than to the models,
    and the fallback would silently lose its ability to call them.
    """
    order = active_providers()
    if not order:
        raise NoProviderConfiguredError(
            "No LLM provider configured. Set GOOGLE_API_KEY (and optionally "
            "GROQ_API_KEY) in .env."
        )

    models: list[Runnable] = []
    for name in order:
        model: Runnable = _BUILDERS[name]()
        if tools:
            model = model.bind_tools(tools)
        models.append(model)

    primary, *fallbacks = models
    if not fallbacks:
        return primary

    logger.info(
        "llm_chain_built", extra={"primary": order[0], "fallbacks": order[1:]}
    )
    return primary.with_fallbacks(
        fallbacks,
        # Anything the provider SDKs raise is a candidate; is_transient() is applied
        # by the caller for logging, but with_fallbacks needs concrete types. Broad
        # here is correct: by the time a call raises, the alternative to failing over
        # is failing the request outright.
        exceptions_to_handle=(Exception,),
    )


def build_structured_model(schema: type) -> Runnable:
    """Build a structured-output chain, with fallbacks, one provider at a time.

    Structured output must be configured *per provider*, not on the fallback wrapper,
    because the two providers do not agree on how to do it. Groq's default method
    selection fails on ``openai/gpt-oss-120b`` with "Tool choice is required", while
    ``json_schema`` works; Gemini is correct on its default. Wrapping the whole chain
    once would apply one provider's strategy to both, and the fallback would fail the
    moment it was actually needed — the worst possible time to discover it.
    """
    order = active_providers()
    if not order:
        raise NoProviderConfiguredError(
            "No LLM provider configured. Set GOOGLE_API_KEY (and optionally "
            "GROQ_API_KEY) in .env."
        )

    models: list[Runnable] = []
    for name in order:
        model = _BUILDERS[name]()
        method = STRUCTURED_OUTPUT_METHOD.get(name)
        models.append(
            model.with_structured_output(schema, method=method)
            if method
            else model.with_structured_output(schema)
        )

    primary, *fallbacks = models
    if not fallbacks:
        return primary
    return primary.with_fallbacks(fallbacks, exceptions_to_handle=(Exception,))


def describe_chain() -> str:
    """Human-readable provider chain, for /healthz and startup logs."""
    order = active_providers()
    if not order:
        return "none configured"
    labels = {
        "google": f"google:{settings.llm_model}",
        "groq": f"groq:{settings.groq_model}",
    }
    return " -> ".join(labels[name] for name in order)

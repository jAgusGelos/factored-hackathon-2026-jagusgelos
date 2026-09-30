"""NFR bounded-retry/fallback tests (Task 2.3).

Every LLM call has a 15s timeout and at most 2 retries with backoff (1s,
2s); if every attempt fails, `call_llm` must raise `LLMUnavailable` — never
hang, crash uncaught, or return a hallucinated answer. The caller (Task 2.4's
orchestration) is responsible for catching this and forcing escalation with
the deterministic fallback message.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from app import llm
from app.policy import DisputeReason


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)


def _mock_client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.messages.create.side_effect = exc
    return client


def test_call_llm_raises_llm_unavailable_after_exhausting_retries_on_timeout():
    exc = anthropic.APITimeoutError(request=MagicMock())
    with patch("app.llm.anthropic.Anthropic", return_value=_mock_client_raising(exc)):
        with pytest.raises(llm.LLMUnavailable):
            llm.call_llm("hola")


def test_call_llm_retries_exactly_the_configured_budget_before_failing():
    exc = anthropic.APIConnectionError(request=MagicMock())
    mock_client = _mock_client_raising(exc)
    with patch("app.llm.anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(llm.LLMUnavailable):
            llm.call_llm("hola")

    expected_attempts = 1 + len(llm.config.LLM_RETRY_BACKOFF_SECONDS)
    assert mock_client.messages.create.call_count == expected_attempts


def test_call_llm_succeeds_without_retry_when_the_first_attempt_works():
    response = MagicMock()
    response.content = [MagicMock(type="text", text="hola, como puedo ayudarte")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("app.llm.anthropic.Anthropic", return_value=mock_client):
        result = llm.call_llm("hola")

    assert result == "hola, como puedo ayudarte"
    assert mock_client.messages.create.call_count == 1


def test_call_llm_recovers_after_a_transient_failure_within_the_retry_budget():
    response = MagicMock()
    response.content = [MagicMock(type="text", text="recuperado")]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        anthropic.APITimeoutError(request=MagicMock()),
        response,
    ]

    with patch("app.llm.anthropic.Anthropic", return_value=mock_client):
        result = llm.call_llm("hola")

    assert result == "recuperado"
    assert mock_client.messages.create.call_count == 2


def test_call_llm_does_not_retry_a_non_retryable_error():
    """A programming/auth error (e.g. a malformed request) should fail fast,
    not burn the retry budget pretending it might succeed on attempt 2.
    """
    mock_client = _mock_client_raising(ValueError("not an APIError"))
    with patch("app.llm.anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(ValueError):
            llm.call_llm("hola")
    assert mock_client.messages.create.call_count == 1


def test_call_llm_raises_llm_unavailable_when_api_key_is_missing(monkeypatch):
    """A missing ANTHROPIC_API_KEY (the exact state of this environment until
    a real key is supplied — see todo.md Task 2.3b) must degrade gracefully,
    not crash the request with an uncaught TypeError from the SDK's own
    client-side header validation.
    """
    monkeypatch.setattr(llm.config, "ANTHROPIC_API_KEY", "")
    mock_client = MagicMock()
    with patch("app.llm.anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(llm.LLMUnavailable):
            llm.call_llm("hola")
    mock_client.messages.create.assert_not_called()


def test_deterministic_fallback_message_exists_for_spanish_and_portuguese():
    assert llm.DETERMINISTIC_FALLBACK_MESSAGE["es"]
    assert llm.DETERMINISTIC_FALLBACK_MESSAGE["pt"]
    assert llm.DETERMINISTIC_FALLBACK_MESSAGE["es"] != llm.DETERMINISTIC_FALLBACK_MESSAGE["pt"]


def test_call_llm_maps_a_non_retryable_provider_error_to_llm_unavailable_without_retrying():
    """A revoked/invalid (but non-empty) key returns HTTP 401 — that must reach
    the caller's fallback path, not crash the request with an uncaught 500.
    """
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    exc = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=request), body=None
    )
    mock_client = _mock_client_raising(exc)
    with patch("app.llm.anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(llm.LLMUnavailable):
            llm.call_llm("hola")
    assert mock_client.messages.create.call_count == 1


def test_malformed_llm_date_degrades_to_missing_instead_of_crashing():
    raw = '{"amount": 10, "currency": "USD", "date": "9/3/2024", "merchant_hint": null, "wants_human": false}'
    extraction = llm._parse_extraction_response(raw)
    assert extraction.date is None
    assert extraction.amount == 10.0
    assert extraction.parse_failed is False


def test_json_wrapped_in_a_markdown_code_fence_is_still_parsed():
    """Real Claude Haiku output seen live: correct JSON inside a ```json fence."""
    raw = (
        '```json\n{\n  "amount": 1753.69,\n  "currency": "MXN",\n  "date": "2024-09-16",\n'
        '  "merchant_hint": null,\n  "wants_human": false\n}\n```'
    )
    extraction = llm._parse_extraction_response(raw)
    assert extraction.parse_failed is False
    assert extraction.amount == 1753.69
    assert extraction.currency == "MXN"
    assert extraction.date == "2024-09-16"


def test_unknown_or_missing_intent_defaults_to_report():
    base = '{"amount": null, "currency": null, "date": null, "merchant_hint": null, "wants_human": false'
    assert llm._parse_extraction_response(base + "}").intent == llm.ExtractionIntent.REPORT
    assert llm._parse_extraction_response(base + ', "intent": "approve_refund"}').intent == llm.ExtractionIntent.REPORT
    assert llm._parse_extraction_response(base + ', "intent": "greeting"}').intent == llm.ExtractionIntent.GREETING


@pytest.mark.parametrize("reason", list(DisputeReason))
def test_the_assessment_prompt_offers_and_describes_every_dispute_reason(reason):
    assert f'"{reason}"' in llm._ASSESSMENT_SYSTEM_PROMPT
    assert f"{reason} = " in llm._ASSESSMENT_SYSTEM_PROMPT

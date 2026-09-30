"""AD-5: no hidden SDK retries, and one model budget per chat turn.

`call_llm` runs inside the turn's budget (`llm.turn_deadline()`, opened by
`state_machine.handle_message`): each attempt's timeout is capped to what is
left, a backoff that does not fit is not slept, and a used-up budget raises
`LLMUnavailable`, which every caller already turns into its fallback. Outside
a turn (tests, eval, scripts) there is no shared deadline.

The clock is fake: a mocked attempt "times out" by advancing it by the
timeout it was given, so no test waits for real.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest

from app import config, llm
from app.state_machine import handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    charge_extraction,
    demo_session,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(llm, "time", fake)
    return fake


def _always_timing_out(clock: FakeClock, timeouts: list[float]) -> MagicMock:
    def create(*, model, max_tokens, system, messages, timeout):
        timeouts.append(timeout)
        clock.now += timeout
        raise anthropic.APITimeoutError(request=MagicMock())

    client = MagicMock()
    client.messages.create.side_effect = create
    return client


def _answering(text: str, sent: list[dict] | None = None) -> MagicMock:
    """A client that always answers `text`, recording each call's limits in `sent`."""

    def create(*, model, max_tokens, system, messages, timeout):
        if sent is not None:
            sent.append({"timeout": timeout, "max_tokens": max_tokens})
        response = MagicMock()
        response.content = [MagicMock(type="text", text=text)]
        return response

    client = MagicMock()
    client.messages.create.side_effect = create
    return client


def test_the_client_is_built_without_hidden_sdk_retries():
    with patch("app.llm.anthropic.Anthropic", return_value=_answering("ok")) as constructor:
        llm.call_llm("hola")
    assert constructor.call_args.kwargs["max_retries"] == 0


def test_each_attempt_is_capped_to_what_is_left_of_the_turn_budget(clock):
    timeouts: list[float] = []
    with patch("app.llm.anthropic.Anthropic", return_value=_always_timing_out(clock, timeouts)):
        with llm.turn_deadline(), pytest.raises(llm.LLMUnavailable):
            llm.call_llm("hola")

    # 15 s, then 1 s of backoff, then only the 4 s left; the 2 s backoff before
    # a third attempt no longer fits, so it is neither slept nor attempted.
    assert timeouts == [config.LLM_TIMEOUT_SECONDS, 4.0]
    assert clock.slept == [1.0]
    assert sum(timeouts) + sum(clock.slept) <= config.TURN_DEADLINE_SECONDS


def test_calls_of_one_turn_share_the_budget(clock):
    sent: list[dict] = []
    with llm.turn_deadline():
        clock.now += 12.0  # an earlier call of the same turn took 12 s
        with patch("app.llm.anthropic.Anthropic", return_value=_answering("ok", sent)):
            llm.call_llm("hola")
    assert [c["timeout"] for c in sent] == [config.TURN_DEADLINE_SECONDS - 12.0]


def test_a_used_up_budget_raises_without_calling_the_model(clock, caplog):
    client = _answering("ok")
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        with llm.turn_deadline(), pytest.raises(llm.LLMDeadlineExceeded):
            clock.now += config.TURN_DEADLINE_SECONDS
            llm.call_llm("hola")
    client.messages.create.assert_not_called()
    assert "llm_deadline_exceeded" in caplog.text


def test_outside_a_turn_there_is_no_deadline(clock):
    timeouts: list[float] = []
    with patch("app.llm.anthropic.Anthropic", return_value=_always_timing_out(clock, timeouts)):
        with pytest.raises(llm.LLMUnavailable):
            llm.call_llm("hola")
    assert timeouts == [config.LLM_TIMEOUT_SECONDS] * (1 + len(config.LLM_RETRY_BACKOFF_SECONDS))


def test_a_second_turn_starts_with_the_full_budget(clock):
    first: list[float] = []
    with patch("app.llm.anthropic.Anthropic", return_value=_always_timing_out(clock, first)):
        with llm.turn_deadline(), pytest.raises(llm.LLMUnavailable):
            llm.call_llm("hola")
    second: list[dict] = []
    with patch("app.llm.anthropic.Anthropic", return_value=_answering("ok", second)):
        with llm.turn_deadline():
            llm.call_llm("hola")
    assert [c["timeout"] for c in second] == [config.LLM_TIMEOUT_SECONDS]


def test_the_deadline_is_reset_even_when_the_turn_raises():
    with pytest.raises(RuntimeError), llm.turn_deadline():
        raise RuntimeError("boom")
    assert llm._remaining_budget() is None


def test_the_explanation_assessment_asks_for_a_short_answer():
    sent: list[dict] = []
    with patch("app.llm.anthropic.Anthropic", return_value=_answering("{}", sent)):
        llm.assess_explanation("No uso Uber", charge=llm.build_prompt_context(case_state="awaiting_explanation"))
    assert [c["max_tokens"] for c in sent] == [config.ASSESSMENT_MAX_TOKENS]


@requires_real_fixture
def test_handle_message_runs_each_turn_inside_its_own_budget(real_fixture_app_db):
    """Every model call of a turn sees a budget, and nothing leaks past the turn."""
    session = demo_session(real_fixture_app_db)
    budgets: list[float | None] = []
    client = mock_anthropic_client(charge_extraction(AUTO_RESOLVE_CHARGE))
    answer = client.messages.create.side_effect

    def create(**kwargs):
        budgets.append(llm._remaining_budget())
        return answer(**kwargs)

    client.messages.create.side_effect = create
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        handle_message(session, None, "Tengo un cargo que no reconozco", db_path=real_fixture_app_db)
    assert budgets and all(b is not None and 0 < b <= config.TURN_DEADLINE_SECONDS for b in budgets)
    assert llm._remaining_budget() is None


@requires_real_fixture
@pytest.mark.parametrize(
    ("error", "payload"),
    [
        (llm.LLMDeadlineExceeded("budget used up"), {"call": "extract_entities", "cause": "deadline"}),
        (llm.LLMUnavailable("down"), {"call": "extract_entities"}),
    ],
)
def test_a_call_stopped_by_the_deadline_is_logged_with_its_cause(real_fixture_app_db, error, payload):
    session = demo_session(real_fixture_app_db)
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(charge_extraction())), \
            patch("app.llm.extract_entities", side_effect=error):
        reply = handle_message(session, None, "Tengo un cargo que no reconozco", db_path=real_fixture_app_db)
    assert reply["reply"] == llm.DETERMINISTIC_FALLBACK_MESSAGE[llm.Language.ES]
    assert logged_events(real_fixture_app_db, "llm_unavailable") == [payload]


@requires_real_fixture
def test_a_reply_that_falls_back_on_the_deadline_is_logged_with_its_cause(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(charge_extraction())), \
            patch("app.llm.generate_response", side_effect=llm.LLMDeadlineExceeded("budget used up")):
        handle_message(session, None, "no sé el monto", db_path=real_fixture_app_db)
    assert {"call": "generate_response", "cause": "deadline"} in logged_events(real_fixture_app_db, "llm_unavailable")

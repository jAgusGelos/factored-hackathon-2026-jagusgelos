"""Friction #2: an explanation that denies using the merchant and says the
customer still has the card is enough, and the follow-up to a vague one asks
only for the detail that is missing (a closed enum from the assessment), never
for something the customer already said. None of it can move money: the
evidence check still decides (AD-13).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app import handoffs, llm, replies
from app.llm import Language, PromptScene
from app.policy import (
    DisputeReason,
    ExplanationAssessment,
    ExplanationVerdict,
    MissingDetail,
    evaluate_explanation,
)
from app.state_machine import CaseState, handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    CONVINCING_ASSESSMENT,
    charge_extraction,
    clean_txn,
    demo_session,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
)

README_EXPLANATION = "no uso Uber hace meses, tengo la tarjeta conmigo"


def _assessment_json(**overrides) -> str:
    return json.dumps({**CONVINCING_ASSESSMENT, **overrides})


# -- The assessment contract -----------------------------------------------------


def test_the_assessment_prompt_says_denying_the_merchant_and_having_the_card_is_specific():
    prompt = llm._ASSESSMENT_SYSTEM_PROMPT
    assert "IS specific" in prompt
    assert README_EXPLANATION in prompt
    # The negative example stays: a bare "no lo reconozco" is still vague.
    assert "a bare 'no lo reconozco'" in prompt
    # Being concrete is not being believable: the evidence check is separate.
    assert "never whether it is believable" in prompt


@pytest.mark.parametrize("detail", list(MissingDetail))
def test_the_assessment_prompt_offers_and_describes_every_missing_detail(detail):
    assert f'"{detail}"' in llm._ASSESSMENT_SYSTEM_PROMPT
    assert f"{detail} = " in llm._ASSESSMENT_SYSTEM_PROMPT


@pytest.mark.parametrize("detail", list(MissingDetail))
def test_a_valid_missing_detail_is_parsed(detail):
    assessment = llm._parse_assessment(_assessment_json(specific=False, missing_detail=str(detail)))
    assert assessment is not None
    assert assessment.missing_detail == detail


@pytest.mark.parametrize("raw_value", [None, "phone_number", 3, ["how_noticed"], ""])
def test_a_missing_or_unknown_missing_detail_is_dropped_not_fatal(raw_value):
    assessment = llm._parse_assessment(_assessment_json(specific=False, missing_detail=raw_value))
    assert assessment is not None
    assert assessment.missing_detail is None


def test_an_assessment_without_the_field_is_still_valid():
    assessment = llm._parse_assessment(_assessment_json())
    assert assessment is not None
    assert assessment.missing_detail is None


def test_the_allowlist_only_accepts_the_closed_enum():
    context = llm.build_prompt_context(case_state="explanation_followup", missing_detail="card_possession")
    assert context["missing_detail"] == MissingDetail.CARD_POSSESSION
    with pytest.raises(ValueError):
        llm.build_prompt_context(case_state="explanation_followup", missing_detail="tengo la tarjeta conmigo")


# -- missing_detail never reaches a decision ----------------------------------------


@pytest.mark.parametrize("specific", [True, False])
@pytest.mark.parametrize("attempts_left", [True, False])
def test_missing_detail_never_changes_the_policy_decision(specific, attempts_left):
    base = ExplanationAssessment(
        reason=DisputeReason.UNRECOGNIZED, specific=specific, consistent=True, contradictions=(),
        summary="Resumen.",
    )
    expected = evaluate_explanation(base, attempts_left=attempts_left)
    for detail in MissingDetail:
        with_detail = ExplanationAssessment(**{**base.__dict__, "missing_detail": detail})
        assert evaluate_explanation(with_detail, attempts_left=attempts_left) == expected


def test_missing_detail_never_changes_the_handoff():
    base = ExplanationAssessment(
        reason=DisputeReason.UNRECOGNIZED, specific=False, consistent=True, contradictions=(),
        summary="Resumen.",
    )
    report = handoffs.ReportedCharge(amount=100.0, date=None, currency="USD", reason=DisputeReason.UNRECOGNIZED)
    expected = handoffs.explanation_not_accepted(report, clean_txn(), "motivo", base, too_short=False)
    for detail in MissingDetail:
        with_detail = ExplanationAssessment(**{**base.__dict__, "missing_detail": detail})
        got = handoffs.explanation_not_accepted(report, clean_txn(), "motivo", with_detail, too_short=False)
        assert got == expected


# -- The follow-up question ---------------------------------------------------------


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("detail", [None, *MissingDetail])
def test_every_follow_up_fallback_exists_and_asks_one_question(language, detail):
    text = replies.explanation_followup(detail, language)
    assert text
    if detail is not None:
        assert text.count("?") == 1


@pytest.mark.parametrize("detail", [None, *MissingDetail])
def test_the_spanish_follow_up_fallbacks_do_not_use_voseo(detail):
    text = replies.explanation_followup(detail, Language.ES).lower()
    for voseo in ("tenés", "podés", "querés", "contame", "decime", "te diste", "con vos", "recibiste", "pagaste"):
        assert voseo not in text


def test_the_follow_up_instruction_forbids_asking_again_for_what_was_said():
    for language in Language:
        instruction = llm._STATE_INSTRUCTION[language][PromptScene.EXPLANATION_FOLLOWUP]
        assert "missing_detail" in instruction
        assert all(f"{detail} = " in instruction for detail in MissingDetail)


@requires_real_fixture
def test_the_follow_up_prompt_gets_the_missing_detail_and_never_the_customers_words(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    vague = {**CONVINCING_ASSESSMENT, "specific": False, "missing_detail": "how_noticed"}
    explanation = "no uso Uber, tengo la tarjeta conmigo guardada"
    prompts: list[str] = []

    def say(text, case_id=None, **kwargs):
        client = mock_anthropic_client(charge_extraction(), assessment=vague, captured_prompts=prompts)
        with patch("app.llm.anthropic.Anthropic", return_value=client):
            return handle_message(session, case_id, text, db_path=real_fixture_app_db, **kwargs)

    listed = say("no sé el monto")
    picked = say("cargo", case_id=listed["case_id"], selected_transaction_id=AUTO_RESOLVE_CHARGE)
    assert picked["state"] == CaseState.AWAITING_EXPLANATION
    prompts.clear()

    reply = say(explanation, case_id=picked["case_id"])

    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    followup_prompt = next(p for p in prompts if "case_state: explanation_followup" in p)
    assert "missing_detail: how_noticed" in followup_prompt
    assert explanation not in followup_prompt


@requires_real_fixture
def test_with_the_model_down_the_follow_up_asks_for_the_missing_detail(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    vague = {**CONVINCING_ASSESSMENT, "specific": False, "missing_detail": "merchant_known"}

    def say(text, case_id=None, nlg_down=False, **kwargs):
        client = mock_anthropic_client(charge_extraction(), assessment=vague)
        with patch("app.llm.anthropic.Anthropic", return_value=client):
            if nlg_down:
                with patch("app.llm.generate_response", side_effect=llm.LLMUnavailable("down")):
                    return handle_message(session, case_id, text, db_path=real_fixture_app_db, **kwargs)
            return handle_message(session, case_id, text, db_path=real_fixture_app_db, **kwargs)

    listed = say("no sé el monto")
    picked = say("cargo", case_id=listed["case_id"], selected_transaction_id=AUTO_RESOLVE_CHARGE)
    reply = say("tengo la tarjeta y no fui yo quien compró", case_id=picked["case_id"], nlg_down=True)

    assert reply["reply"] == replies.explanation_followup(MissingDetail.MERCHANT_KNOWN, Language.ES)


@requires_real_fixture
def test_the_readme_explanation_resolves_in_one_turn_when_the_model_reads_it_as_specific(real_fixture_app_db):
    """With the model's (mocked) specific reading, the README phrase is the
    last turn: the evidence check passes for this online charge.
    """
    session = demo_session(real_fixture_app_db)
    client = mock_anthropic_client(charge_extraction())
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        listed = handle_message(session, None, "no sé el monto", db_path=real_fixture_app_db)
        picked = handle_message(
            session, listed["case_id"], "cargo", db_path=real_fixture_app_db,
            selected_transaction_id=AUTO_RESOLVE_CHARGE,
        )
        reply = handle_message(session, picked["case_id"], README_EXPLANATION, db_path=real_fixture_app_db)

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assessed = logged_events(real_fixture_app_db, "explanation_assessed")
    assert [e["verdict"] for e in assessed] == [ExplanationVerdict.ACCEPT]

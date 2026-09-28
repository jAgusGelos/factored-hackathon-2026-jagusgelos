"""LLM integration (AD-5 privacy boundary, AD-10 provider config, NFR retries).

`build_prompt_context()` is the ONLY function in this codebase allowed to
turn customer/transaction data into LLM-bound context. It is a closed
keyword-only allowlist (explicit parameters, no **kwargs, no passthrough of
a raw dict/row/object) — a caller cannot smuggle an unlisted field through it
even by accident, because there is no parameter to put it in. Allowed facts:
transaction amount/date/merchant name/merchant category (AD-5's explicit
list, plus merchant_name since AD-11 Row 3 names it as a clarification
question option), case state, and round-tracking integers. NEVER allowed,
anywhere in this module: email, phone, address, document_number,
credit_score, full transcripts, or any raw DB row/object.

`call_llm()` is the only function that talks to the Anthropic API (AD-10:
Haiku 4.5 by default, `config.ANTHROPIC_MODEL` is the single config constant
every call site uses — never hardcoded per call site). It enforces the NFR's
bounded-retry contract: a 15s timeout, at most 2 retries with 1s/2s backoff,
then `LLMUnavailable` — the caller (Task 2.4's orchestration) is required to
catch that and force escalation with a deterministic fallback message, never
crash, hang, or hallucinate a best-guess answer.
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum

import anthropic

from app import config

logger = logging.getLogger("app.llm")


class Language(StrEnum):
    ES = "es"
    PT = "pt"


class PromptContext(dict[str, object]):
    """Only `build_prompt_context()` constructs this; `generate_response()`
    rejects any other mapping, so a raw dict/row can never reach a prompt
    (AD-5) even if a caller skips the allowlist function.
    """


class LLMUnavailable(Exception):
    """Raised when the LLM call failed after the full retry budget. The
    caller MUST catch this and force escalation with a deterministic
    fallback message (NFR) — never crash, hang, or hallucinate an answer.
    """


DETERMINISTIC_FALLBACK_MESSAGE = {
    Language.ES: (
        "Estamos teniendo dificultades técnicas para procesar tu solicitud en este "
        "momento. Un agente humano va a revisar tu caso a la brevedad."
    ),
    Language.PT: (
        "Estamos com dificuldades técnicas para processar sua solicitação neste "
        "momento. Um agente humano vai revisar seu caso em breve."
    ),
}


def build_prompt_context(
    *,
    case_state: str,
    language: Language = Language.ES,
    reported_amount: float | None = None,
    reported_currency: str | None = None,
    reported_date: str | None = None,
    candidate_amount: float | None = None,
    candidate_date: str | None = None,
    candidate_currency: str | None = None,
    candidate_merchant_name: str | None = None,
    candidate_merchant_category: str | None = None,
    candidate_count: int | None = None,
    clarification_rounds: int | None = None,
    resolution_reference: str | None = None,
) -> PromptContext:
    """The single allowlist function ALL prompt construction must go
    through. Returns only the explicitly-listed, non-None fields — this
    function's signature IS the privacy boundary (AD-5), not a convention
    layered on top of it.
    """
    context = {
        "case_state": case_state,
        "language": language,
        "reported_amount": reported_amount,
        "reported_currency": reported_currency,
        "reported_date": reported_date,
        "candidate_amount": candidate_amount,
        "candidate_date": candidate_date,
        "candidate_currency": candidate_currency,
        "candidate_merchant_name": candidate_merchant_name,
        "candidate_merchant_category": candidate_merchant_category,
        "candidate_count": candidate_count,
        "clarification_rounds": clarification_rounds,
        "resolution_reference": resolution_reference,
    }
    return PromptContext({k: v for k, v in context.items() if v is not None})


def call_llm(prompt: str, *, system: str | None = None) -> str:
    """The ONLY function in this codebase allowed to call the Anthropic API.

    Bounded retries per the NFR: 15s timeout per attempt, at most 2 retries
    with 1s then 2s backoff (3 attempts total). Raises `LLMUnavailable` if
    every attempt fails — never returns a hallucinated/partial answer.

    A missing `ANTHROPIC_API_KEY` is treated the same as an unavailable LLM
    (fail fast, no retry, `LLMUnavailable`) rather than a bug — retrying with
    the same empty key would never succeed, and the caller's fallback path
    must still fire rather than an uncaught `TypeError` reaching the client
    (the anthropic SDK raises a bare `TypeError` for this, not an `APIError`
    subclass, which would otherwise crash the request with an uncaught 500).
    """
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not configured — cannot call the LLM.")
        raise LLMUnavailable("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    last_exc: Exception | None = None
    retryable = (
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
        anthropic.InternalServerError,
        anthropic.RateLimitError,
    )

    for attempt, delay in enumerate([0.0, *config.LLM_RETRY_BACKOFF_SECONDS], start=1):
        if delay:
            logger.warning("LLM call attempt %d failed, retrying in %.1fs", attempt - 1, delay)
            time.sleep(delay)
        try:
            response = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=config.LLM_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                timeout=config.LLM_TIMEOUT_SECONDS,
            )
            return "".join(block.text for block in response.content if block.type == "text")
        except retryable as exc:
            last_exc = exc
        except anthropic.APIError as exc:
            # Non-transient provider errors (bad/revoked key, malformed request)
            # fail fast without burning retries, but still reach the caller's
            # fallback path instead of an uncaught 500.
            logger.error("LLM call failed with a non-retryable provider error: %s", exc)
            raise LLMUnavailable(f"LLM call failed with a non-retryable error: {exc}") from exc

    logger.error("LLM call exhausted its retry budget: %s", last_exc)
    raise LLMUnavailable(f"LLM call failed after retries: {last_exc}") from last_exc


_EXTRACTION_SYSTEM_PROMPT = {
    Language.ES: (
        "Sos un asistente que extrae datos estructurados de un mensaje de un cliente "
        "de un banco que reporta un cargo no reconocido. Respondé SIEMPRE con un JSON "
        'válido, sin texto adicional, con este formato exacto: '
        '{"amount": <numero o null>, "currency": <"MXN"|"COP"|"ARS"|"USD"|null>, '
        '"date": <"YYYY-MM-DD" o null>, "merchant_hint": <string o null>, '
        '"wants_human": <true|false>}. '
        "Si el cliente no menciona un monto, moneda o fecha, usá null en ese campo. "
        "Si el cliente pide explícitamente hablar con una persona/agente humano, "
        'poné "wants_human": true.'
    ),
    Language.PT: (
        "Você é um assistente que extrai dados estruturados de uma mensagem de um "
        "cliente de um banco relatando uma cobrança não reconhecida. Responda SEMPRE "
        "com um JSON válido, sem texto adicional, neste formato exato: "
        '{"amount": <numero ou null>, "currency": <"MXN"|"COP"|"ARS"|"USD"|null>, '
        '"date": <"YYYY-MM-DD" ou null>, "merchant_hint": <string ou null>, '
        '"wants_human": <true|false>}. '
        "Se o cliente não mencionar um valor, moeda ou uma data, use null nesse campo. "
        "Se o cliente pedir explicitamente para falar com uma pessoa/agente humano, "
        'defina "wants_human": true.'
    ),
}


@dataclass(frozen=True)
class ExtractedEntities:
    amount: float | None
    currency: str | None
    date: str | None
    # Collected but not yet consulted by app/state_machine.py: AD-11 Row 3
    # names "merchant name" as one option for the clarifying question, but
    # the current clarifying-question flow asks a generic question rather
    # than narrowing candidates by this hint. Logged in the case's message
    # history either way, so it isn't lost — wiring it into disambiguation
    # is a documented follow-up, not silently dropped.
    merchant_hint: str | None
    wants_human: bool
    parse_failed: bool


def _build_extraction_prompt(customer_text: str, *, language: Language, today: str) -> str:
    if language == Language.ES:
        return f"Fecha de hoy: {today}\nMensaje del cliente: {customer_text}"
    return f"Data de hoje: {today}\nMensagem do cliente: {customer_text}"


def _valid_iso_date(value: object) -> str | None:
    # The LLM's date is untrusted free-form output: anything that is not a
    # real ISO date is treated as "not provided" so the clarification loop
    # asks for it again, instead of crashing a later `date.fromisoformat()`.
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _parse_extraction_response(raw: str) -> ExtractedEntities:
    try:
        data = json.loads(raw)
        return ExtractedEntities(
            amount=float(data["amount"]) if data.get("amount") is not None else None,
            currency=data.get("currency"),
            date=_valid_iso_date(data.get("date")),
            merchant_hint=data.get("merchant_hint"),
            wants_human=bool(data.get("wants_human", False)),
            parse_failed=False,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning("Could not parse entity-extraction response as the expected JSON: %r", raw)
        return ExtractedEntities(
            amount=None, currency=None, date=None, merchant_hint=None, wants_human=False,
            parse_failed=True,
        )


def extract_entities(customer_text: str, *, language: Language, today: str) -> ExtractedEntities:
    """Runs the NLU entity-extraction call. Raises `LLMUnavailable` on
    exhausted retries (the caller must force-escalate); a malformed (but
    successfully-returned) response degrades to `parse_failed=True` rather
    than raising, since that is a content problem, not an availability one.
    """
    prompt = _build_extraction_prompt(customer_text, language=language, today=today)
    raw = call_llm(prompt, system=_EXTRACTION_SYSTEM_PROMPT[language])
    return _parse_extraction_response(raw)


_RESPONSE_SYSTEM_PROMPT = {
    Language.ES: (
        "Sos un asistente de atención al cliente de un banco, especializado en "
        "disputas de transacciones. Respondé de forma breve, clara y empática, "
        "en español. USÁ SOLO los hechos que te paso en el contexto — nunca "
        "inventes montos, fechas, comercios ni resultados que no estén en el "
        "contexto. No prometas nada que el contexto no confirme explícitamente."
    ),
    Language.PT: (
        "Você é um assistente de atendimento ao cliente de um banco, especializado "
        "em disputas de transações. Responda de forma breve, clara e empática, em "
        "português. USE APENAS os fatos que constam no contexto — nunca invente "
        "valores, datas, comerciantes ou resultados que não estejam no contexto. "
        "Não prometa nada que o contexto não confirme explicitamente."
    ),
}


def generate_response(context: PromptContext, *, language: Language) -> str:
    """Grounded NLG call. Raises `LLMUnavailable` on exhausted retries."""
    if not isinstance(context, PromptContext):
        raise TypeError("generate_response() only accepts build_prompt_context() output (AD-5)")
    prompt = "\n".join(f"{k}: {v}" for k, v in context.items())
    return call_llm(prompt, system=_RESPONSE_SYSTEM_PROMPT[language])

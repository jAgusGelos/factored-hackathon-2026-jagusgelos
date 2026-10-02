"""The code check on a model-written summary before it reaches a handoff (the
explanation's and the statement's). The prompts already ask for one neutral
sentence with no personal data; this is the guarantee, with a margin, since
the customer's own text can steer the model. A summary that fails is
dropped, never repaired, and the caller logs why (`SummaryDrop`).
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from app import llm
from app.transactions import TransactionCandidate


class SummaryDrop(StrEnum):
    TOO_LONG = "too_long"
    NUMBER = "number"
    CONTACT_OR_QUOTE = "contact_or_quote"
    NAME = "name"
    COPIED = "copied"


_MAX_WORDS = llm.MODEL_SUMMARY_MAX_WORDS + 15
_QUOTED_RUN_WORDS = 6
# A summary names no number but a date or the charge's own amount, so the
# digits left once those are removed are counted however they are spread:
# a document, phone, card or account number split into short groups still
# adds up. A year or a count of days fits under the cap.
_MAX_STRAY_DIGITS = 4
_MAX_DATES = 2
_MONTHS = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
    "|janeiro|fevereiro|março|maio|junho|julho|setembro|outubro|novembro|dezembro"
)
_DATE = re.compile(
    r"(?<![\d./-])(?:\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])[./-](?:\d{4}|\d{2}))(?![./-]?\d)"
    rf"|\b(?:(?:0?[1-9]|[12]\d|3[01])\s+de\s+)?(?:{_MONTHS})(?:\s+(?:de\s+)?\d{{4}})?\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d(?:[.,]?\d)*")
_DIGIT_WORDS = frozenset({
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "zero", "um", "dois", "três", "quatro", "sete", "oito", "nove",
})
_MAX_DIGIT_WORDS_IN_A_ROW = 4
_CONTACT = re.compile(r"@|https?:|www\.|\.com\b|\barroba\b|\b(?:punto|ponto|dot)\s+com\b", re.IGNORECASE)
# An apostrophe inside a word (McDonald's) is no quote.
_QUOTE = re.compile(r"[\"“”«»‘„`]|(?<!\w)['’]|['’](?!\w)")
# Two capitalized words together, written by the customer too, read as a
# person's full name: the model is never told the customer's name, so it can
# only echo it. A place or a brand the customer did not write is no name.
_FULL_NAME = re.compile(r"\b[A-ZÁÉÍÓÚÑÂÊÔÃÕÇ][a-záéíóúñâêôãõç]+\s+[A-ZÁÉÍÓÚÑÂÊÔÃÕÇ][a-záéíóúñâêôãõç]+")
# A copied run only counts when it carries the customer's own content, not
# just the charge's facts in the words anyone would use for them.
_MIN_CONTENT_WORDS = 2
_MIN_CONTENT_WORD_CHARS = 4
_COMMON_WORDS = frozenset({
    "cliente", "cargo", "cargos", "compra", "tarjeta", "banco", "comercio", "este", "esta", "ese", "esa",
    "para", "pero", "como", "porque", "desde", "hasta", "sobre", "entre", "cuando", "donde", "pesos",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
    "noviembre", "diciembre",
})


def summary_drop(summary: str, customer_text: str, charge: TransactionCandidate | None) -> SummaryDrop | None:
    """Why `summary` must not reach a handoff, or None when it may."""
    text = _normalized(summary, charge)
    if len(text.split()) > _MAX_WORDS:
        return SummaryDrop.TOO_LONG
    if _names_a_number(text, charge):
        return SummaryDrop.NUMBER
    if _CONTACT.search(text) or _QUOTE.search(text):
        return SummaryDrop.CONTACT_OR_QUOTE
    if _echoes_a_full_name(text, customer_text):
        return SummaryDrop.NAME
    if _quotes_the_customer(text, _normalized(customer_text, charge)):
        return SummaryDrop.COPIED
    return None


def _normalized(text: str, charge: TransactionCandidate | None) -> str:
    """Compatibility forms folded (superscript, circled, fullwidth digits),
    and the charge's merchant, which is on record, written as a common word:
    its name (an apostrophe, two capitalized words) is no personal data.
    """
    text = unicodedata.normalize("NFKC", text)
    if charge is None or not charge.merchant_name:
        return text
    merchant = rf"(?:{_QUOTE.pattern})?{re.escape(charge.merchant_name)}(?:{_QUOTE.pattern})?"
    return re.sub(merchant, "comercio", text, flags=re.IGNORECASE)


def _echoes_a_full_name(text: str, customer_text: str) -> bool:
    said = _folded(customer_text)
    return any(_folded(name) in said for name in _FULL_NAME.findall(text))


def _folded(text: str) -> str:
    """Case and accents dropped, whitespace collapsed."""
    stripped = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def _names_a_number(text: str, charge: TransactionCandidate | None) -> bool:
    if len(_DATE.findall(text)) > _MAX_DATES:
        return True
    allowed = _charge_amount_digits(charge)
    stray = [digits for digits in map(_digits, _NUMBER.findall(_DATE.sub(" ", text))) if digits not in allowed]
    return sum(map(len, stray)) > _MAX_STRAY_DIGITS or _spells_out_a_number(text)


def _digits(number: str) -> str:
    return re.sub(r"\D", "", number)


def _charge_amount_digits(charge: TransactionCandidate | None) -> frozenset[str]:
    """The charge's own amount as the model may write it (with or without cents)."""
    if charge is None:
        return frozenset()
    return frozenset({str(int(charge.amount)), _digits(f"{charge.amount:.2f}")})


def _spells_out_a_number(text: str) -> bool:
    in_a_row = 0
    for word in _words(text):
        in_a_row = in_a_row + 1 if word in _DIGIT_WORDS else 0
        if in_a_row > _MAX_DIGIT_WORDS_IN_A_ROW:
            return True
    return False


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _is_content_word(word: str) -> bool:
    return len(word) >= _MIN_CONTENT_WORD_CHARS and not word.isdigit() and word not in _COMMON_WORDS


def _content_words(run: tuple[str, ...]) -> int:
    return sum(1 for word in run if _is_content_word(word))


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    return any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


def _is_copied_content(run: tuple[str, ...], runs: set[tuple[str, ...]]) -> bool:
    return run in runs and _content_words(run) >= _MIN_CONTENT_WORDS


def _quotes_the_customer(summary: str, customer_text: str) -> bool:
    """The whole summary copied, or a run of the customer's own words."""
    written = _words(summary)
    said = _words(customer_text)
    if not written:
        return False
    if len(written) < _QUOTED_RUN_WORDS:
        return _contains_sequence(said, written)
    runs = {tuple(said[i:i + _QUOTED_RUN_WORDS]) for i in range(len(said) - _QUOTED_RUN_WORDS + 1)}
    copied = (
        tuple(written[i:i + _QUOTED_RUN_WORDS]) for i in range(len(written) - _QUOTED_RUN_WORDS + 1)
    )
    return any(_is_copied_content(run, runs) for run in copied)

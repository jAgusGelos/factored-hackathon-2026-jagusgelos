"""Baseline (b) of the measured eval: a deterministic regex/keyword extractor
standing in for the model's entity extraction (`app.llm.extract_entities`).

Only the extraction step changes; every other model call of the app stays
real. It reads amounts written with digits ("38.500", "$38,500", "38 mil",
"38k"), dates ("14/06", "14 de junio", "dia 14 de junho", "ayer"/"ontem"),
a merchant from a fixed catalogue of merchant names (a production rules
engine would ship a merchant dictionary; this one is the fixture's), a
request for a person and the message intent from keyword lists. Numbers in
words, typos it has no rule for and other languages are its known blind
spots, which is the point of the comparison.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from app.llm import ExtractedEntities, ExtractionIntent, Language, stated_currency

MERCHANT_CATALOGUE = (
    "Empresa Telefónica", "Tienda Don José", "Internet Plus", "Gasolinera Express", "Super Ahorro",
    "Streaming Music", "Cine Premium", "Farmacia Salud", "Tienda Online Global", "Boutique Moda", "Uber",
    "Taxi Seguro", "Restaurante El Buen Sabor",
)

_MONTHS = {
    "enero": 1, "janeiro": 1, "febrero": 2, "fevereiro": 2, "marzo": 3, "marco": 3, "abril": 4,
    "mayo": 5, "maio": 5, "junio": 6, "junho": 6, "julio": 7, "julho": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "setembro": 9, "octubre": 10, "outubro": 10,
    "noviembre": 11, "novembro": 11, "diciembre": 12, "dezembro": 12,
}

_HUMAN = (
    r"\b(hablar|falar|comunicar\w*|pasar\w*|passar\w*)\b.{0,30}\b(persona|humano|humana|agente|asesor\w*|atendente|pessoa|operador\w*)\b",
    r"\b(quiero|quero|necesito|preciso)\b.{0,15}\b(una persona|um humano|una humana|un humano|uma pessoa|un asesor|um atendente|un agente)\b",
)
_SHOW_CHARGES = (r"\b(ver|mostrar?|muestre|mostre|lista\w*)\b.{0,25}\b(cargos|cobros|movimientos|cobrancas|movimentacoes|compras)\b",)
_GREETING = (r"^\s*(hola|buenas|buenos dias|buenas tardes|oi|ola|bom dia|boa tarde)\b[\s!.,]*$",)
_OTHER = (
    r"\bsaldo\b", r"\bprestamo\b", r"\bemprestimo\b", r"\bcupo\b", r"\blimite\b", r"\bfecha de pago\b",
    r"\bvencimiento\b", r"\bvencimento\b", r"\bextracto\b(?!.*\bcargo)", r"\btarjeta nueva\b", r"\bcartao novo\b",
)
_REPORT = (
    r"\bno reconozco\b", r"\bnao reconheco\b", r"\bcobr\w*\b", r"\bcargo\w*\b", r"\bcompra\w*\b",
    r"\bdos veces\b", r"\bduas vezes\b", r"\bdoble\b", r"\bdobrad\w*\b", r"\breembols\w*\b", r"\bdevoluc\w*\b",
    r"\bestorno\b", r"\bfraude\b", r"\brob\w*\b", r"\broub\w*\b",
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _number(raw: str) -> float | None:
    digits = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+([.,]\d{1,2})?", digits):
        parts = re.split(r"[.,]", digits)
        if len(parts[-1]) <= 2 and len(parts) > 2:
            return float("".join(parts[:-1]) + "." + parts[-1])
        return float("".join(parts))
    try:
        return float(digits.replace(",", "."))
    except ValueError:
        return None


def extract_amount(folded: str) -> float | None:
    """The first amount that does not look like part of a date."""
    for match in re.finditer(r"(\$\s*)?(\d[\d.,]*)\s*(mil\b|k\b|mill\w*|lucas|luca|pesos|cop|reais)?", folded):
        dollar, raw, unit = match.groups()
        end = match.end(2)
        if re.match(r"\s*(de|/|-)\s*(\d|" + "|".join(_MONTHS) + ")", folded[end:end + 14]):
            continue  # "14 de junio", "14/06"
        if re.search(r"(dia|el|del|day)\s*$", folded[:match.start()]) and not (dollar or unit):
            continue
        value = _number(raw.rstrip(".,"))
        if value is None:
            continue
        if unit in ("mil", "k", "lucas", "luca"):
            value *= 1_000
        elif unit and unit.startswith("mill"):
            value *= 1_000_000
        if value < 100 and not (dollar or unit):
            continue  # a day number, a count, an hour
        return value
    return None


def extract_date(folded: str, today: date) -> date | None:
    if re.search(r"\b(ayer|ontem)\b", folded):
        return today - timedelta(days=1)
    if re.search(r"\b(antier|anteayer|anteontem)\b", folded):
        return today - timedelta(days=2)
    match = re.search(r"\b(\d{1,2})\s*(?:de\s+)?(" + "|".join(_MONTHS) + r")\b", folded)
    if match:
        day, month = int(match.group(1)), _MONTHS[match.group(2)]
    else:
        match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", folded)
        if not match:
            return None
        day, month = int(match.group(1)), int(match.group(2))
        if match.group(3):
            year = int(match.group(3))
            try:
                return date(year + 2000 if year < 100 else year, month, day)
            except ValueError:
                return None
    try:
        return date(today.year, month, day)
    except ValueError:
        return None


def extract_merchant(folded: str) -> str | None:
    for name in MERCHANT_CATALOGUE:
        if _fold(name) in folded:
            return name
    tokens = {
        "uber": "Uber", "taxi": "Taxi Seguro", "cine": "Cine Premium", "farmacia": "Farmacia Salud",
        "boutique": "Boutique Moda", "gasolinera": "Gasolinera Express", "restaurante": "Restaurante El Buen Sabor",
        "streaming": "Streaming Music", "telefonica": "Empresa Telefónica", "tienda online": "Tienda Online Global",
    }
    return next((name for token, name in tokens.items() if re.search(rf"\b{token}\b", folded)), None)


def extract_entities(customer_text: str, *, language: Language, today: str) -> ExtractedEntities:
    """Same contract as `app.llm.extract_entities`, with no model call."""
    folded = _fold(customer_text)
    amount = extract_amount(folded)
    day = extract_date(folded, date.fromisoformat(today))
    merchant = extract_merchant(folded)
    wants_human = _any(_HUMAN, folded)
    if _any(_SHOW_CHARGES, folded):
        intent = ExtractionIntent.SHOW_CHARGES
    elif _any(_REPORT, folded) or amount is not None or merchant is not None:
        intent = ExtractionIntent.REPORT
    elif _any(_OTHER, folded):
        intent = ExtractionIntent.OTHER
    elif _any(_GREETING, folded):
        intent = ExtractionIntent.GREETING
    else:
        intent = ExtractionIntent.REPORT
    currency = None
    for code in ("COP", "USD", "MXN", "ARS"):
        currency = stated_currency(customer_text, code)
        if currency:
            break
    return ExtractedEntities(
        amount=amount, currency=currency, date=day.isoformat() if day else None, merchant_hint=merchant,
        wants_human=wants_human, parse_failed=False, intent=intent,
    )

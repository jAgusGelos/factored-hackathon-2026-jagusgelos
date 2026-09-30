"""The register every customer-facing text keeps (usability-s2 AD-1/AD-2):
Spanish in "usted", neutral and professional (the demo customer is
Colombian), Portuguese in "você" without slang.

Two closed lists per language, matched as whole words and ignoring case:

- `BROAD`: every form a deterministic text, UI string or ES prompt must never
  contain (voseo, tuteo in ES, colloquialisms). `tests/test_register.py`
  sweeps all of them with it.
- `RUNTIME`: the forms of `BROAD` minus those that are also plain words (`_ES_PLAIN_WORDS`, `_PT_PLAIN_WORDS`), checked on every
  model reply by `app/case_turn.py::Turn.generate_reply`. A hit replaces the
  reply with that step's template.

Accents are matched exactly; the unaccented spellings a model tends to write
("podes") are listed explicitly instead of stripping accents, which would
turn "tocá" into the ordinary "toca".
"""

from __future__ import annotations

import re
import unicodedata

from app.llm import Language

_ES_VOSEO = (
    "vos", "sos", "podés", "tenés", "querés", "sabés", "reconocés", "preferís", "necesitás",
    "contame", "decime", "dejame", "confirmame", "avisame", "fijate", "acordate", "contanos",
    "tocá", "elegí", "escribí", "mirá", "intentá", "esperá", "pedí", "respondé", "poné",
    "seguí", "nombrá", "explicá", "cerrá", "hacé", "usá", "pedile", "contale", "decile",
    "preguntale", "presentate", "decíselo", "entendé", "podes", "tenes", "queres",
)
_ES_TUTEO = (
    "tu", "tus", "te", "ti", "contigo", "puedes", "tienes", "quieres", "reconoces", "elijas", "cuentes",
    "puedas", "quieras", "reconozcas", "diste", "recibiste", "pagaste",
)
_ES_COLLOQUIAL = ("dale", "che", "bárbaro", "qué onda", "sin drama", "re bien", "un toque", "joya")

_ES_PLAIN_WORDS = frozenset({"sos", "pedí", "seguí", "elegí", "escribí", "ti", "un toque", "joya"})

_PT_COLLOQUIAL = (
    "a gente", "deixa eu", "tá bom", "beleza", "galera", "valeu", "né", "pra", "pro", "tipo assim",
)
_PT_PLAIN_WORDS = frozenset({"a gente", "pra", "pro"})

BROAD: dict[Language, tuple[str, ...]] = {
    Language.ES: _ES_VOSEO + _ES_TUTEO + _ES_COLLOQUIAL,
    Language.PT: _PT_COLLOQUIAL,
}
RUNTIME: dict[Language, tuple[str, ...]] = {
    Language.ES: tuple(form for form in BROAD[Language.ES] if form not in _ES_PLAIN_WORDS),
    Language.PT: tuple(form for form in BROAD[Language.PT] if form not in _PT_PLAIN_WORDS),
}


def _pattern(forms: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


_BROAD_PATTERNS = {lang: _pattern(forms) for lang, forms in BROAD.items()}
_RUNTIME_PATTERNS = {lang: _pattern(forms) for lang, forms in RUNTIME.items()}


def _found(pattern: re.Pattern[str], text: str) -> list[str]:
    return [m.group(0).lower() for m in pattern.finditer(unicodedata.normalize("NFC", text))]


def broad_findings(text: str, language: Language) -> list[str]:
    """Every `BROAD` form in `text` (for the static sweep)."""
    return _found(_BROAD_PATTERNS[language], text)


def runtime_findings(text: str, language: Language) -> list[str]:
    """Every `RUNTIME` form in a model reply; empty when the register is right."""
    return _found(_RUNTIME_PATTERNS[language], text)

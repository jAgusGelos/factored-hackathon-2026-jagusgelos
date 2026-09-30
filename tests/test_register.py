"""Register of every customer-facing text (usability-s2 AD-1/AD-2): Spanish
in "usted" without voseo, tuteo or colloquialisms, Portuguese without slang.

Sweeps the deterministic texts (`app/replies.py`, `app/llm.py`), the Spanish
prompts the model reads, and the UI strings (`static/js/*.js`, `static/*.html`)
with `app.register.BROAD`, and pins what the runtime check on model replies
does and does not flag.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from html.parser import HTMLParser

import pytest

from app import cases, llm, register, replies
from app.case_turn import Turn
from app.llm import Language, PromptScene
from tests.support import STATIC, chat_js_language_block, demo_session, logged_events


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, tuple | list):
        for item in value:
            yield from _strings(item)


def _by_language(module: object) -> dict[Language, list[tuple[str, str]]]:
    """(name, text) of every module-level text keyed by language."""
    found: dict[Language, list[tuple[str, str]]] = {Language.ES: [], Language.PT: []}
    for name, value in vars(module).items():
        if not isinstance(value, dict) or not value or not all(k in (Language.ES, Language.PT) for k in value):
            continue
        for lang, texts in value.items():
            found[Language(lang)] += [(name, text) for text in _strings(texts)]
    return found


def _js_literals(source: str) -> list[str]:
    return [m.group(2) for m in re.finditer(r"([\"'`])((?:\\.|(?!\1).)*)\1", source)]


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.texts.append(data.strip())

    def handle_starttag(self, tag, attrs) -> None:
        self.texts += [v for k, v in attrs if k in ("placeholder", "aria-label", "title") and v]


def _surfaces() -> Iterator[tuple[str, Language, str]]:
    for module in (replies, llm):
        for lang, texts in _by_language(module).items():
            for name, text in texts:
                if module is llm:
                    # A prompt may quote a form to forbid it ('vos', 'dale'): mentioned, not used.
                    text = re.sub(r"'[^']*'", "''", text)
                yield f"{module.__name__}.{name}", lang, text
    chat_js = (STATIC / "js" / "chat.js").read_text(encoding="utf-8")
    for lang in Language:
        for text in _js_literals(chat_js_language_block(chat_js, lang)):
            yield f"chat.js STRINGS.{lang}", lang, text
    for text in _js_literals((STATIC / "js" / "login.js").read_text(encoding="utf-8")):
        yield "login.js", Language.ES, text
    for page in sorted(STATIC.glob("*.html")):
        collector = _TextCollector()
        collector.feed(page.read_text(encoding="utf-8"))
        for text in collector.texts:
            yield page.name, Language.ES, text


def test_the_sweep_reaches_every_surface():
    sources = {source.split(".")[0] if source.startswith("app.") else source for source, _, _ in _surfaces()}
    assert {"app", "chat.js STRINGS.es", "chat.js STRINGS.pt", "login.js", "index.html", "chat.html"} <= sources
    names = {source for source, _, _ in _surfaces()}
    assert {"app.llm._RESPONSE_SYSTEM_PROMPT", "app.llm._STATE_INSTRUCTION", "app.replies.WELCOME"} <= names


def test_no_customer_text_breaks_the_register():
    offenders = [
        (source, lang.value, found, text[:80])
        for source, lang, text in _surfaces()
        if (found := register.broad_findings(text, lang))
    ]
    assert offenders == []


def test_the_spanish_response_prompt_asks_for_usted():
    prompt = llm._RESPONSE_SYSTEM_PROMPT[Language.ES]
    assert "Trate al cliente SIEMPRE de usted" in prompt
    assert prompt.count("voseo") == 1 and "Nunca use voseo" in prompt


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mirá, contame qué pasó", ["mirá", "contame"]),
        ("¿Podes revisarlo? Dale.", ["podes", "dale"]),
        ("Entendé que preferís hablar con alguien", ["entendé", "preferís"]),
        ("Te paso sin drama, ¿vos querés?", ["sin drama", "vos", "querés"]),
    ],
)
def test_the_runtime_check_flags_voseo_and_slang(text, expected):
    assert register.runtime_findings(text, Language.ES) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Mira el detalle más abajo: está en su cuenta.",
        "Le toca revisar el cargo; acá o aquí, como prefiera. Usa la tarjeta desde 2024.",
        "¿Puede contarme qué pasó? Toque el cargo que no reconoce.",
        "Después le escribimos. Podemos ayudarle a elegir el cargo.",
    ],
)
def test_the_runtime_check_leaves_plain_spanish_alone(text):
    assert register.runtime_findings(text, Language.ES) == []


def test_the_runtime_check_flags_portuguese_slang_only():
    assert register.runtime_findings("Beleza, deixa eu ver, tá bom?", Language.PT) == ["beleza", "deixa eu", "tá bom"]
    assert register.runtime_findings("Pode me contar o que aconteceu com a cobrança?", Language.PT) == []


def _turn(app_db, language: Language = Language.ES) -> Turn:
    session = demo_session(app_db)
    case = cases.create_case(session.customer_id, language, db_path=app_db)
    return Turn(session, case, language, "corr-register", app_db)


@pytest.mark.parametrize(
    ("language", "model_reply"),
    [(Language.ES, "Mirá, contame qué pasó con ese cargo, ¿dale?"), (Language.PT, "Beleza, deixa eu ver a cobrança.")],
)
def test_a_model_reply_off_register_is_replaced_by_the_template(real_fixture_app_db, monkeypatch, language, model_reply):
    monkeypatch.setattr(llm, "generate_response", lambda context, *, language: model_reply)
    context = llm.build_prompt_context(case_state=PromptScene.GREETING, language=language)

    reply = _turn(real_fixture_app_db, language).generate_reply(context, fallback="PLANTILLA")

    assert reply == "PLANTILLA"
    assert logged_events(real_fixture_app_db, "nlg_reply_replaced") == [{"reason": "register", "scene": "greeting"}]


def test_a_model_reply_in_register_is_kept(real_fixture_app_db, monkeypatch):
    model_reply = "Con gusto le ayudo. Toque el cargo que no reconoce o cuénteme el monto."
    monkeypatch.setattr(llm, "generate_response", lambda context, *, language: model_reply)
    context = llm.build_prompt_context(case_state=PromptScene.GREETING, language=Language.ES)

    assert _turn(real_fixture_app_db).generate_reply(context, fallback="PLANTILLA") == model_reply
    assert logged_events(real_fixture_app_db, "nlg_reply_replaced") == []

"""Static checks on the chat UI (usability-s1 DESIGN.md, AD-1/AD-7).

Reads `static/` as text: every string added for the wait indicator, retry and
new-claim flow exists in ES and PT (their register is swept by
`tests/test_register.py`), the wait is announced by a
`role=status` region outside the chat log, the case panel is no longer a live
region, the typing dots stop moving under `prefers-reduced-motion`, and the
buttons send the server's `CustomerAction` values (AD-9).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from app import replies
from app.case_model import CustomerAction
from app.llm import Language
from tests.support import STATIC, chat_js_language_block

CHAT_JS = (STATIC / "js" / "chat.js").read_text(encoding="utf-8")
CHAT_HTML = (STATIC / "chat.html").read_text(encoding="utf-8")
APP_CSS = (STATIC / "css" / "app.css").read_text(encoding="utf-8")

NEW_KEYS = (
    "waitGeneric", "waitSearching", "waitConfirming", "waitExplanation", "waitSlow",
    "turnError", "retry", "quickNewClaim", "claimResolved", "claimEscalated", "claimDivider",
    "caseNumberLabel", "escalationStepDone", "escalationStepPending", "escalationDeadline",
    "stepDone", "stepPending", "escalationCharge", "escalationReason", "escalationNote",
)


def _entries(lang: str) -> dict[str, str]:
    """Top-level keys of STRINGS.<lang> mapped to their source line(s)."""
    entries: dict[str, str] = {}
    current = None
    for line in chat_js_language_block(CHAT_JS, lang).splitlines():
        key = re.match(r"^    (\w+):", line)
        if key:
            current = key.group(1)
            entries[current] = line
        elif current and line.startswith("      "):
            entries[current] += "\n" + line
    return entries


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_every_new_string_exists_in_both_languages(lang):
    missing = [key for key in NEW_KEYS if key not in _entries(lang)]
    assert not missing


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_the_human_offer_names_the_button_the_chat_shows(lang):
    # `replies.HUMAN_OFFER` quotes the button by its label (plan.md AD-8).
    label = re.search(r'quickHuman: "([^"]+)"', _entries(lang)["quickHuman"]).group(1)
    assert f"«{label}»" in replies.HUMAN_OFFER[Language(lang)]


def test_es_and_pt_define_the_same_keys():
    assert set(_entries("es")) == set(_entries("pt"))


def test_new_strings_follow_the_design_copy():
    es, pt = _entries("es"), _entries("pt")
    assert "Está tardando más de lo habitual. Seguimos procesando su mensaje." in es["waitSlow"]
    assert "Está demorando mais que o normal. Continuamos processando sua mensagem." in pt["waitSlow"]
    assert '"Reintentar"' in es["retry"] and '"Tentar novamente"' in pt["retry"]
    assert '"Reportar otro cargo"' in es["quickNewClaim"]
    assert '"Contestar outra cobrança"' in pt["quickNewClaim"]


class _Tree(HTMLParser):
    """Records each element's attributes and the ids of its open ancestors."""

    VOID = {"meta", "link", "input", "br", "img", "hr"}

    def __init__(self):
        super().__init__()
        self.stack: list[str | None] = []
        self.elements: list[tuple[dict, list[str | None]]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((attributes, list(self.stack)))
        if tag not in self.VOID:
            self.stack.append(attributes.get("id"))

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.stack:
            self.stack.pop()


def _chat_html():
    tree = _Tree()
    tree.feed(CHAT_HTML)
    return {attrs["id"]: (attrs, ancestors) for attrs, ancestors in tree.elements if "id" in attrs}


def test_wait_status_region_lives_outside_the_chat_log():
    elements = _chat_html()
    attrs, ancestors = elements["chat-status"]
    assert attrs.get("role") == "status"
    assert "sr-only" in attrs.get("class", "").split()
    assert "chat-log" not in ancestors
    assert elements["chat-log"][0].get("aria-live") == "polite"


def test_case_panel_is_not_a_live_region():
    attrs, _ = _chat_html()["case-panel"]
    assert "aria-live" not in attrs


def test_typing_dots_are_static_with_reduced_motion():
    match = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", APP_CSS, re.DOTALL)
    assert match
    assert re.search(r"\.typing-dots[^{]*\{[^}]*animation: none", match.group(1))


def test_new_ui_classes_use_only_existing_tokens():
    root = re.search(r":root \{(.*?)\}", APP_CSS, re.DOTALL).group(1)
    defined = set(re.findall(r"(--[\w-]+):", root))
    start = APP_CSS.index(".action-card__actions")
    end = APP_CSS.index("@media (prefers-reduced-motion: reduce)")
    new_rules = APP_CSS[start:end]
    assert set(re.findall(r"var\((--[\w-]+)\)", new_rules)) <= defined
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", new_rules)


def test_the_client_actions_match_the_server_ones():
    block = re.search(r"const ACTIONS = Object\.freeze\(\{(.*?)\}\);", CHAT_JS, re.DOTALL)
    assert block, "ACTIONS not found in chat.js"
    assert set(re.findall(r'"(\w+)"', block.group(1))) == {str(a) for a in CustomerAction}


def _function_body(name: str) -> str:
    match = re.search(rf"^(?:async )?function {name}\(.*?\) \{{\n(.*?)^\}}", CHAT_JS, re.MULTILINE | re.DOTALL)
    assert match, f"function {name} not found in chat.js"
    return match.group(1)


def test_the_show_charges_starter_is_an_action_not_free_text():
    assert "ACTIONS.SHOW_CHARGES" in _function_body("starterButtons")


# -- Escalation card and client panel (usability-s2 DESIGN.md, direction B) ------


def test_the_escalation_copy_follows_the_design():
    es, pt = _entries("es"), _entries("pt")
    assert '"Número de caso"' in es["caseNumberLabel"] and '"Número do caso"' in pt["caseNumberLabel"]
    assert '"Le contactamos"' in es["escalationStepPending"]
    assert "En un plazo de hasta ${days} días hábiles" in es["escalationDeadline"]
    assert "Em até ${days} dias úteis" in pt["escalationDeadline"]
    assert '"Este chat ya no agrega información al caso."' in es["escalationNote"]
    assert '"Hecho:"' in es["stepDone"] and '"Pendiente:"' in es["stepPending"]


def test_the_timeline_never_says_today():
    for lang in ("es", "pt"):
        entries = _entries(lang)
        for key in ("escalationStepDone", "escalationStepPending", "escalationDeadline"):
            assert not re.search(r"\b(Hoy|Hoje)\b", entries[key]), (lang, key)


def test_card_and_panel_share_one_escalation_helper():
    assert "escalationDetailsHtml()" in _function_body("renderTurnActionCard")
    assert "escalationDetailsHtml()" in _function_body("renderPersonaView")
    assert CHAT_JS.count("function escalationDetailsHtml(") == 1
    helper = _function_body("escalationDetailsHtml")
    assert "caseStatus" not in helper and "handoff" not in helper
    # The charge line exists only when the charge is known: no placeholder.
    assert re.search(r"if \(esc\.charge\) html \+=", helper)
    assert re.search(r"if \(esc\.reason\) html \+=", helper)
    assert "time" in helper and "contact_business_days" in helper


def test_the_escalation_card_does_not_depend_on_the_case_refresh():
    render = _function_body("renderReply")
    assert "rememberEscalation(reply.escalation)" in render
    assert "reply.state === CASE_STATES.ESCALATED" in render
    # The panel falls back to the stored escalation when the refresh failed.
    assert "state.escalation ? CASE_STATES.ESCALATED" in _function_body("renderPanel")


def test_a_new_claim_clears_the_stored_escalation():
    assert "state.escalation = null;" in _function_body("startNewClaim")


def test_the_timeline_states_are_spoken_not_only_drawn():
    helper = _function_body("escalationDetailsHtml")
    assert 't("stepDone")' in helper and 't("stepPending")' in helper
    assert 'class="sr-only"' in _function_body("verifyStepHtml")


def test_only_two_new_css_rules_for_the_escalation():
    assert re.search(r"^\.case-id \{[^}]*tabular-nums", APP_CSS, re.MULTILINE)
    assert re.search(r"^\.action-card__timeline \{", APP_CSS, re.MULTILINE)


def _js_object_keys(lang: str, name: str) -> set[str]:
    block = re.search(rf"^    {name}: \{{\n(.*?)^    \}},$", chat_js_language_block(CHAT_JS, lang), re.MULTILINE | re.DOTALL)
    assert block, f"STRINGS.{lang}.{name} not found"
    return set(re.findall(r"(\w+): \"", block.group(1)))


def _handoff_field_keys() -> set[str]:
    from tests.test_handoff_shape import PRODUCERS

    keys: set[str] = set()
    for build in PRODUCERS.values():
        handoff = build()
        keys |= set(handoff.verified_facts) | set(handoff.customer_reported)
    return keys


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_every_handoff_field_has_a_label_in_both_languages(lang):
    legacy = {"reported_amount", "reported_date", "reported_merchant", "matched_transaction_id"}
    assert _handoff_field_keys() | legacy <= _js_object_keys(lang, "handoffFields")


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_coded_handoff_values_are_labelled_in_both_languages(lang):
    from app.llm import ConfirmationAnswer
    from app.policy import DisputeReason

    codes = {str(v) for v in DisputeReason} | {str(v) for v in ConfirmationAnswer}
    assert codes <= _js_object_keys(lang, "handoffValues")


def test_the_internal_view_never_prints_a_raw_field_key():
    render = CHAT_JS[CHAT_JS.index("function factListHtml"):CHAT_JS.index("function handoffSectionHtml")]
    assert "factLabel(key)" in render and "escapeHtml(key)" not in render
    assert "humanizeKey(key)" in CHAT_JS[CHAT_JS.index("function factLabel"):]


def test_the_internal_view_sections_follow_the_design_order():
    body = CHAT_JS[CHAT_JS.index("function handoffSections"):CHAT_JS.index("function renderHandoffCard")]
    order = ["handoffRequest", "handoffFacts", "handoffReported", "handoffLegacyFacts", "handoffPolicy",
             "handoffActions", "handoffEvidence", "handoffQuestions"]
    positions = [body.index(f'"{key}"') for key in order]
    assert positions == sorted(positions)
    assert "policy_reasons" not in body


def test_the_verification_tags_are_text_not_only_color():
    for lang, record, unverified in (("es", "Del registro", "Sin verificar"), ("pt", "Do registro", "Não verificado")):
        entries = _entries(lang)
        assert record in entries["handoffFactsTag"] and unverified in entries["handoffReportedTag"]


def test_the_internal_view_styles_use_only_existing_tokens():
    root = re.search(r":root \{(.*?)\}", APP_CSS, re.DOTALL).group(1)
    defined = set(re.findall(r"(--[\w-]+):", root))
    rules = APP_CSS[APP_CSS.index(".fact-list {"):APP_CSS.index(".action-log {")]
    assert set(re.findall(r"var\((--[\w-]+)\)", rules)) <= defined
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", rules)

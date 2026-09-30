"""Customer-facing copy (Spanish and Portuguese) and how amounts/dates are
written in it. These are the deterministic texts: the welcome message the chat
opens with (served to the frontend by `/api/me`), and every fallback used when
the LLM is unavailable or its reply fails a check in `app/state_machine.py`.
"""

from __future__ import annotations

import re
from datetime import date

from app.case_model import CaseState
from app.charge_search import ListFilter, iso_day, txn_day
from app.llm import Language
from app.transactions import TransactionCandidate

WELCOME = {
    Language.ES: (
        "Hola, soy el asistente de disputas de LATAM Bank. Te ayudo con cargos que no reconocés: "
        "te muestro tus últimos movimientos para que elijas el cargo, lo reviso contra la política "
        "del banco y, si corresponde, te aplico un crédito provisional en el momento. Si necesita "
        "más revisión, lo paso a una persona del equipo con todo el resumen.\n\n"
        "Contame qué cargo querés revisar (monto, fecha o comercio, lo que recuerdes) o tocá una opción."
    ),
    Language.PT: (
        "Olá, sou o assistente de contestações do LATAM Bank. Ajudo com cobranças que você não "
        "reconhece: mostro suas últimas movimentações para você escolher a cobrança, reviso conforme "
        "a política do banco e, se couber, aplico um crédito provisório na hora. Se precisar de mais "
        "análise, passo para uma pessoa da equipe com todo o resumo.\n\n"
        "Conte qual cobrança você quer revisar (valor, data ou comerciante, o que lembrar) ou toque "
        "em uma opção."
    ),
}

OUT_OF_SCOPE = {
    Language.ES: (
        "Por este canal solo puedo ayudarte con cargos que no reconocés. Si querés, contame qué "
        "cargo te llamó la atención o te muestro tus últimos movimientos."
    ),
    Language.PT: (
        "Por este canal só posso ajudar com cobranças que você não reconhece. Se quiser, me conte "
        "qual cobrança chamou sua atenção ou mostro suas últimas movimentações."
    ),
}

ASK_FOR_DETAILS = {
    Language.ES: (
        "Para ayudarte necesito el monto exacto y la fecha aproximada del cargo que no "
        "reconocés. ¿Me los podés compartir?"
    ),
    Language.PT: (
        "Para te ajudar preciso do valor exato e da data aproximada da cobrança que você não "
        "reconhece. Pode me informar?"
    ),
}

CHARGE_LIST = {
    Language.ES: {
        ListFilter.RECENT: "Te muestro abajo tus últimos cargos. Tocá el que no reconocés, o “No está en la lista” si no aparece.",
        ListFilter.FILTERED: "Te muestro abajo los cargos que coinciden con lo que me contaste. Tocá el que no reconocés, o “No está en la lista” si no aparece.",
        ListFilter.FALLBACK_RECENT: "No encontré cargos que coincidan con eso, así que te muestro abajo tus últimos cargos. Tocá el que no reconocés, o “No está en la lista” si no aparece.",
    },
    Language.PT: {
        ListFilter.RECENT: "Mostro abaixo suas últimas cobranças. Toque na que você não reconhece, ou em “Não está na lista” se ela não aparecer.",
        ListFilter.FILTERED: "Mostro abaixo as cobranças que batem com o que você contou. Toque na que você não reconhece, ou em “Não está na lista” se ela não aparecer.",
        ListFilter.FALLBACK_RECENT: "Não encontrei cobranças que batam com isso, então mostro abaixo suas últimas cobranças. Toque na que você não reconhece, ou em “Não está na lista” se ela não aparecer.",
    },
}

SELECTION_UNAVAILABLE = {
    Language.ES: "Esa opción ya no está disponible. Elegí uno de los cargos de la lista de abajo.",
    Language.PT: "Essa opção não está mais disponível. Escolha uma das cobranças da lista abaixo.",
}

CASE_MOVED_ON = {
    Language.ES: "Tu caso cambió mientras te respondía (quizás desde otra pestaña). Seguimos desde acá.",
    Language.PT: "Seu caso mudou enquanto eu respondia (talvez em outra aba). Seguimos daqui.",
}

ESCALATED = {
    Language.ES: (
        "Le paso tu caso a una persona del equipo de disputas, con todo lo que revisamos hasta "
        "acá. Se va a contactar con vos para seguir."
    ),
    Language.PT: (
        "Vou passar seu caso para uma pessoa da equipe de disputas, com tudo o que revisamos até "
        "aqui. Ela vai entrar em contato para seguir."
    ),
}

_RESOLVED = {
    Language.ES: (
        "Listo, tu caso quedó resuelto: se aplicó un crédito provisional por ese cargo. Tu número "
        "de referencia es {reference}."
    ),
    Language.PT: (
        "Pronto, seu caso foi resolvido: um crédito provisório foi aplicado por essa cobrança. Seu "
        "número de referência é {reference}."
    ),
}

_CONFIRMATION_QUESTION = {
    Language.ES: (
        "Encontré este cargo: {amount} en {merchant} el {date}. ¿Es ese el que no reconocés? Si "
        "me confirmás, avanzo con tu caso."
    ),
    Language.PT: (
        "Encontrei esta cobrança: {amount} em {merchant} no dia {date}. É essa a que você não "
        "reconhece? Se você confirmar, sigo com o seu caso."
    ),
}

_UNKNOWN_MERCHANT = {
    Language.ES: "un comercio sin nombre registrado",
    Language.PT: "um comerciante sem nome registrado",
}

_TERMINAL = {
    Language.ES: {
        CaseState.RESOLVED_AUTO: "Tu caso ya fue resuelto (referencia {reference}).",
        CaseState.ESCALATED: "Tu caso ya fue derivado a un agente humano; te van a contactar a la brevedad.",
    },
    Language.PT: {
        CaseState.RESOLVED_AUTO: "Seu caso já foi resolvido (referência {reference}).",
        CaseState.ESCALATED: "Seu caso já foi encaminhado a um agente humano; você será contatado em breve.",
    },
}

_MONTHS = {
    Language.ES: ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                  "septiembre", "octubre", "noviembre", "diciembre"),
    Language.PT: ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                  "setembro", "outubro", "novembro", "dezembro"),
}


def format_amount(amount: float, currency: str) -> str:
    # Spanish/Portuguese convention: "." for thousands, "," for decimals, and
    # no ",00" on whole amounts (COP charges almost never have cents).
    whole = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{currency} {whole.removesuffix(',00')}"


def format_day(day: date, language: Language) -> str:
    return f"{day.day} de {_MONTHS[language][day.month - 1]} de {day.year}"


def resolved(reference: str, language: Language) -> str:
    return _RESOLVED[language].format(reference=reference)


def terminal_case(state: CaseState, reference: str | None, language: Language) -> str:
    # The CURRENT request's language, not the case's: the customer may have
    # switched the ES/PT toggle after the case closed.
    return _TERMINAL[language][state].format(reference=reference)


def confirmation_question(matched: TransactionCandidate, language: Language) -> str:
    return _CONFIRMATION_QUESTION[language].format(
        amount=format_amount(matched.amount, matched.currency),
        merchant=matched.merchant_name or _UNKNOWN_MERCHANT[language],
        date=format_day(txn_day(matched), language),
    )


def names_the_facts(reply: str, matched: TransactionCandidate, language: Language) -> bool:
    """Whether a model-written confirmation question shows the customer the
    merchant, the exact amount and the date of the charge. Cents may be
    omitted only when they are zero ("38.500" for 38500.00), and the date may
    be written with the month name.
    """
    if matched.merchant_name and matched.merchant_name.lower() not in reply.lower():
        return False
    exact = re.sub(r"\D", "", f"{matched.amount:.2f}")
    accepted = {exact, exact[:-2]} if exact.endswith("00") else {exact}
    reply_digits = re.sub(r"(?<=\d)[.,\s](?=\d)", "", reply)
    if not any(re.search(rf"(?<!\d){a}(?!\d)", reply_digits) for a in accepted):
        return False
    if iso_day(matched) in reply:
        return True
    day = txn_day(matched)
    names_month = str(day.year) in reply or _MONTHS[language][day.month - 1] in reply.lower()
    return bool(re.search(rf"(?<!\d){day.day}(?!\d)", reply)) and names_month

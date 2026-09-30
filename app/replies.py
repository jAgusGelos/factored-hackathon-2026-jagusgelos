"""Customer-facing copy (Spanish and Portuguese) and how amounts/dates are
written in it. These are the deterministic texts: the welcome message the chat
opens with (served to the frontend by `/api/me`), the resolution message (always
this template, see `app/credit.py`), and every fallback used when
the LLM is unavailable or its reply fails a check in a conversation step
(`app/state_machine.py`, `app/explanation.py`, `app/credit.py`,
`app/case_turn.py`).
"""

from __future__ import annotations

import re
from datetime import date

from app.case_model import CaseState
from app.charge_search import ListFilter, iso_day, txn_day
from app.llm import Language
from app.policy import DisputeReason, MissingDetail
from app.transactions import TransactionCandidate

WELCOME = {
    Language.ES: (
        "Hola, soy el asistente de disputas de LATAM Bank. Le ayudo con cargos que no reconoce: "
        "le muestro sus últimos movimientos para que elija el cargo, lo reviso según la política "
        "del banco y, si corresponde, le aplico un crédito provisional en el momento. Si el caso "
        "necesita más revisión, lo derivo a una persona del equipo con todo el resumen.\n\n"
        "Cuénteme qué cargo quiere revisar (monto, fecha o comercio, lo que recuerde) o toque una opción."
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
        "Por este canal solo puedo ayudarle con cargos que no reconoce. Si lo desea, cuénteme qué "
        "cargo le llamó la atención o le muestro sus últimos movimientos."
    ),
    Language.PT: (
        "Por este canal só posso ajudar com cobranças que você não reconhece. Se quiser, me conte "
        "qual cobrança chamou sua atenção ou mostro suas últimas movimentações."
    ),
}

ASK_FOR_DETAILS = {
    Language.ES: (
        "Para ayudarle necesito el monto exacto y la fecha aproximada del cargo que no "
        "reconoce. ¿Me los puede indicar?"
    ),
    Language.PT: (
        "Para ajudar, preciso do valor exato e da data aproximada da cobrança que você não "
        "reconhece. Pode me informar?"
    ),
}

CHARGE_LIST = {
    Language.ES: {
        ListFilter.RECENT: "Abajo tiene sus últimos cargos. Toque el que no reconoce, o “No está en la lista” si no aparece.",
        ListFilter.FILTERED: "Abajo tiene los cargos que coinciden con lo que me contó. Toque el que no reconoce, o “No está en la lista” si no aparece.",
        ListFilter.FALLBACK_RECENT: "No encontré cargos que coincidan con eso, así que abajo tiene sus últimos cargos. Toque el que no reconoce, o “No está en la lista” si no aparece.",
    },
    Language.PT: {
        ListFilter.RECENT: "Mostro abaixo suas últimas cobranças. Toque na que você não reconhece, ou em “Não está na lista” se ela não aparecer.",
        ListFilter.FILTERED: "Mostro abaixo as cobranças que batem com o que você contou. Toque na que você não reconhece, ou em “Não está na lista” se ela não aparecer.",
        ListFilter.FALLBACK_RECENT: "Não encontrei cobranças que batam com isso, então mostro abaixo suas últimas cobranças. Toque na que você não reconhece, ou em “Não está na lista” se ela não aparecer.",
    },
}

SELECTION_UNAVAILABLE = {
    Language.ES: "Esa opción ya no está disponible. Elija uno de los cargos de la lista de abajo.",
    Language.PT: "Essa opção não está mais disponível. Escolha uma das cobranças da lista abaixo.",
}

# A quick-reply tapped after the conversation moved past it (an old "Sí, es
# ese" or "No está en la lista"): it changes nothing.
ACTION_UNAVAILABLE = {
    Language.ES: "Esa opción ya no está disponible. Puede continuar desde el último mensaje.",
    Language.PT: "Essa opção não está mais disponível. Você pode continuar a partir da última mensagem.",
}

HUMAN_DEFERRED = {
    Language.ES: (
        "Antes de derivarlo, intentemos ubicar el cargo, que suele ser mucho más rápido: toque el "
        "cargo que no reconoce o indíqueme monto, fecha o comercio."
    ),
    Language.PT: (
        "Antes de encaminhar, vamos tentar localizar a cobrança, o que costuma ser bem mais rápido: "
        "toque na cobrança que você não reconhece ou informe valor, data ou comerciante."
    ),
}

HUMAN_DEFERRED_WHILE_CONFIRMING = {
    Language.ES: (
        "Antes de derivarlo, confirmemos el cargo, que es más rápido: ¿es ese el cargo que no "
        "reconoce? Si no es, indíquelo y lo buscamos."
    ),
    Language.PT: (
        "Antes de encaminhar, vamos confirmar a cobrança, o que é mais rápido: é essa a cobrança "
        "que você não reconhece? Se não for, diga que não e procuramos outra."
    ),
}

ASK_FOR_EXPLANATION = {
    Language.ES: (
        "Ya ubiqué el cargo: {charge}. Cuénteme con sus palabras qué pasó: cómo se dio cuenta, si "
        "reconoce el comercio, si tiene la tarjeta, si pagó algo y no lo recibió. Con eso "
        "decido si puedo reintegrarlo ahora."
    ),
    Language.PT: (
        "Já localizei a cobrança: {charge}. Me conte com suas palavras o que aconteceu: como você "
        "percebeu, se reconhece o comerciante, se está com o cartão, se pagou algo e não recebeu. "
        "Com isso decido se posso reembolsar agora."
    ),
}

_EXPLANATION_FOLLOWUP = {
    Language.ES: {
        None: (
            "Gracias. Para poder decidir necesito un detalle más concreto: por ejemplo cómo se dio "
            "cuenta del cargo, si tiene la tarjeta consigo o si recibió lo que pagó."
        ),
        MissingDetail.HOW_NOTICED: "Gracias. Para poder decidir necesito un detalle más: ¿cómo se dio cuenta de este cargo?",
        MissingDetail.CARD_POSSESSION: (
            "Gracias. Para poder decidir necesito un detalle más: ¿tiene la tarjeta consigo en este momento?"
        ),
        MissingDetail.MERCHANT_KNOWN: (
            "Gracias. Para poder decidir necesito un detalle más: ¿conoce este comercio o lo usó alguna vez?"
        ),
        MissingDetail.ITEM_RECEIVED: "Gracias. Para poder decidir necesito un detalle más: ¿recibió lo que pagó con este cargo?",
    },
    Language.PT: {
        None: (
            "Obrigado. Para decidir preciso de um detalhe mais concreto: por exemplo como você percebeu "
            "a cobrança, se está com o cartão ou se recebeu o que pagou."
        ),
        MissingDetail.HOW_NOTICED: "Obrigado. Para decidir preciso de mais um detalhe: como você percebeu esta cobrança?",
        MissingDetail.CARD_POSSESSION: "Obrigado. Para decidir preciso de mais um detalhe: você está com o cartão neste momento?",
        MissingDetail.MERCHANT_KNOWN: (
            "Obrigado. Para decidir preciso de mais um detalhe: você conhece este comerciante ou já o usou alguma vez?"
        ),
        MissingDetail.ITEM_RECEIVED: "Obrigado. Para decidir preciso de mais um detalhe: você recebeu o que pagou com esta cobrança?",
    },
}

HUMAN_DEFERRED_WHILE_EXPLAINING = {
    Language.ES: (
        "Antes de derivarlo, intentemos resolverlo, que es más rápido: cuénteme qué pasó con ese "
        "cargo y lo reviso ahora mismo."
    ),
    Language.PT: (
        "Antes de encaminhar, vamos tentar resolver, o que é mais rápido: conte o que aconteceu "
        "com essa cobrança e eu reviso agora mesmo."
    ),
}

ASK_FOR_ONE_DETAIL = {
    Language.ES: "Indíqueme un dato más del cargo (monto aproximado, fecha o comercio) y lo busco.",
    Language.PT: "Informe mais um dado da cobrança (valor aproximado, data ou comerciante) e eu procuro.",
}

CASE_MOVED_ON = {
    Language.ES: "Su caso cambió mientras le respondía (quizás desde otra pestaña). Seguimos desde aquí.",
    Language.PT: "Seu caso mudou enquanto eu respondia (talvez em outra aba). Seguimos daqui.",
}

ESCALATED = {
    Language.ES: (
        "Derivé su caso a una persona del equipo de disputas, con todo lo que revisamos hasta "
        "aquí. Le contactará para continuar."
    ),
    Language.PT: (
        "Vou passar seu caso para uma pessoa da equipe de disputas, com tudo o que revisamos até "
        "aqui. Ela vai entrar em contato para seguir."
    ),
}

_RESOLVED = {
    Language.ES: {
        DisputeReason.UNRECOGNIZED: (
            "Listo: le aplicamos un crédito provisional por ese cargo. Por seguridad bloqueamos su "
            "tarjeta. El equipo revisa el caso y, si el cargo resultara suyo, el crédito se revierte. "
            "Su número de referencia es {reference}."
        ),
        DisputeReason.DUPLICATE: (
            "Listo: confirmamos que el cargo estaba duplicado y le devolvimos uno de los dos. Su "
            "número de referencia es {reference}."
        ),
    },
    Language.PT: {
        DisputeReason.UNRECOGNIZED: (
            "Pronto: aplicamos um crédito provisório por essa cobrança. Por segurança bloqueamos seu "
            "cartão. A equipe analisa o caso e, se a cobrança for sua, o crédito é revertido. Seu "
            "número de referência é {reference}."
        ),
        DisputeReason.DUPLICATE: (
            "Pronto: confirmamos que a cobrança estava duplicada e devolvemos uma das duas. Seu "
            "número de referência é {reference}."
        ),
    },
}

_CONFIRMATION_QUESTION = {
    Language.ES: (
        "Encontré este cargo: {amount} en {merchant} el {date}. ¿Es ese el que no reconoce? Si "
        "me lo confirma, avanzo con su caso."
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
        CaseState.RESOLVED_AUTO: "Su caso ya fue resuelto (referencia {reference}).",
        CaseState.ESCALATED: "Su caso ya fue derivado a una persona del equipo, que le contactará.",
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


def resolved(reference: str, reason: DisputeReason, language: Language) -> str:
    return _RESOLVED[language][reason].format(reference=reference)


def terminal_case(state: CaseState, reference: str | None, language: Language) -> str:
    # The CURRENT request's language, not the case's: the customer may have
    # switched the ES/PT toggle after the case closed.
    return _TERMINAL[language][state].format(reference=reference)


def charge_summary(matched: TransactionCandidate, language: Language) -> str:
    merchant = matched.merchant_name or _UNKNOWN_MERCHANT[language]
    return f"{merchant}, {format_amount(matched.amount, matched.currency)}, {format_day(txn_day(matched), language)}"


def ask_for_explanation(matched: TransactionCandidate, language: Language) -> str:
    return ASK_FOR_EXPLANATION[language].format(charge=charge_summary(matched, language))


def explanation_followup(missing_detail: MissingDetail | None, language: Language) -> str:
    return _EXPLANATION_FOLLOWUP[language][missing_detail]


def confirmation_question(matched: TransactionCandidate, language: Language) -> str:
    return _CONFIRMATION_QUESTION[language].format(
        amount=format_amount(matched.amount, matched.currency),
        merchant=matched.merchant_name or _UNKNOWN_MERCHANT[language],
        date=format_day(txn_day(matched), language),
    )


def names_the_facts(reply: str, matched: TransactionCandidate, language: Language) -> bool:
    """Whether a model-written confirmation question (or explanation request)
    shows the customer the merchant, the exact amount and the date of the charge. Cents may be
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

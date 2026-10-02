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
from typing import TypedDict

from app.case_model import CaseState, EscalationReason
from app.charge_search import ChargeOption, ListFilter, charge_option, iso_day, txn_day
from app.llm import Language
from app.policy import (
    ESCALATION_CONTACT_BUSINESS_DAYS,
    DisputeReason,
    MissingDetail,
    StatementField,
)
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

# Appended to a reply after the customer's first request for a person
# (`case_turn.Turn.reply`): the agent tried once, the second request escalates.
HUMAN_OFFER = {
    Language.ES: "Si aun así prefiere hablar con una persona, vuelva a pedirlo o use el botón «Hablar con una persona».",
    Language.PT: "Se ainda assim preferir falar com uma pessoa, peça de novo ou use o botão «Falar com uma pessoa».",
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

# The statement step (`app/statement.py`): asked before an escalation that is
# already decided, so it never promises the account changes the outcome.
ASK_FOR_STATEMENT = {
    Language.ES: (
        "Antes de derivar su caso, cuénteme qué pasó y por qué solicita la devolución. La persona "
        "que lo revise usará esta información."
    ),
    Language.PT: (
        "Antes de encaminhar seu caso, conte o que aconteceu e por que solicita o reembolso. A "
        "pessoa que for revisar vai usar essas informações."
    ),
}

# The statement step's one follow-up: only the fact still missing (None: a
# statement too short to tell what happened).
_STATEMENT_FOLLOWUP = {
    Language.ES: {
        StatementField.CARD_POSSESSION: (
            "Gracias. Para que la persona que revise el caso tenga el contexto, indíqueme si tiene la "
            "tarjeta consigo en este momento."
        ),
        StatementField.MERCHANT_KNOWN: (
            "Gracias. Para que la persona que revise el caso tenga el contexto, indíqueme si conoce este "
            "comercio o si hizo usted esta compra."
        ),
        StatementField.HOW_NOTICED: (
            "Gracias. Para que la persona que revise el caso tenga el contexto, indíqueme cómo y cuándo "
            "se dio cuenta del cargo."
        ),
        None: (
            "Gracias. Para que la persona que revise el caso tenga el contexto, indíqueme un poco más de "
            "lo que pasó."
        ),
    },
    Language.PT: {
        StatementField.CARD_POSSESSION: (
            "Obrigado. Para que a pessoa que revisar o caso tenha o contexto, informe se está com o "
            "cartão neste momento."
        ),
        StatementField.MERCHANT_KNOWN: (
            "Obrigado. Para que a pessoa que revisar o caso tenha o contexto, informe se conhece este "
            "comerciante ou se fez esta compra."
        ),
        StatementField.HOW_NOTICED: (
            "Obrigado. Para que a pessoa que revisar o caso tenha o contexto, informe como e quando "
            "percebeu a cobrança."
        ),
        None: (
            "Obrigado. Para que a pessoa que revisar o caso tenha o contexto, conte um pouco mais do "
            "que aconteceu."
        ),
    },
}

STATEMENT_INSIST = {
    Language.ES: (
        "Entiendo. Esta información la va a usar el asesor que revise su caso, y con ella puede "
        "avanzar sin volver a contactarle. ¿Me cuenta brevemente qué pasó con este cargo?"
    ),
    Language.PT: (
        "Entendo. Essas informações vão ser usadas pela pessoa que for revisar seu caso, e com elas "
        "ela pode avançar sem precisar entrar em contato de novo. Pode me contar brevemente o que "
        "aconteceu com essa cobrança?"
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

class EscalationNotice(TypedDict):
    """What the customer is told about a case that went to a person, for the
    chat's card and client panel (`ChatReply.escalation`). The same values
    are in the notice text, so the message and the card cannot disagree.
    """

    case_number: str
    # Only a charge the customer identified (picked, named, confirmed): never
    # an unconfirmed proposal (plan.md AD-5).
    charge: ChargeOption | None
    # Localized; None for a case escalated before the reason was stored.
    reason: str | None
    contact_business_days: int


# The reason sentence of the escalation notice, one per EscalationReason. No
# threshold, score or rule name: every policy outcome is NEEDS_REVIEW.
_ESCALATION_REASON = {
    Language.ES: {
        EscalationReason.HUMAN_REQUESTED: "usted pidió hablar con una persona",
        EscalationReason.CHARGE_NOT_IDENTIFIED: "no pudimos identificar el cargo con los datos disponibles",
        EscalationReason.NEEDS_REVIEW: "el cargo necesita la revisión de una persona antes de cualquier reintegro",
        EscalationReason.NOT_RECEIVED: (
            "usted indicó que no recibió lo que pagó, y ese reclamo se gestiona con el comercio"
        ),
        EscalationReason.WRONG_AMOUNT: "usted indicó que el monto no es el correcto, y hay que determinar el monto real",
        EscalationReason.CARD_LOST_STOLEN: (
            "usted indicó que perdió la tarjeta o se la robaron, y una persona revisa sus movimientos recientes"
        ),
        EscalationReason.ALREADY_CREDITED: "ese cargo ya tuvo un crédito en otro caso",
        EscalationReason.ALREADY_IN_REVIEW: "ese cargo ya se está revisando en otro caso",
        EscalationReason.SERVICE_ISSUE: "tuvimos un problema técnico al procesar su solicitud",
    },
    Language.PT: {
        EscalationReason.HUMAN_REQUESTED: "você pediu para falar com uma pessoa",
        EscalationReason.CHARGE_NOT_IDENTIFIED: "não conseguimos identificar a cobrança com os dados disponíveis",
        EscalationReason.NEEDS_REVIEW: "a cobrança precisa da análise de uma pessoa antes de qualquer reembolso",
        EscalationReason.NOT_RECEIVED: (
            "você informou que não recebeu o que pagou, e essa contestação é tratada com o comerciante"
        ),
        EscalationReason.WRONG_AMOUNT: "você informou que o valor não está correto, e é preciso definir o valor real",
        EscalationReason.CARD_LOST_STOLEN: (
            "você informou que perdeu o cartão ou que ele foi roubado, e uma pessoa analisa suas "
            "movimentações recentes"
        ),
        EscalationReason.ALREADY_CREDITED: "essa cobrança já teve um crédito em outro caso",
        EscalationReason.ALREADY_IN_REVIEW: "essa cobrança já está em análise em outro caso",
        EscalationReason.SERVICE_ISSUE: "tivemos um problema técnico ao processar sua solicitação",
    },
}

_ESCALATION_NOTICE = {
    Language.ES: (
        "Derivé su caso a una persona del equipo de disputas.{charge} Motivo: {reason}. Su número "
        "de caso es {case_number}. Le contactaremos en un plazo de hasta {days} días hábiles. Este "
        "chat ya no agrega información al caso: si tiene algo más para contar, podrá hacerlo cuando "
        "le contacten."
    ),
    Language.PT: (
        "Encaminhei seu caso para uma pessoa da equipe de contestações.{charge} Motivo: {reason}. O "
        "número do seu caso é {case_number}. Entraremos em contato em até {days} dias úteis. Este "
        "chat não adiciona mais informações ao caso: se tiver algo mais a contar, poderá fazer isso "
        "quando entrarmos em contato."
    ),
}

_ESCALATION_CHARGE = {
    Language.ES: " El cargo es {charge}.",
    Language.PT: " A cobrança é {charge}.",
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
        CaseState.ESCALATED: (
            "Su caso {case_number} ya fue derivado a una persona del equipo, que le contactará en un "
            "plazo de hasta {days} días hábiles desde la derivación."
        ),
    },
    Language.PT: {
        CaseState.RESOLVED_AUTO: "Seu caso já foi resolvido (referência {reference}).",
        CaseState.ESCALATED: (
            "Seu caso {case_number} já foi encaminhado a uma pessoa da equipe, que entrará em contato "
            "em até {days} dias úteis a partir do encaminhamento."
        ),
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


def terminal_case(state: CaseState, *, case_number: str, reference: str | None, language: Language) -> str:
    # The CURRENT request's language, not the case's: the customer may have
    # switched the ES/PT toggle after the case closed.
    return _TERMINAL[language][state].format(
        reference=reference, case_number=case_number, days=ESCALATION_CONTACT_BUSINESS_DAYS,
    )


def escalation_summary(
    case_number: str, reason: EscalationReason | None, *, charge: TransactionCandidate | None, language: Language,
) -> EscalationNotice:
    return {
        "case_number": case_number,
        "charge": charge_option(charge) if charge is not None else None,
        "reason": _ESCALATION_REASON[language][reason] if reason is not None else None,
        "contact_business_days": ESCALATION_CONTACT_BUSINESS_DAYS,
    }


def escalation_notice(
    case_number: str, reason: EscalationReason, *, charge: TransactionCandidate | None, language: Language,
) -> tuple[str, EscalationNotice]:
    """The message a case gets when it goes to a person, always this template
    (never the model): the charge when the customer identified it, the reason,
    the case number and the contact deadline.
    """
    notice = escalation_summary(case_number, reason, charge=charge, language=language)
    text = _ESCALATION_NOTICE[language].format(
        charge=_ESCALATION_CHARGE[language].format(charge=charge_summary(charge, language)) if charge is not None else "",
        reason=notice["reason"], case_number=case_number, days=notice["contact_business_days"],
    )
    return text, notice


def charge_summary(matched: TransactionCandidate, language: Language) -> str:
    merchant = matched.merchant_name or _UNKNOWN_MERCHANT[language]
    return f"{merchant}, {format_amount(matched.amount, matched.currency)}, {format_day(txn_day(matched), language)}"


def ask_for_explanation(matched: TransactionCandidate, language: Language) -> str:
    return ASK_FOR_EXPLANATION[language].format(charge=charge_summary(matched, language))


def explanation_followup(missing_detail: MissingDetail | None, language: Language) -> str:
    return _EXPLANATION_FOLLOWUP[language][missing_detail]


def statement_followup(fact: StatementField | None, language: Language) -> str:
    return _STATEMENT_FOLLOWUP[language][fact]


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

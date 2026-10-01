// Renders purely from the state machine's API responses (/api/chat, /api/case/{id}),
// never from hardcoded demo content.

const STRINGS = {
  es: {
    htmlLang: "es",
    timeLocale: "es-AR",
    starterShowCharges: "Ver mis últimos cargos",
    quickHuman: "Hablar con una persona",
    quickNotInList: "No está en la lista",
    quickYes: "Sí, es ese",
    quickNo: "No es ese",
    chargeListLabel: "Sus movimientos",
    unknownMerchant: "Comercio sin nombre",
    badgeSelecting: "Elija el cargo",
    stepTransactionSelecting: "Mostrando sus movimientos para que elija",
    categories: { Food: "Comida", Transport: "Transporte", Services: "Servicios", Entertainment: "Entretenimiento", Health: "Salud", Other: "Otros" },
    messageLabel: "Mensaje",
    inputPlaceholder: "Escriba su mensaje...",
    send: "Enviar",
    logout: "Salir",
    panelTitle: "Ficha del caso",
    stepIdentity: "Identidad verificada",
    stepIdentityDetail: "Sesión de demostración activa",
    stepTransaction: "Transacción localizada",
    stepTransactionPending: "Esperando el reporte del cliente",
    stepTransactionSearching: "Buscando transacciones candidatas…",
    stepTransactionNotIdentified: "No se pudo identificar el cargo; lo revisa una persona",
    stepTransactionFound: (charge) => `Cargo identificado: ${charge}`,
    stepPolicy: "Política evaluada",
    stepPolicyPending: "Pendiente",
    stepPolicyResolved: "Resolución automática aplicada",
    stepPolicyEscalated: "Derivado a un agente humano",
    badgePending: "Esperando reporte",
    badgeClarifying: "Necesita aclaración",
    badgeConfirming: "Esperando su confirmación",
    stepTransactionAwaitingConfirm: "Cargo encontrado, pendiente de su confirmación",
    badgeExplaining: "Esperando su explicación",
    stepPolicyExplaining: "Falta que nos cuente qué pasó con el cargo",
    badgeStatement: "Esperando su relato",
    stepPolicyStatement: "Antes de derivar, falta que nos cuente qué pasó",
    badgeResolved: "Resuelto",
    badgeEscalated: "Escalado",
    verifiedChip: (ref) => `✓ Referencia ${ref}`,
    actionCardResolvedTitle: "Verificación del sistema",
    actionCardResolvedBody: (amount, ref) =>
      `Cargo confirmado (${amount}). Crédito provisional simulado registrado (referencia ${ref}).`,
    actionCardEscalatedTitle: "Caso derivado",
    actionCardEscalatedBody: "El caso fue derivado a un agente humano con el resumen estructurado del reclamo.",
    // Escalation card and client panel (usability-s2 DESIGN.md, direction B).
    caseNumberLabel: "Número de caso",
    escalationStepDone: "Caso derivado a una persona del equipo",
    escalationStepPending: "Le contactamos",
    escalationDeadline: (days) => `En un plazo de hasta ${days} días hábiles`,
    stepDone: "Hecho:",
    stepPending: "Pendiente:",
    escalationCharge: (charge) => `Cargo: ${charge}`,
    escalationReason: (reason) => `Motivo: ${reason}`,
    escalationNote: "Este chat ya no agrega información al caso.",
    personaClient: "Vista Cliente",
    personaInternal: "Vista Interna",
    handoffTitle: "Resumen para el agente humano",
    handoffRequest: "Pedido del cliente",
    handoffFacts: "Hechos verificados",
    handoffFactsTag: "Del registro",
    handoffReported: "Lo que dijo el cliente",
    handoffReportedTag: "Sin verificar",
    handoffLegacyFacts: "Datos del caso",
    handoffPolicy: "Motivos de política",
    handoffPolicyCount: (n) => (n === 1 ? "1 motivo registrado en el expediente interno." : `${n} motivos registrados en el expediente interno.`),
    handoffActions: "Acciones realizadas",
    handoffEvidence: "Evidencia",
    handoffQuestions: "Preguntas abiertas",
    handoffFields: {
      transaction_id: "Transacción", merchant: "Comercio", amount: "Monto", currency: "Moneda", date: "Fecha",
      channel: "Canal", status: "Estado", category: "Categoría", charge_confirmed: "Confirmado por el cliente",
      credited_in_case: "Ya acreditado en el caso", prior_case: "Caso anterior", candidate_count: "Cargos candidatos",
      charges_shown: "Cargos mostrados", dispute_reason: "Motivo de la disputa",
      customer_confirmation: "Respuesta a la confirmación", explanation_summary: "Explicación (resumen del modelo)",
      explanation_specific: "Explicación concreta", explanation_consistent: "Explicación coherente",
      explanation_assessment: "Evaluación de la explicación", customer_message: "Mensaje del cliente",
      reported_amount: "Monto reportado", reported_date: "Fecha reportada", reported_merchant: "Comercio reportado",
      matched_transaction_id: "Transacción identificada",
      statement_status: "Relato del cliente", statement_summary: "Relato (resumen del modelo)",
      denies_purchase: "Niega haber hecho la compra", merchant_known: "Conoce el comercio",
      card_possession: "Tiene la tarjeta consigo", how_noticed: "Cómo lo notó", noticed_on: "Cuándo lo notó",
      other_suspicious_activity: "Otros movimientos que no reconoce",
    },
    handoffValues: {
      unrecognized: "No reconoce el cargo", duplicate: "Cargo duplicado", not_received: "No recibió el producto",
      wrong_amount: "Monto incorrecto", card_lost_stolen: "Tarjeta perdida o robada", unclear: "No está claro",
      yes: "Sí", no: "No", human: "Pidió una persona", sí: "Sí",
      given: "Contado", declined: "Prefirió no contarlo", summary_unavailable: "Resumen no disponible",
      app_alert: "Alerta o app del banco", statement: "Resumen de cuenta", sms_or_email: "SMS o correo",
      other: "Otro",
    },
    sessionLoadError: "No se pudo cargar la sesión.",
    logoutFailed: "No se pudo cerrar sesión.",
    // Wait indicator, retry and new claim (usability-s1 DESIGN.md copy table).
    waitGeneric: "El asistente está respondiendo…",
    waitSearching: "Buscando sus movimientos…",
    waitConfirming: "Revisando el cargo…",
    waitExplanation: "Revisando su explicación…",
    waitStatement: "Registrando su relato…",
    waitSlow: "Está tardando más de lo habitual. Seguimos procesando su mensaje.",
    turnError: "No se pudo obtener respuesta. Puede reintentar el envío.",
    retry: "Reintentar",
    quickNewClaim: "Reportar otro cargo",
    claimResolved: "resuelto",
    claimEscalated: "derivado",
    claimDivider: (ref, outcome) => `Nuevo reclamo · caso anterior ${ref ? `${ref} ` : ""}(${outcome})`,
  },
  pt: {
    htmlLang: "pt-BR",
    timeLocale: "pt-BR",
    starterShowCharges: "Ver minhas últimas cobranças",
    quickHuman: "Falar com uma pessoa",
    quickNotInList: "Não está na lista",
    quickYes: "Sim, é essa",
    quickNo: "Não é essa",
    chargeListLabel: "Suas movimentações",
    unknownMerchant: "Comerciante sem nome",
    badgeSelecting: "Escolha a cobrança",
    stepTransactionSelecting: "Mostrando suas movimentações para você escolher",
    categories: { Food: "Alimentação", Transport: "Transporte", Services: "Serviços", Entertainment: "Entretenimento", Health: "Saúde", Other: "Outros" },
    messageLabel: "Mensagem",
    inputPlaceholder: "Escreva sua mensagem...",
    send: "Enviar",
    logout: "Sair",
    panelTitle: "Ficha do caso",
    stepIdentity: "Identidade verificada",
    stepIdentityDetail: "Sessão de demonstração ativa",
    stepTransaction: "Transação localizada",
    stepTransactionPending: "Aguardando o relato do cliente",
    stepTransactionSearching: "Buscando transações candidatas…",
    stepTransactionNotIdentified: "Não foi possível identificar a cobrança; uma pessoa vai revisar",
    stepTransactionFound: (charge) => `Cobrança identificada: ${charge}`,
    stepPolicy: "Política avaliada",
    stepPolicyPending: "Pendente",
    stepPolicyResolved: "Resolução automática aplicada",
    stepPolicyEscalated: "Encaminhado a um agente humano",
    badgePending: "Aguardando relato",
    badgeClarifying: "Precisa de esclarecimento",
    badgeConfirming: "Aguardando sua confirmação",
    stepTransactionAwaitingConfirm: "Cobrança encontrada, aguardando sua confirmação",
    badgeExplaining: "Aguardando sua explicação",
    stepPolicyExplaining: "Falta você contar o que aconteceu com a cobrança",
    badgeStatement: "Aguardando seu relato",
    stepPolicyStatement: "Antes de encaminhar, falta você contar o que aconteceu",
    badgeResolved: "Resolvido",
    badgeEscalated: "Escalado",
    verifiedChip: (ref) => `✓ Referência ${ref}`,
    actionCardResolvedTitle: "Verificação do sistema",
    actionCardResolvedBody: (amount, ref) =>
      `Cobrança confirmada (${amount}). Crédito provisório simulado registrado (referência ${ref}).`,
    actionCardEscalatedTitle: "Caso encaminhado",
    actionCardEscalatedBody: "O caso foi encaminhado a um agente humano com o resumo estruturado da reclamação.",
    caseNumberLabel: "Número do caso",
    escalationStepDone: "Caso encaminhado a uma pessoa da equipe",
    escalationStepPending: "Entraremos em contato",
    escalationDeadline: (days) => `Em até ${days} dias úteis`,
    stepDone: "Feito:",
    stepPending: "Pendente:",
    escalationCharge: (charge) => `Cobrança: ${charge}`,
    escalationReason: (reason) => `Motivo: ${reason}`,
    escalationNote: "Este chat não adiciona mais informações ao caso.",
    personaClient: "Vista Cliente",
    personaInternal: "Vista Interna",
    handoffTitle: "Resumo para o agente humano",
    handoffRequest: "Pedido do cliente",
    handoffFacts: "Fatos verificados",
    handoffFactsTag: "Do registro",
    handoffReported: "O que o cliente disse",
    handoffReportedTag: "Não verificado",
    handoffLegacyFacts: "Dados do caso",
    handoffPolicy: "Motivos de política",
    handoffPolicyCount: (n) => (n === 1 ? "1 motivo registrado no dossiê interno." : `${n} motivos registrados no dossiê interno.`),
    handoffActions: "Ações realizadas",
    handoffEvidence: "Evidências",
    handoffQuestions: "Perguntas em aberto",
    handoffFields: {
      transaction_id: "Transação", merchant: "Estabelecimento", amount: "Valor", currency: "Moeda", date: "Data",
      channel: "Canal", status: "Status", category: "Categoria", charge_confirmed: "Confirmada pelo cliente",
      credited_in_case: "Já creditada no caso", prior_case: "Caso anterior", candidate_count: "Cobranças candidatas",
      charges_shown: "Cobranças mostradas", dispute_reason: "Motivo da contestação",
      customer_confirmation: "Resposta à confirmação", explanation_summary: "Explicação (resumo do modelo)",
      explanation_specific: "Explicação concreta", explanation_consistent: "Explicação coerente",
      explanation_assessment: "Avaliação da explicação", customer_message: "Mensagem do cliente",
      reported_amount: "Valor informado", reported_date: "Data informada", reported_merchant: "Estabelecimento informado",
      matched_transaction_id: "Transação identificada",
      statement_status: "Relato do cliente", statement_summary: "Relato (resumo do modelo)",
      denies_purchase: "Nega ter feito a compra", merchant_known: "Conhece o estabelecimento",
      card_possession: "Está com o cartão", how_noticed: "Como percebeu", noticed_on: "Quando percebeu",
      other_suspicious_activity: "Outras movimentações que não reconhece",
    },
    handoffValues: {
      unrecognized: "Não reconhece a cobrança", duplicate: "Cobrança duplicada", not_received: "Não recebeu o produto",
      wrong_amount: "Valor incorreto", card_lost_stolen: "Cartão perdido ou roubado", unclear: "Não está claro",
      yes: "Sim", no: "Não", human: "Pediu uma pessoa", sí: "Sim",
      given: "Contado", declined: "Preferiu não contar", summary_unavailable: "Resumo indisponível",
      app_alert: "Alerta ou app do banco", statement: "Extrato", sms_or_email: "SMS ou e-mail",
      other: "Outro",
    },
    sessionLoadError: "Não foi possível carregar a sessão.",
    logoutFailed: "Não foi possível encerrar a sessão.",
    waitGeneric: "O assistente está respondendo…",
    waitSearching: "Buscando suas movimentações…",
    waitConfirming: "Verificando a cobrança…",
    waitExplanation: "Analisando sua explicação…",
    waitStatement: "Registrando seu relato…",
    waitSlow: "Está demorando mais que o normal. Continuamos processando sua mensagem.",
    turnError: "Não foi possível obter resposta. Você pode tentar enviar novamente.",
    retry: "Tentar novamente",
    quickNewClaim: "Contestar outra cobrança",
    claimResolved: "resolvido",
    claimEscalated: "encaminhado",
    claimDivider: (ref, outcome) => `Nova reclamação · caso anterior ${ref ? `${ref} ` : ""}(${outcome})`,
  },
};

// Must match app/case_model.py::CustomerAction.
const ACTIONS = Object.freeze({
  HUMAN: "human",
  NONE_OF_THESE: "none_of_these",
  CONFIRM_YES: "confirm_yes",
  CONFIRM_NO: "confirm_no",
  SHOW_CHARGES: "show_charges",
});

const state = {
  welcome: null, // {es, pt}, from /api/me
  language: "es",
  caseId: null,
  caseStatus: null,
  caseState: null, // the state from the last delivered reply (null = no case yet)
  closedCase: null, // {state, reference} of a terminal case until a new claim starts
  // reply.escalation of the escalated case plus the time it arrived; the card
  // and the client panel render from it, never from /api/case.
  escalation: null,
  personaView: "client", // "client" | "internal"
  busy: false,
};

// Must match app/case_model.py::CaseState.
const CASE_STATES = Object.freeze({
  AWAITING_REPORT: "awaiting_report",
  CLARIFYING: "clarifying",
  SELECTING: "selecting",
  CONFIRMING: "confirming",
  AWAITING_EXPLANATION: "awaiting_explanation",
  AWAITING_STATEMENT: "awaiting_statement",
  RESOLVED_AUTO: "resolved_auto",
  ESCALATED: "escalated",
});

const TERMINAL_STATES = new Set([CASE_STATES.RESOLVED_AUTO, CASE_STATES.ESCALATED]);

// What the typing indicator says while a turn from each state is in flight.
const WAIT_CAPTION_KEYS = Object.freeze({
  [CASE_STATES.AWAITING_REPORT]: "waitSearching",
  [CASE_STATES.SELECTING]: "waitSearching",
  [CASE_STATES.CLARIFYING]: "waitSearching",
  [CASE_STATES.CONFIRMING]: "waitConfirming",
  [CASE_STATES.AWAITING_EXPLANATION]: "waitExplanation",
  [CASE_STATES.AWAITING_STATEMENT]: "waitStatement",
});

const TURN_OUTCOMES = Object.freeze({
  DELIVERED: "delivered",
  IN_PROGRESS: "in_progress",
  REJECTED: "rejected",
  SESSION_EXPIRED: "session_expired",
  FAILED: "failed",
});

// app/main.py answers 409 while the same turn_id is still being processed.
const HTTP_TURN_IN_PROGRESS = 409;
const HTTP_UNAUTHORIZED = 401;
// The server's per-turn model budget is 20 s (AD-5); the client gives up a bit later.
const TURN_TIMEOUT_MS = 25000;
const SLOW_TURN_MS = 10000;

let slowTimer = null;

const chatLog = document.getElementById("chat-log");
const chatStatus = document.getElementById("chat-status");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
const messageLabel = document.getElementById("message-label");
const sendBtn = document.getElementById("send-btn");
const logoutBtn = document.getElementById("logout-btn");
const panelContent = document.getElementById("panel-content");
const panelTitle = document.getElementById("panel-title");
const customerNameEl = document.getElementById("customer-name");

function t(key, ...args) {
  const value = STRINGS[state.language][key];
  return typeof value === "function" ? value(...args) : value;
}

function actionButton(labelKey, action) {
  return quickButton(t(labelKey), () => sendToAgent({ message: t(labelKey), action }));
}

function humanButton() {
  return actionButton("quickHuman", ACTIONS.HUMAN);
}

function starterButtons() {
  return [actionButton("starterShowCharges", ACTIONS.SHOW_CHARGES)];
}

// The conversation opens with the agent introducing itself and what it can
// do, followed by the two starter options. Re-rendered on a language switch
// until the customer sends their first message.
function renderWelcome() {
  if (!state.welcome || state.caseId || state.busy || chatLog.querySelector(".msg-bubble--customer")) return;
  chatLog.querySelectorAll(".welcome").forEach((el) => el.remove());
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble msg-bubble--agent welcome";
  bubble.textContent = state.welcome[state.language];
  const starters = quickRepliesBlock(starterButtons());
  starters.classList.add("welcome");
  chatLog.prepend(bubble, starters);
}

function applyStaticStrings() {
  document.documentElement.lang = t("htmlLang");
  panelTitle.textContent = t("panelTitle");
  messageLabel.textContent = t("messageLabel");
  messageInput.placeholder = t("inputPlaceholder");
  sendBtn.textContent = t("send");
  logoutBtn.textContent = t("logout");
  renderWelcome();
}

async function init() {
  let meRes;
  try {
    meRes = await fetch("/api/me", { credentials: "same-origin" });
  } catch {
    customerNameEl.textContent = t("sessionLoadError");
    return;
  }
  if (!meRes.ok) {
    window.location.href = "/";
    return;
  }
  const me = await meRes.json();
  customerNameEl.textContent = me.customer_id;
  state.welcome = me.welcome;

  document.querySelectorAll(".lang-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => setLanguage(btn.dataset.lang));
  });
  logoutBtn.addEventListener("click", logout);
  chatForm.addEventListener("submit", onSubmit);

  applyStaticStrings();
  renderPanel();
}

function setLanguage(lang) {
  if (lang === state.language || !(lang in STRINGS)) return;
  state.language = lang;
  document.querySelectorAll(".lang-toggle button").forEach((btn) => {
    const active = btn.dataset.lang === lang;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });
  applyStaticStrings();
  renderPanel();
}

async function logout() {
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch (err) {
    appendActionCard(t("logoutFailed"), String(err), { isError: true });
    return;
  }
  window.location.href = "/";
}

function appendBubble(role, text) {
  const el = document.createElement("div");
  el.className = `msg-bubble msg-bubble--${role}`;
  el.textContent = text;
  chatLog.appendChild(el);
  scrollLogToEnd();
}

function scrollLogToEnd() {
  chatLog.scrollTop = chatLog.scrollHeight;
}

function currentTime() {
  return new Date().toLocaleTimeString(t("timeLocale"), { hour: "2-digit", minute: "2-digit" });
}

// `bodyHtml`: markup already escaped by its builder (the escalation details).
function appendActionCard(title, body, { isError = false, actions = [], bodyHtml = null } = {}) {
  const card = document.createElement("div");
  card.className = `action-card${isError ? " action-card--error" : ""}`;
  const content = bodyHtml ?? (body ? escapeHtml(body) : "");
  card.innerHTML = `
    <div class="action-card__head">${escapeHtml(title)}<span class="action-card__time">${escapeHtml(currentTime())}</span></div>
    ${content ? `<div class="action-card__body">${content}</div>` : ""}
  `;
  if (actions.length) {
    // "interactive": the next send retires these buttons like any other block.
    const row = document.createElement("div");
    row.className = "action-card__actions interactive";
    row.append(...actions);
    card.appendChild(row);
  }
  chatLog.appendChild(card);
  scrollLogToEnd();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function listItemsHtml(items) {
  return (items || []).map((item) => `<li>${escapeHtml(String(item))}</li>`).join("");
}

function onSubmit(event) {
  event.preventDefault();
  // Checked BEFORE clearing the input: text typed while a turn is in flight is kept.
  if (state.busy) return;
  const message = messageInput.value.trim();
  if (!message) return;
  // A typed message after a closed case opens a new claim (AD-1).
  if (state.closedCase) startNewClaim();
  messageInput.value = "";
  sendToAgent({ message });
}

// Everything the customer sends goes through here: typed text, a tapped
// charge (selected_transaction_id) or a quick-reply button (action). The
// bubble always shows what the customer "said" (the button label for a tap).
function sendToAgent({ message, selectedTransactionId = null, action = null }) {
  if (state.busy) return;
  // Generated before the DOM is touched, so nothing is half-sent if it fails.
  const turnId = newTurnId();
  const retired = retireInteractiveBlocks();
  appendBubble("customer", message);
  // Frozen payload: a retry re-sends exactly this, same turn_id (AD-4), so the
  // server replays the turn instead of applying it twice.
  const turn = {
    body: Object.freeze({
      case_id: state.caseId,
      message,
      language: state.language,
      selected_transaction_id: selectedTransactionId,
      action,
      turn_id: turnId,
    }),
    retired,
    attempts: 0,
  };
  runTurn(turn);
}

// crypto.randomUUID exists only in secure contexts (HTTPS or localhost); the
// fallback builds the same lowercase UUIDv4 from crypto.getRandomValues.
function newTurnId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function runTurn(turn) {
  if (state.busy) return;
  const prevState = state.caseState;
  setBusy(true, WAIT_CAPTION_KEYS[prevState] || "waitGeneric");
  turn.attempts += 1;
  const { outcome, reply } = await postTurn(turn.body);
  if (outcome === TURN_OUTCOMES.SESSION_EXPIRED) {
    hideTyping();
    setBusy(false);
    window.location.href = "/";
    return;
  }

  // Before rendering: the typing bubble never sits under the reply.
  hideTyping();
  let focusTarget = messageInput;
  try {
    if (outcome === TURN_OUTCOMES.DELIVERED) {
      await renderReply(reply, prevState);
    } else {
      // Only a definite rejection of the FIRST attempt proves the turn changed
      // nothing server-side: a retry's rejection says nothing about the first.
      if (outcome === TURN_OUTCOMES.REJECTED && turn.attempts === 1) restoreInteractiveBlocks(turn.retired);
      focusTarget = appendRetryCard(turn, outcome === TURN_OUTCOMES.IN_PROGRESS);
    }
  } finally {
    setBusy(false);
    focusTarget.focus();
  }
}

// POSTs one turn, giving up after TURN_TIMEOUT_MS. Never throws.
async function postTurn(body) {
  const controller = new AbortController();
  const abortTimer = setTimeout(() => controller.abort(), TURN_TIMEOUT_MS);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (res.ok) return { outcome: TURN_OUTCOMES.DELIVERED, reply: await res.json() };
    if (res.status === HTTP_TURN_IN_PROGRESS) return { outcome: TURN_OUTCOMES.IN_PROGRESS, reply: null };
    if (res.status === HTTP_UNAUTHORIZED) return { outcome: TURN_OUTCOMES.SESSION_EXPIRED, reply: null };
    if (res.status >= 400 && res.status < 500) return { outcome: TURN_OUTCOMES.REJECTED, reply: null };
  } catch {
    // Abort (timeout), network error or an unreadable body: the turn may
    // have been applied on the server, so nothing is given back.
  } finally {
    clearTimeout(abortTimer);
  }
  return { outcome: TURN_OUTCOMES.FAILED, reply: null };
}

async function renderReply(reply, prevState) {
  // An abandoned turn can come back without a case (case_id null): no case yet.
  state.caseId = reply.case_id || null;
  state.caseState = reply.state || null;
  appendBubble("agent", reply.reply);
  if (reply.options && reply.options.length) appendChargeList(reply.options);
  rememberEscalation(reply.escalation);

  const refreshed = state.caseId ? await refreshCaseStatus() : false;
  if (!state.caseId) state.caseStatus = null;
  // The card marks the transition into a terminal state, once per case. The
  // escalation card needs only the reply, so a failed refresh cannot drop it.
  if (!TERMINAL_STATES.has(prevState) && (refreshed || reply.state === CASE_STATES.ESCALATED)) {
    renderTurnActionCard(reply.state);
  }
  // Last, so the next thing the customer can do sits at the end of the thread.
  appendQuickReplies(reply.state, reply.human_available);
  state.closedCase = TERMINAL_STATES.has(reply.state)
    ? { state: reply.state, reference: closedCaseReference(reply.state) }
    : null;
  renderPanel();
}

// What the new-claim divider names: the resolution reference, or the case
// number of an escalated case.
function closedCaseReference(caseState) {
  if (caseState === CASE_STATES.ESCALATED) return state.escalation ? state.escalation.case_number : null;
  return state.caseStatus ? state.caseStatus.resolution_reference : null;
}

function appendRetryCard(turn, inProgress) {
  const retryBtn = quickButton(t("retry"), () => {
    if (state.busy) return;
    // Retires this retry row too. It never comes back: a rejection restores
    // only turn.retired, the blocks the first send took away.
    retireInteractiveBlocks();
    runTurn(turn);
  }, "btn-secondary");
  appendActionCard(inProgress ? t("waitSlow") : t("turnError"), "", { isError: !inProgress, actions: [retryBtn] });
  return retryBtn;
}

function setBusy(busy, captionKey = null) {
  state.busy = busy;
  sendBtn.disabled = busy;
  messageInput.readOnly = busy;
  chatLog.setAttribute("aria-busy", String(busy));
  if (busy) {
    showTyping(t(captionKey));
    slowTimer = setTimeout(() => setWaitText(t("waitSlow"), true), SLOW_TURN_MS);
  }
  // Not busy: runTurn already hid the typing bubble before rendering the reply.
}

// The whole bubble is aria-hidden: the log's aria-live must not announce it;
// #chat-status (role=status, outside the log) carries the same text instead.
function showTyping(caption) {
  hideTyping();
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble msg-bubble--agent msg-bubble--typing";
  bubble.setAttribute("aria-hidden", "true");
  bubble.innerHTML = `
    <span class="typing-dots"><span></span><span></span><span></span></span>
    <span class="typing-caption"></span>
  `;
  chatLog.appendChild(bubble);
  setWaitText(caption, false);
  scrollLogToEnd();
}

function setWaitText(text, slow) {
  const caption = chatLog.querySelector(".msg-bubble--typing .typing-caption");
  if (caption) {
    caption.textContent = text;
    caption.classList.toggle("typing-caption--slow", slow);
  }
  chatStatus.textContent = text;
}

function hideTyping() {
  clearTimeout(slowTimer);
  slowTimer = null;
  chatLog.querySelectorAll(".msg-bubble--typing").forEach((el) => el.remove());
  chatStatus.textContent = "";
}

// A new claim in the same chat (AD-1): the closed case stays as it is, the
// next message goes out without case_id, so the server opens a new case.
function startNewClaim({ fromButton = false } = {}) {
  if (state.busy) return;
  const closed = state.closedCase;
  retireInteractiveBlocks({ permanently: true });
  state.caseId = null;
  state.caseStatus = null;
  state.caseState = null;
  state.closedCase = null;
  state.escalation = null;
  state.personaView = "client";

  if (closed) appendClaimDivider(closed);
  renderPanel();

  if (fromButton) {
    const starters = quickRepliesBlock(starterButtons());
    chatLog.appendChild(starters);
    starters.querySelector("button").focus();
  }
  scrollLogToEnd();
}

function appendClaimDivider(closed) {
  const divider = document.createElement("div");
  divider.className = "claim-divider";
  const outcome = t(closed.state === CASE_STATES.RESOLVED_AUTO ? "claimResolved" : "claimEscalated");
  divider.textContent = t("claimDivider", closed.reference, outcome);
  chatLog.appendChild(divider);
}

function quickButton(label, onClick, className = "quick-reply") {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = className;
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

// "interactive": the next send retires the block like any other.
function quickRepliesBlock(buttons) {
  const block = document.createElement("div");
  block.className = "quick-replies interactive";
  block.append(...buttons);
  return block;
}

// Old lists/buttons stay visible as history but can no longer be used. If the
// message definitely changed nothing they are given back
// (restoreInteractiveBlocks), except the ones retired for good by a new claim.
function retireInteractiveBlocks({ permanently = false } = {}) {
  const blocks = [...chatLog.querySelectorAll(".interactive:not(.retired)")];
  blocks.forEach((block) => {
    block.classList.add("retired");
    if (permanently) block.dataset.retiredForGood = "true";
    block.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  });
  return blocks;
}

function restoreInteractiveBlocks(blocks) {
  blocks.forEach((block) => {
    if (block.dataset.retiredForGood) return;
    block.classList.remove("retired");
    block.querySelectorAll("button").forEach((b) => {
      b.disabled = false;
      b.classList.remove("chosen");
    });
  });
}

function formatAmount(amount, currency) {
  const style = currency ? { style: "currency", currency } : {};
  try {
    return new Intl.NumberFormat(t("timeLocale"), { ...style, maximumFractionDigits: 2 }).format(amount);
  } catch {
    return `${amount} ${currency}`;
  }
}

function formatDay(isoDay) {
  // Noon avoids the date shifting a day in timezones west of UTC.
  return new Date(`${isoDay}T12:00:00`).toLocaleDateString(t("timeLocale"), { day: "numeric", month: "short", year: "numeric" });
}

function chargeLabel(opt) {
  return `${opt.merchant || t("unknownMerchant")} · ${formatAmount(opt.amount, opt.currency)} · ${formatDay(opt.date)}`;
}

function categoryLabel(category) {
  return t("categories")[category] || String(category);
}

function chargeMeta(opt) {
  const category = opt.category ? categoryLabel(opt.category) : null;
  return category ? `${formatDay(opt.date)} · ${category}` : formatDay(opt.date);
}

function pickCharge(btn, opt) {
  btn.classList.add("chosen");
  sendToAgent({ message: chargeLabel(opt), selectedTransactionId: opt.transaction_id });
}

function appendChargeList(options) {
  const block = document.createElement("div");
  block.className = "charge-list interactive";
  block.setAttribute("role", "group");
  block.setAttribute("aria-label", t("chargeListLabel"));
  options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "charge-option";
    btn.innerHTML = `
      <span class="charge-option__merchant">${escapeHtml(opt.merchant || t("unknownMerchant"))}</span>
      <span class="charge-option__amount">${escapeHtml(formatAmount(opt.amount, opt.currency))}</span>
      <span class="charge-option__meta">${escapeHtml(chargeMeta(opt))}</span>
    `;
    btn.addEventListener("click", () => pickCharge(btn, opt));
    block.appendChild(btn);
  });
  chatLog.appendChild(block);
  scrollLogToEnd();
}

// "Hablar con una persona" only appears once the server says a request for a
// person escalates (human_available): after the customer's first request, or
// once the agent could not resolve the case.
function appendQuickReplies(caseState, humanAvailable) {
  const buttons = [];
  if (caseState === CASE_STATES.SELECTING) {
    buttons.push(actionButton("quickNotInList", ACTIONS.NONE_OF_THESE));
  } else if (caseState === CASE_STATES.CONFIRMING) {
    buttons.push(actionButton("quickYes", ACTIONS.CONFIRM_YES), actionButton("quickNo", ACTIONS.CONFIRM_NO));
  }
  if (caseState === CASE_STATES.AWAITING_REPORT) buttons.push(...starterButtons());
  if (TERMINAL_STATES.has(caseState)) {
    buttons.push(quickButton(t("quickNewClaim"), () => startNewClaim({ fromButton: true })));
  }
  if (humanAvailable) buttons.push(humanButton());
  if (!buttons.length) return;
  chatLog.appendChild(quickRepliesBlock(buttons));
  scrollLogToEnd();
}

async function refreshCaseStatus() {
  if (!state.caseId) return false;
  try {
    const res = await fetch(`/api/case/${encodeURIComponent(state.caseId)}`, { credentials: "same-origin" });
    if (!res.ok) return false;
    state.caseStatus = await res.json();
    return true;
  } catch {
    return false;
  }
}

// The charge the case is about: the identified one when there is one,
// otherwise what the customer reported.
function caseChargeLabel(status) {
  if (status.matched_charge) return chargeLabel(status.matched_charge);
  return formatAmount(status.reported_amount, status.reported_currency);
}

function renderTurnActionCard(newState) {
  const status = state.caseStatus;
  if (newState === CASE_STATES.RESOLVED_AUTO) {
    appendActionCard(
      t("actionCardResolvedTitle"),
      t("actionCardResolvedBody", caseChargeLabel(status), status.resolution_reference),
    );
  } else if (newState === CASE_STATES.ESCALATED) {
    // A legacy reply without an escalation object keeps the generic sentence.
    if (state.escalation) appendActionCard(t("actionCardEscalatedTitle"), "", { bodyHtml: escalationDetailsHtml() });
    else appendActionCard(t("actionCardEscalatedTitle"), t("actionCardEscalatedBody"));
  }
}

// Later replies about the same case name no charge (only the escalating turn
// knows the customer identified one): the first charge and time are kept, the
// rest follows the latest reply (e.g. the reason in the language just chosen).
function rememberEscalation(escalation) {
  if (!escalation) return;
  const sameCase = state.escalation && state.escalation.case_number === escalation.case_number;
  state.escalation = sameCase
    ? { ...escalation, charge: state.escalation.charge, time: state.escalation.time }
    : { ...escalation, time: currentTime() };
}

// The one builder of the escalation details, for the card and the client
// panel: case number, the two-step timeline, the charge (only when known),
// the reason and the note. Every value is escaped here.
function escalationDetailsHtml() {
  const esc = state.escalation;
  let html = `
    <div class="client-summary">${escapeHtml(t("caseNumberLabel"))}</div>
    <div class="case-id">${escapeHtml(esc.case_number)}</div>
    <div class="action-card__timeline">
      ${verifyStepHtml("done", "✓", t("escalationStepDone"), esc.time, t("stepDone"))}
      ${verifyStepHtml("pending", "2", t("escalationStepPending"), t("escalationDeadline", esc.contact_business_days), t("stepPending"))}
    </div>
  `;
  if (esc.charge) html += `<p class="client-summary">${escapeHtml(t("escalationCharge", chargeLabel(esc.charge)))}</p>`;
  if (esc.reason) html += `<p class="client-summary">${escapeHtml(t("escalationReason", esc.reason))}</p>`;
  html += `<p class="client-summary">${escapeHtml(t("escalationNote"))}</p>`;
  return html;
}

// `srState`: the step's state in words for screen readers (the dot is aria-hidden).
function verifyStepHtml(variant, dotLabel, label, detail, srState = null) {
  const prefix = srState ? `<span class="sr-only">${escapeHtml(srState)} </span>` : "";
  return `
    <div class="verify-step verify-step--${variant}">
      <div class="dot" aria-hidden="true">${dotLabel}</div>
      <div class="label">${prefix}${escapeHtml(label)}</div>
      <div class="detail">${escapeHtml(detail)}</div>
    </div>
  `;
}

function badgeHtml(variant, label) {
  return `<span class="status-badge status-badge--${variant}">${escapeHtml(label)}</span>`;
}

function renderPanel() {
  const status = state.caseStatus;
  // The escalation object wins: a failed refresh may have left the previous
  // turn's status (or none) behind, and the case is escalated either way.
  const caseState = state.escalation ? CASE_STATES.ESCALATED : status ? status.state : null;
  const transactionFound = () => t("stepTransactionFound", caseChargeLabel(status));

  let badge;
  let step2Variant = "pending", step2Dot = "2", step2Detail = t("stepTransactionPending");
  let step3Variant = "pending", step3Dot = "3", step3Detail = t("stepPolicyPending");

  if (caseState === CASE_STATES.CLARIFYING) {
    badge = badgeHtml("warning", t("badgeClarifying"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionSearching");
  } else if (caseState === CASE_STATES.SELECTING) {
    badge = badgeHtml("warning", t("badgeSelecting"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionSelecting");
  } else if (caseState === CASE_STATES.CONFIRMING) {
    badge = badgeHtml("warning", t("badgeConfirming"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionAwaitingConfirm");
  } else if (caseState === CASE_STATES.AWAITING_EXPLANATION) {
    badge = badgeHtml("warning", t("badgeExplaining"));
    step2Variant = "done"; step2Dot = "✓";
    step2Detail = transactionFound();
    step3Variant = "warning"; step3Dot = "?";
    step3Detail = t("stepPolicyExplaining");
  } else if (caseState === CASE_STATES.AWAITING_STATEMENT) {
    badge = badgeHtml("warning", t("badgeStatement"));
    step3Variant = "warning"; step3Dot = "?";
    step3Detail = t("stepPolicyStatement");
  } else if (caseState === CASE_STATES.RESOLVED_AUTO) {
    badge = badgeHtml("success", t("badgeResolved"));
    step2Variant = "done"; step2Dot = "✓";
    step2Detail = transactionFound();
    step3Variant = "done"; step3Dot = "✓";
    step3Detail = t("stepPolicyResolved");
  } else if (caseState === CASE_STATES.ESCALATED) {
    badge = badgeHtml("info", t("badgeEscalated"));
    // With an escalation object, only the charge the customer identified (the
    // stored match may be an unconfirmed proposal, plan.md AD-5).
    const escalatedCharge = state.escalation ? state.escalation.charge : null;
    if (escalatedCharge) {
      step2Variant = "done"; step2Dot = "✓";
      step2Detail = t("stepTransactionFound", chargeLabel(escalatedCharge));
    } else if (!state.escalation && status && status.matched_transaction_id) {
      step2Variant = "done"; step2Dot = "✓";
      step2Detail = transactionFound();
    } else {
      step2Variant = "error"; step2Dot = "✕";
      step2Detail = t("stepTransactionNotIdentified");
    }
    step3Variant = "info"; step3Dot = "→";
    step3Detail = t("stepPolicyEscalated");
  } else {
    badge = badgeHtml("pending", t("badgePending"));
  }

  let html = badge;
  html += verifyStepHtml("done", "✓", t("stepIdentity"), t("stepIdentityDetail"));
  html += verifyStepHtml(step2Variant, step2Dot, t("stepTransaction"), step2Detail);
  html += verifyStepHtml(step3Variant, step3Dot, t("stepPolicy"), step3Detail);

  if (caseState === CASE_STATES.RESOLVED_AUTO && status.resolution_reference) {
    html += `<p><span class="verified-chip">${escapeHtml(t("verifiedChip", status.resolution_reference))}</span></p>`;
  }
  if (caseState === CASE_STATES.ESCALATED) {
    html += renderPersonaToggle() + renderPersonaView();
  }

  panelContent.innerHTML = html;

  if (caseState === CASE_STATES.ESCALATED) {
    document.querySelectorAll(".persona-toggle button").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.personaView = btn.dataset.view;
        renderPanel();
      });
    });
  }
}

function renderPersonaToggle() {
  return `
    <div class="persona-toggle" role="group" aria-label="Vista">
      <button type="button" data-view="client" class="${state.personaView === "client" ? "active" : ""}">${t("personaClient")}</button>
      <button type="button" data-view="internal" class="${state.personaView === "internal" ? "active" : ""}">${t("personaInternal")}</button>
    </div>
  `;
}

function renderPersonaView() {
  if (state.personaView === "client") {
    if (state.escalation) return escalationDetailsHtml();
    return `<p class="client-summary">${escapeHtml(t("actionCardEscalatedBody"))}</p>`;
  }
  return renderHandoffCard();
}

function humanizeKey(key) {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function factLabel(key) {
  return t("handoffFields")[key] || humanizeKey(key);
}

const AMOUNT_FACTS = new Set(["amount", "reported_amount"]);
const DATE_FACTS = new Set(["date", "reported_date", "noticed_on"]);
const CODED_FACTS = new Set([
  "dispute_reason", "customer_confirmation", "charge_confirmed", "explanation_specific", "explanation_consistent",
  "statement_status", "denies_purchase", "merchant_known", "card_possession", "how_noticed",
  "other_suspicious_activity",
]);

function factValue(key, value, facts) {
  if (AMOUNT_FACTS.has(key)) return formatAmount(Number(value), facts.currency);
  if (DATE_FACTS.has(key)) return formatDay(value);
  if (key === "category") return categoryLabel(value);
  if (CODED_FACTS.has(key)) return t("handoffValues")[value] || String(value);
  return String(value);
}

function hasAmount(facts) {
  return Object.keys(facts).some((key) => AMOUNT_FACTS.has(key));
}

function isShownFact(facts) {
  const currencyInAmount = hasAmount(facts);
  return ([key]) => !(key === "currency" && currencyInAmount);
}

function factListHtml(facts) {
  const rows = Object.entries(facts).filter(isShownFact(facts)).map(factRowHtml(facts));
  return `<dl class="fact-list">${rows.join("")}</dl>`;
}

function factRowHtml(facts) {
  return ([key, value]) => `<div><dt>${escapeHtml(factLabel(key))}</dt><dd>${escapeHtml(factValue(key, value, facts))}</dd></div>`;
}

function handoffSectionHtml(label, body, tag = null) {
  const tagHtml = tag ? ` <span class="verify-tag verify-tag--${tag.kind}">${escapeHtml(tag.text)}</span>` : "";
  return `<div class="handoff-card__section"><div class="label">${escapeHtml(label)}${tagHtml}</div>${body}</div>`;
}

function listSectionHtml(labelKey, items, listClass = "") {
  const classAttr = listClass ? ` class="${listClass}"` : "";
  return handoffSectionHtml(t(labelKey), `<ul${classAttr}>${listItemsHtml(items)}</ul>`);
}

function hasEntries(obj) {
  return Boolean(obj) && Object.keys(obj).length > 0;
}

function handoffSections(handoff) {
  const sections = [];
  if (handoff.request_summary) sections.push(handoffSectionHtml(t("handoffRequest"), `<p>${escapeHtml(handoff.request_summary)}</p>`));
  if (hasEntries(handoff.verified_facts)) {
    sections.push(handoffSectionHtml(t("handoffFacts"), factListHtml(handoff.verified_facts), { kind: "record", text: t("handoffFactsTag") }));
  }
  if (hasEntries(handoff.customer_reported)) {
    sections.push(handoffSectionHtml(t("handoffReported"), factListHtml(handoff.customer_reported), { kind: "unverified", text: t("handoffReportedTag") }));
  }
  if (hasEntries(handoff.facts)) sections.push(handoffSectionHtml(t("handoffLegacyFacts"), factListHtml(handoff.facts)));
  if (handoff.policy_reason_count) sections.push(handoffSectionHtml(t("handoffPolicy"), `<p>${escapeHtml(t("handoffPolicyCount", handoff.policy_reason_count))}</p>`));
  if (handoff.actions_taken?.length) sections.push(listSectionHtml("handoffActions", handoff.actions_taken, "action-log"));
  if (handoff.evidence?.length) sections.push(listSectionHtml("handoffEvidence", handoff.evidence));
  if (handoff.open_questions?.length) sections.push(listSectionHtml("handoffQuestions", handoff.open_questions, "checklist"));
  return sections.join("");
}

function renderHandoffCard() {
  const handoff = state.caseStatus ? state.caseStatus.handoff : null;
  if (!handoff) return "";
  return `
    <div class="handoff-card">
      <div class="handoff-card__header">${t("handoffTitle")}</div>
      <div class="handoff-card__body">${handoffSections(handoff)}</div>
    </div>
  `;
}

init();

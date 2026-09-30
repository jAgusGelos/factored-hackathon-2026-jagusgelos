// Renders purely from the state machine's API responses (/api/chat, /api/case/{id}),
// never from hardcoded demo content.

const STRINGS = {
  es: {
    htmlLang: "es",
    timeLocale: "es-AR",
    starterShowCharges: "Ver mis últimos cargos",
    starterShowChargesMessage: "Mostrame mis últimos cargos",
    quickHuman: "Hablar con una persona",
    quickNotInList: "No está en la lista",
    quickYes: "Sí, es ese",
    quickNo: "No es ese",
    chargeListLabel: "Tus movimientos",
    unknownMerchant: "Comercio sin nombre",
    badgeSelecting: "Elegí el cargo",
    stepTransactionSelecting: "Mostrando tus movimientos para que elijas",
    categories: { Food: "Comida", Transport: "Transporte", Services: "Servicios", Entertainment: "Entretenimiento", Health: "Salud", Other: "Otros" },
    messageLabel: "Mensaje",
    inputPlaceholder: "Escribí tu mensaje...",
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
    badgeConfirming: "Esperando tu confirmación",
    stepTransactionAwaitingConfirm: "Cargo encontrado, pendiente de tu confirmación",
    badgeExplaining: "Esperando tu explicación",
    stepPolicyExplaining: "Falta que cuentes qué pasó con el cargo",
    badgeResolved: "Resuelto",
    badgeEscalated: "Escalado",
    verifiedChip: (ref) => `✓ Referencia ${ref}`,
    actionCardResolvedTitle: "Verificación del sistema",
    actionCardResolvedBody: (amount, ref) =>
      `Cargo confirmado (${amount}). Crédito provisional simulado registrado (referencia ${ref}).`,
    actionCardEscalatedTitle: "Caso derivado",
    actionCardEscalatedBody: "El caso fue derivado a un agente humano con el resumen estructurado del reclamo.",
    personaClient: "Vista Cliente",
    personaInternal: "Vista Interna",
    handoffTitle: "Resumen para el agente humano",
    handoffFacts: "Hechos verificados",
    handoffActions: "Acciones realizadas",
    handoffEvidence: "Evidencia",
    handoffQuestions: "Preguntas abiertas",
    sendError: "No se pudo enviar el mensaje. Intentá de nuevo.",
    sessionLoadError: "No se pudo cargar la sesión.",
    logoutFailed: "No se pudo cerrar sesión.",
  },
  pt: {
    htmlLang: "pt-BR",
    timeLocale: "pt-BR",
    starterShowCharges: "Ver minhas últimas cobranças",
    starterShowChargesMessage: "Mostre minhas últimas cobranças",
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
    badgeResolved: "Resolvido",
    badgeEscalated: "Escalado",
    verifiedChip: (ref) => `✓ Referência ${ref}`,
    actionCardResolvedTitle: "Verificação do sistema",
    actionCardResolvedBody: (amount, ref) =>
      `Cobrança confirmada (${amount}). Crédito provisório simulado registrado (referência ${ref}).`,
    actionCardEscalatedTitle: "Caso encaminhado",
    actionCardEscalatedBody: "O caso foi encaminhado a um agente humano com o resumo estruturado da reclamação.",
    personaClient: "Vista Cliente",
    personaInternal: "Vista Interna",
    handoffTitle: "Resumo para o agente humano",
    handoffFacts: "Fatos verificados",
    handoffActions: "Ações realizadas",
    handoffEvidence: "Evidências",
    handoffQuestions: "Perguntas em aberto",
    sendError: "Não foi possível enviar a mensagem. Tente novamente.",
    sessionLoadError: "Não foi possível carregar a sessão.",
    logoutFailed: "Não foi possível encerrar a sessão.",
  },
};

// Must match app/case_model.py::CustomerAction.
const ACTIONS = Object.freeze({
  HUMAN: "human",
  NONE_OF_THESE: "none_of_these",
  CONFIRM_YES: "confirm_yes",
  CONFIRM_NO: "confirm_no",
});

const state = {
  welcome: null, // {es, pt}, from /api/me
  language: "es",
  caseId: null,
  caseStatus: null,
  personaView: "client", // "client" | "internal"
  busy: false,
};

const chatLog = document.getElementById("chat-log");
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
  return [quickButton(t("starterShowCharges"), () => sendToAgent({ message: t("starterShowChargesMessage") }))];
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
  const starters = document.createElement("div");
  starters.className = "quick-replies interactive welcome";
  starters.append(...starterButtons());
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
  chatLog.scrollTop = chatLog.scrollHeight;
}

function appendActionCard(title, body, { isError = false } = {}) {
  const card = document.createElement("div");
  card.className = `action-card${isError ? " action-card--error" : ""}`;
  const time = new Date().toLocaleTimeString(t("timeLocale"), { hour: "2-digit", minute: "2-digit" });
  card.innerHTML = `
    <div class="action-card__head">${escapeHtml(title)}<span class="action-card__time">${escapeHtml(time)}</span></div>
    <div class="action-card__body">${escapeHtml(body)}</div>
  `;
  chatLog.appendChild(card);
  chatLog.scrollTop = chatLog.scrollHeight;
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
  const message = messageInput.value.trim();
  if (!message) return;
  messageInput.value = "";
  sendToAgent({ message });
}

// Everything the customer sends goes through here: typed text, a tapped
// charge (selected_transaction_id) or a quick-reply button (action). The
// bubble always shows what the customer "said" (the button label for a tap).
async function sendToAgent({ message, selectedTransactionId = null, action = null }) {
  if (state.busy) return;
  state.busy = true;
  sendBtn.disabled = true;
  const retired = retireInteractiveBlocks();
  appendBubble("customer", message);
  let delivered = false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        case_id: state.caseId,
        message,
        language: state.language,
        selected_transaction_id: selectedTransactionId,
        action,
      }),
    });

    if (!res.ok) {
      appendActionCard(t("sendError"), `HTTP ${res.status}`, { isError: true });
      return;
    }

    const reply = await res.json();
    delivered = true;
    state.caseId = reply.case_id;
    appendBubble("agent", reply.reply);
    if (reply.options && reply.options.length) appendChargeList(reply.options);
    appendQuickReplies(reply.state, reply.human_available);

    if (await refreshCaseStatus()) {
      renderTurnActionCard(reply.state);
    }
    renderPanel();
  } catch (err) {
    if (!delivered) appendActionCard(t("sendError"), String(err), { isError: true });
  } finally {
    if (!delivered) restoreInteractiveBlocks(retired);
    state.busy = false;
    sendBtn.disabled = false;
    messageInput.focus();
  }
}

function quickButton(label, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "quick-reply";
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

// Old lists/buttons stay visible as history but can no longer be used. If the
// message never reached the server they are given back (restoreInteractiveBlocks).
function retireInteractiveBlocks() {
  const blocks = [...chatLog.querySelectorAll(".interactive:not(.retired)")];
  blocks.forEach((block) => {
    block.classList.add("retired");
    block.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  });
  return blocks;
}

function restoreInteractiveBlocks(blocks) {
  blocks.forEach((block) => {
    block.classList.remove("retired");
    block.querySelectorAll("button").forEach((b) => {
      b.disabled = false;
      b.classList.remove("chosen");
    });
  });
}

function formatAmount(amount, currency) {
  try {
    return new Intl.NumberFormat(t("timeLocale"), { style: "currency", currency, maximumFractionDigits: 2 }).format(amount);
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

function chargeMeta(opt) {
  const category = opt.category ? t("categories")[opt.category] || opt.category : null;
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
  chatLog.scrollTop = chatLog.scrollHeight;
}

// "Hablar con una persona" only appears once the server says the agent has
// tried and could not resolve the case (human_available).
function appendQuickReplies(caseState, humanAvailable) {
  const buttons = [];
  if (caseState === "selecting") {
    buttons.push(actionButton("quickNotInList", ACTIONS.NONE_OF_THESE));
  } else if (caseState === "confirming") {
    buttons.push(actionButton("quickYes", ACTIONS.CONFIRM_YES), actionButton("quickNo", ACTIONS.CONFIRM_NO));
  }
  if (caseState === "awaiting_report") buttons.push(...starterButtons());
  if (humanAvailable) buttons.push(humanButton());
  if (!buttons.length) return;
  const block = document.createElement("div");
  block.className = "quick-replies interactive";
  block.append(...buttons);
  chatLog.appendChild(block);
  chatLog.scrollTop = chatLog.scrollHeight;
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
  if (newState === "resolved_auto") {
    appendActionCard(
      t("actionCardResolvedTitle"),
      t("actionCardResolvedBody", caseChargeLabel(status), status.resolution_reference),
    );
  } else if (newState === "escalated") {
    appendActionCard(t("actionCardEscalatedTitle"), t("actionCardEscalatedBody"));
  }
}

function verifyStepHtml(variant, dotLabel, label, detail) {
  return `
    <div class="verify-step verify-step--${variant}">
      <div class="dot" aria-hidden="true">${dotLabel}</div>
      <div class="label">${escapeHtml(label)}</div>
      <div class="detail">${escapeHtml(detail)}</div>
    </div>
  `;
}

function badgeHtml(variant, label) {
  return `<span class="status-badge status-badge--${variant}">${escapeHtml(label)}</span>`;
}

function renderPanel() {
  const status = state.caseStatus;
  const caseState = status ? status.state : null;
  const transactionFound = () => t("stepTransactionFound", caseChargeLabel(status));

  let badge;
  let step2Variant = "pending", step2Dot = "2", step2Detail = t("stepTransactionPending");
  let step3Variant = "pending", step3Dot = "3", step3Detail = t("stepPolicyPending");

  if (caseState === "clarifying") {
    badge = badgeHtml("warning", t("badgeClarifying"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionSearching");
  } else if (caseState === "selecting") {
    badge = badgeHtml("warning", t("badgeSelecting"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionSelecting");
  } else if (caseState === "confirming") {
    badge = badgeHtml("warning", t("badgeConfirming"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionAwaitingConfirm");
  } else if (caseState === "awaiting_explanation") {
    badge = badgeHtml("warning", t("badgeExplaining"));
    step2Variant = "done"; step2Dot = "✓";
    step2Detail = transactionFound();
    step3Variant = "warning"; step3Dot = "?";
    step3Detail = t("stepPolicyExplaining");
  } else if (caseState === "resolved_auto") {
    badge = badgeHtml("success", t("badgeResolved"));
    step2Variant = "done"; step2Dot = "✓";
    step2Detail = transactionFound();
    step3Variant = "done"; step3Dot = "✓";
    step3Detail = t("stepPolicyResolved");
  } else if (caseState === "escalated") {
    badge = badgeHtml("info", t("badgeEscalated"));
    if (status.matched_transaction_id) {
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

  if (caseState === "resolved_auto" && status.resolution_reference) {
    html += `<p><span class="verified-chip">${escapeHtml(t("verifiedChip", status.resolution_reference))}</span></p>`;
  }
  if (caseState === "escalated") {
    html += renderPersonaToggle() + renderPersonaView();
  }

  panelContent.innerHTML = html;

  if (caseState === "escalated") {
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
    return `<p class="client-summary">${escapeHtml(t("actionCardEscalatedBody"))}</p>`;
  }
  return renderHandoffCard();
}

function renderHandoffCard() {
  const handoff = state.caseStatus.handoff;
  if (!handoff) return "";

  const factsRows = Object.entries(handoff.facts || {})
    .map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(String(v))}</td></tr>`)
    .join("");

  return `
    <div class="handoff-card">
      <div class="handoff-card__header">${t("handoffTitle")}</div>
      <div class="handoff-card__body">
        <div class="handoff-card__section">
          <div class="label">${t("handoffFacts")}</div>
          <table class="fact-table"><tbody>${factsRows}</tbody></table>
        </div>
        <div class="handoff-card__section">
          <div class="label">${t("handoffActions")}</div>
          <ul class="action-log">${listItemsHtml(handoff.actions_taken)}</ul>
        </div>
        <div class="handoff-card__section">
          <div class="label">${t("handoffEvidence")}</div>
          <ul>${listItemsHtml(handoff.evidence)}</ul>
        </div>
        <div class="handoff-card__section">
          <div class="label">${t("handoffQuestions")}</div>
          <ul class="checklist">${listItemsHtml(handoff.open_questions)}</ul>
        </div>
      </div>
    </div>
  `;
}

init();

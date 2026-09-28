// Renders purely from the state machine's API responses (/api/chat, /api/case/{id}),
// never from hardcoded demo content.

const STRINGS = {
  es: {
    htmlLang: "es",
    timeLocale: "es-AR",
    emptyTitle: "Contanos qué pasó",
    emptyBody: "Describí el cargo que no reconocés: monto, fecha aproximada y, si te acordás, el comercio.",
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
    stepTransactionFound: (amount, currency) => `Coincidencia confirmada (monto reportado: ${amount} ${currency})`,
    stepPolicy: "Política evaluada",
    stepPolicyPending: "Pendiente",
    stepPolicyResolved: "Resolución automática aplicada",
    stepPolicyEscalated: "Derivado a un agente humano",
    badgePending: "Esperando reporte",
    badgeClarifying: "Necesita aclaración",
    badgeConfirming: "Esperando tu confirmación",
    stepTransactionAwaitingConfirm: "Cargo encontrado, pendiente de tu confirmación",
    badgeResolved: "Resuelto",
    badgeEscalated: "Escalado",
    verifiedChip: (ref) => `✓ Referencia ${ref}`,
    actionCardResolvedTitle: "Verificación del sistema",
    actionCardResolvedBody: (amount, currency, ref) =>
      `Transacción coincidente confirmada (monto reportado: ${amount} ${currency}). Crédito provisional simulado registrado (referencia ${ref}).`,
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
    emptyTitle: "Conte o que aconteceu",
    emptyBody: "Descreva a cobrança que você não reconhece: valor, data aproximada e, se lembrar, o comerciante.",
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
    stepTransactionFound: (amount, currency) => `Correspondência confirmada (valor relatado: ${amount} ${currency})`,
    stepPolicy: "Política avaliada",
    stepPolicyPending: "Pendente",
    stepPolicyResolved: "Resolução automática aplicada",
    stepPolicyEscalated: "Encaminhado a um agente humano",
    badgePending: "Aguardando relato",
    badgeClarifying: "Precisa de esclarecimento",
    badgeConfirming: "Aguardando sua confirmação",
    stepTransactionAwaitingConfirm: "Cobrança encontrada, aguardando sua confirmação",
    badgeResolved: "Resolvido",
    badgeEscalated: "Escalado",
    verifiedChip: (ref) => `✓ Referência ${ref}`,
    actionCardResolvedTitle: "Verificação do sistema",
    actionCardResolvedBody: (amount, currency, ref) =>
      `Transação correspondente confirmada (valor relatado: ${amount} ${currency}). Crédito provisório simulado registrado (referência ${ref}).`,
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

const state = {
  language: "es",
  caseId: null,
  caseStatus: null,
  personaView: "client", // "client" | "internal"
};

const chatLog = document.getElementById("chat-log");
const emptyState = document.getElementById("empty-state");
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

function applyStaticStrings() {
  document.documentElement.lang = t("htmlLang");
  panelTitle.textContent = t("panelTitle");
  document.getElementById("empty-state-title").textContent = t("emptyTitle");
  document.getElementById("empty-state-body").textContent = t("emptyBody");
  messageLabel.textContent = t("messageLabel");
  messageInput.placeholder = t("inputPlaceholder");
  sendBtn.textContent = t("send");
  logoutBtn.textContent = t("logout");
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
  emptyState.style.display = "none";
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

async function onSubmit(event) {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;

  messageInput.value = "";
  sendBtn.disabled = true;
  appendBubble("customer", message);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ case_id: state.caseId, message, language: state.language }),
    });

    if (!res.ok) {
      appendActionCard(t("sendError"), `HTTP ${res.status}`, { isError: true });
      return;
    }

    const reply = await res.json();
    state.caseId = reply.case_id;
    appendBubble("agent", reply.reply);

    if (await refreshCaseStatus()) {
      renderTurnActionCard(reply.state);
    }
    renderPanel();
  } catch (err) {
    appendActionCard(t("sendError"), String(err), { isError: true });
  } finally {
    sendBtn.disabled = false;
    messageInput.focus();
  }
}

async function refreshCaseStatus() {
  if (!state.caseId) return false;
  const res = await fetch(`/api/case/${encodeURIComponent(state.caseId)}`, { credentials: "same-origin" });
  if (!res.ok) return false;
  state.caseStatus = await res.json();
  return true;
}

function renderTurnActionCard(newState) {
  const status = state.caseStatus;
  if (newState === "resolved_auto") {
    appendActionCard(
      t("actionCardResolvedTitle"),
      t("actionCardResolvedBody", status.reported_amount, status.reported_currency, status.resolution_reference),
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
  const transactionFound = () => t("stepTransactionFound", status.reported_amount, status.reported_currency);

  let badge;
  let step2Variant = "pending", step2Dot = "2", step2Detail = t("stepTransactionPending");
  let step3Variant = "pending", step3Dot = "3", step3Detail = t("stepPolicyPending");

  if (caseState === "clarifying") {
    badge = badgeHtml("warning", t("badgeClarifying"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionSearching");
  } else if (caseState === "confirming") {
    badge = badgeHtml("warning", t("badgeConfirming"));
    step2Variant = "warning"; step2Dot = "?";
    step2Detail = t("stepTransactionAwaitingConfirm");
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
      step2Variant = "warning"; step2Dot = "?";
      step2Detail = t("stepTransactionSearching");
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

// Milestone 1 walking-skeleton wiring. Replaced by the DESIGN.md UI in Milestone 4.

let caseId = null;

const loginForm = document.getElementById("login-form");
const loginStatus = document.getElementById("login-status");
const chatSection = document.getElementById("chat-section");
const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;

  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ username, password }),
  });

  if (!res.ok) {
    loginStatus.textContent = "Credenciales inválidas.";
    return;
  }

  const data = await res.json();
  loginStatus.textContent = `Ingresaste como ${data.customer_id}`;
  chatSection.style.display = "block";
});

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const messageInput = document.getElementById("message");
  const message = messageInput.value;
  messageInput.value = "";

  appendMessage("cliente", message);

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ case_id: caseId, message }),
  });

  if (!res.ok) {
    appendMessage("sistema", "Error: no se pudo enviar el mensaje.");
    return;
  }

  const data = await res.json();
  caseId = data.case_id;
  appendMessage("agente", data.reply);
});

function appendMessage(role, text) {
  const el = document.createElement("p");
  el.textContent = `[${role}] ${text}`;
  chatLog.appendChild(el);
}

const form = document.getElementById("login-form");
const errorEl = document.getElementById("login-error");
const submitBtn = form.querySelector("button[type=submit]");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.textContent = "";

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  submitBtn.disabled = true;
  let response;
  try {
    response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username, password }),
    });
  } catch {
    errorEl.textContent = "No se pudo conectar con el servidor. Intentá de nuevo.";
    return;
  } finally {
    submitBtn.disabled = false;
  }

  if (response.status === 429) {
    const seconds = parseInt(response.headers.get("Retry-After") || "0", 10);
    const wait = seconds >= 60 ? `${Math.ceil(seconds / 60)} min` : `${Math.max(seconds, 1)} s`;
    errorEl.textContent = `Demasiados intentos fallidos. Esperá ${wait} e intentá de nuevo.`;
    return;
  }
  if (!response.ok) {
    errorEl.textContent = "Usuario o contraseña incorrectos.";
    return;
  }

  window.location.href = "/chat.html";
});

// Demo personas: what each provisioned test account demonstrates.
const PERSONA_INFO = {
  "cliente.claro": ["Caso simple", "Un cargo claro: el agente lo confirma con vos y se resuelve solo."],
  "cliente.ambiguo": ["Caso ambiguo", "No hay un cargo claro: el agente pregunta y, si sigue sin cerrar, deriva."],
  "cliente.escalado": ["Requiere escalación", "Cargo de riesgo alto: el agente lo deriva a una persona."],
};

async function loadDemoPersonas() {
  let personas;
  try {
    const res = await fetch("/auth/demo-personas", { credentials: "same-origin" });
    if (!res.ok) return;
    personas = await res.json();
  } catch {
    return;
  }
  if (!Array.isArray(personas) || personas.length === 0) return;

  const list = document.getElementById("demo-personas-list");
  personas.forEach(({ username, password }) => {
    const [name, description] = PERSONA_INFO[username] || ["Perfil de demostración", ""];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "demo-persona";
    const title = document.createElement("span");
    title.className = "persona-name";
    title.textContent = `${name} · ${username}`;
    const desc = document.createElement("span");
    desc.className = "persona-desc";
    desc.textContent = description;
    btn.append(title, desc);
    btn.addEventListener("click", () => {
      document.getElementById("username").value = username;
      document.getElementById("password").value = password;
      list.querySelectorAll(".demo-persona").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      submitBtn.focus();
    });
    list.appendChild(btn);
  });
  document.getElementById("demo-personas").hidden = false;
}

loadDemoPersonas();

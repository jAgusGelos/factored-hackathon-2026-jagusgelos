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
    errorEl.textContent = "No se pudo conectar con el servidor. Intente de nuevo.";
    return;
  } finally {
    submitBtn.disabled = false;
  }

  if (response.status === 429) {
    const seconds = parseInt(response.headers.get("Retry-After") || "0", 10);
    const wait = seconds >= 60 ? `${Math.ceil(seconds / 60)} min` : `${Math.max(seconds, 1)} s`;
    errorEl.textContent = `Demasiados intentos fallidos. Espere ${wait} e intente de nuevo.`;
    return;
  }
  if (!response.ok) {
    errorEl.textContent = "Usuario o contraseña incorrectos.";
    return;
  }

  window.location.href = "/chat.html";
});

// Demo-only: one button that fills in the provisioned test account.
async function setupAutofill() {
  let accounts;
  try {
    const res = await fetch("/auth/demo-personas", { credentials: "same-origin" });
    if (!res.ok) return;
    accounts = await res.json();
  } catch {
    return;
  }
  if (!Array.isArray(accounts) || accounts.length === 0) return;

  const [{ username, password }] = accounts;
  const btn = document.getElementById("autofill-btn");
  btn.addEventListener("click", () => fillDemoCredentials(username, password));
  btn.hidden = false;
}

function fillDemoCredentials(username, password) {
  document.getElementById("username").value = username;
  document.getElementById("password").value = password;
  submitBtn.focus();
}

setupAutofill();

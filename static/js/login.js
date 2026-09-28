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

  if (!response.ok) {
    errorEl.textContent = "Usuario o contraseña incorrectos.";
    return;
  }

  window.location.href = "/chat.html";
});

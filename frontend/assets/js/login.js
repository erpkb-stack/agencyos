import { api, showFormError } from "./api.js";

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const submit = document.getElementById("login-submit");

async function redirectIfSignedIn() {
  try {
    await api("/auth/me", { redirectOn401: false });
    window.location.replace("/");
  } catch {
    /* not signed in */
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.classList.add("hidden");
  const email = form.email.value.trim();
  const password = form.password.value;
  if (!email || !password) {
    showFormError(errorBox, { message: "Enter your email and password." });
    return;
  }
  submit.disabled = true;
  submit.textContent = "Signing in…";
  try {
    await api("/auth/login", { method: "POST", body: { email, password }, redirectOn401: false });
    window.location.replace("/");
  } catch (error) {
    showFormError(errorBox, error);
    form.password.value = "";
    form.password.focus();
  } finally {
    submit.disabled = false;
    submit.textContent = "Sign in";
  }
});

async function setupDemo() {
  try {
    const status = await api("/auth/demo-login/status", { redirectOn401: false });
    if (!status.enabled) return;
  } catch {
    return;
  }
  const block = document.getElementById("demo-block");
  block.classList.remove("hidden");
  document.getElementById("demo-login").addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      await api("/auth/demo-login", { method: "POST", redirectOn401: false });
      window.location.replace("/");
    } catch (error) {
      showFormError(errorBox, error);
      event.target.disabled = false;
    }
  });
}

redirectIfSignedIn();
setupDemo();

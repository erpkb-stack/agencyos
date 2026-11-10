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
    const result = await api("/auth/login", { method: "POST", body: { email, password }, redirectOn401: false });
    if (result.mfa_required) {
      showMfaStep(result.mfa_token);
      return;
    }
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

// Second step: authenticator or recovery code. The token proves the password step; it expires in 5 minutes.
const mfaForm = document.getElementById("mfa-form");
const mfaSubmit = document.getElementById("mfa-submit");
const codeField = document.getElementById("mfa-code-field");
const recoveryField = document.getElementById("mfa-recovery-field");
const toggle = document.getElementById("mfa-toggle");
let mfaToken = null;
let useRecovery = false;

function showMfaStep(token) {
  mfaToken = token;
  form.classList.add("hidden");
  document.getElementById("demo-block").classList.add("hidden");
  mfaForm.classList.remove("hidden");
  document.getElementById("mfa-code").focus();
}

toggle.addEventListener("click", () => {
  useRecovery = !useRecovery;
  codeField.classList.toggle("hidden", useRecovery);
  recoveryField.classList.toggle("hidden", !useRecovery);
  toggle.textContent = useRecovery ? "Use an authenticator code instead" : "Use a recovery code instead";
  (useRecovery ? document.getElementById("mfa-recovery") : document.getElementById("mfa-code")).focus();
});

document.getElementById("mfa-back").addEventListener("click", () => {
  mfaToken = null;
  mfaForm.classList.add("hidden");
  form.classList.remove("hidden");
  form.password.value = "";
  form.password.focus();
  errorBox.classList.add("hidden");
});

mfaForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.classList.add("hidden");
  const code = document.getElementById("mfa-code").value.trim();
  const recovery = document.getElementById("mfa-recovery").value.trim();
  if (useRecovery ? !recovery : !code) {
    showFormError(errorBox, { message: useRecovery ? "Enter a recovery code." : "Enter the 6-digit code." });
    return;
  }
  mfaSubmit.disabled = true;
  try {
    await api("/auth/login/2fa", { method: "POST", body: useRecovery ? { mfa_token: mfaToken, recovery_code: recovery } : { mfa_token: mfaToken, code }, redirectOn401: false });
    window.location.replace("/");
  } catch (error) {
    showFormError(errorBox, error);
    if (error.message.includes("expired")) document.getElementById("mfa-back").click();
  } finally {
    mfaSubmit.disabled = false;
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

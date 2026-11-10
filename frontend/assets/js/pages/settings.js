// Settings: profile, password change, system status, user management (founder/admin).
import { api, showFormError } from "../api.js";
import { confirmDialog, el, field, fmt, loadingState, modal, statusBadge, toast } from "../ui.js";

function kv(rows) {
  return el("dl", { class: "kv" }, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}

// -- Two-factor authentication -----------------------------------------------------------------

function showRecoveryCodes(codes) {
  const text = codes.join("\n");
  const download = el("a", { class: "btn", download: "agencyos-recovery-codes.txt", href: `data:text/plain;charset=utf-8,${encodeURIComponent(`AgencyOS recovery codes (each works once)\n\n${text}\n`)}` }, "Download");
  const copy = el("button", { class: "btn", type: "button", onclick: async () => { try { await navigator.clipboard.writeText(text); toast("Copied", "success"); } catch { toast("Copy failed; select the codes instead", "error"); } } }, "Copy");
  modal({
    title: "Save your recovery codes",
    body: el("div", {},
      el("p", { class: "notice notice-warning mb-16" }, "Each code signs you in once if you lose your phone. They are shown only now: store them in your password manager."),
      el("pre", { class: "mono recovery-codes" }, text)),
    actions: [copy, download],
  });
}

function securityCard() {
  const body = el("div", { class: "card-body" }, loadingState());
  const card = el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Two-factor authentication")), body);

  async function draw() {
    let s;
    try {
      s = await api("/auth/2fa");
    } catch (err) {
      body.replaceChildren(el("p", { class: "form-error" }, err.message));
      return;
    }
    if (s.enabled) {
      const code = el("input", { class: "input", id: "mfa-manage-code", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "7", placeholder: "123456" });
      const password = el("input", { class: "input", id: "mfa-manage-pw", type: "password", autocomplete: "current-password" });
      const error = el("div", { class: "form-error hidden", role: "alert" });
      body.replaceChildren(
        el("p", {}, statusBadge("ACTIVE"), " On since ", fmt.datetime(s.enabled_at), "."),
        el("p", { class: "small muted" }, `${s.recovery_codes_left} recovery code(s) left.`),
        error,
        el("div", { class: "form-row" }, field("Current authenticator code", code), field("Password (to turn off)", password)),
        el("div", { class: "row" },
          el("button", { class: "btn", type: "button", onclick: async () => {
            try {
              const r = await api("/auth/2fa/recovery-codes", { method: "POST", body: { code: code.value.trim() } });
              showRecoveryCodes(r.recovery_codes);
              draw();
            } catch (err) { showFormError(error, err); }
          } }, "New recovery codes"),
          s.required ? null : el("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
            if (!(await confirmDialog({ title: "Turn off two-factor authentication?", message: "Your account will be protected by your password only.", confirmLabel: "Turn off" }))) return;
            try {
              await api("/auth/2fa/disable", { method: "POST", body: { password: password.value, code: code.value.trim() } });
              toast("Two-factor authentication turned off", "success");
              draw();
            } catch (err) { showFormError(error, err); }
          } }, "Turn off")),
      );
      return;
    }
    const start = el("button", { class: "btn btn-primary", type: "button", onclick: async () => {
      start.disabled = true;
      try {
        const setup = await api("/auth/2fa/setup", { method: "POST" });
        const code = el("input", { class: "input", id: "mfa-confirm-code", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "7", placeholder: "123456" });
        const error = el("div", { class: "form-error hidden", role: "alert" });
        const confirm = el("button", { class: "btn btn-primary", type: "button", onclick: async () => {
          confirm.disabled = true;
          try {
            const r = await api("/auth/2fa/enable", { method: "POST", body: { code: code.value.trim() } });
            toast("Two-factor authentication is on", "success");
            showRecoveryCodes(r.recovery_codes);
            draw();
          } catch (err) {
            showFormError(error, err);
            confirm.disabled = false;
          }
        } }, "Turn on");
        body.replaceChildren(
          el("ol", { class: "list-plain small" },
            el("li", {}, "Open your authenticator app (1Password, Google Authenticator, Authy, Microsoft Authenticator…)."),
            el("li", {}, "Scan this QR code, or enter the key below."),
            el("li", {}, "Type the 6-digit code it shows.")),
          el("div", { class: "qr-wrap" }, el("img", { src: setup.qr_svg, alt: "QR code for your authenticator app", width: "180", height: "180" })),
          el("p", { class: "small" }, "Key: ", el("code", { class: "mono" }, setup.secret.replace(/(.{4})/g, "$1 ").trim())),
          error,
          field("Code from the app", code),
          el("div", { class: "row" }, confirm, el("button", { class: "btn btn-ghost", type: "button", onclick: draw }, "Cancel")),
        );
        code.focus();
      } catch (err) {
        toast(err.message, "error");
        start.disabled = false;
      }
    } }, "Set up");
    body.replaceChildren(
      s.required ? el("p", { class: "notice notice-warning mb-16" }, "Your role requires two-factor authentication. Set it up to continue using AgencyOS.") : null,
      el("p", { class: "muted" }, "Off. Add a second step at sign-in: a code from an authenticator app on your phone."),
      start,
    );
  }
  draw();
  return card;
}

function sessionsCard() {
  const list = el("div", {}, loadingState());
  const card = el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Signed-in devices"),
    el("button", { class: "btn btn-sm", type: "button", onclick: async () => {
      if (!(await confirmDialog({ title: "Sign out other devices?", message: "Every other browser or device signed in to your account will be signed out.", confirmLabel: "Sign out others" }))) return;
      try {
        const r = await api("/auth/sessions/revoke-others", { method: "POST" });
        toast(`${r.revoked} device(s) signed out`, "success");
        load();
      } catch (err) { toast(err.message, "error"); }
    } }, "Sign out others")), list);

  const device = (ua) => {
    if (!ua) return "Unknown device";
    const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : ua.split(" ")[0];
    const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
    return [browser, os].filter(Boolean).join(" on ");
  };
  async function load() {
    try {
      const { items } = await api("/auth/sessions");
      list.replaceChildren(el("div", { class: "card-body" }, el("ul", { class: "timeline" }, items.map((s) => el("li", {},
        el("div", { class: "row" }, el("strong", {}, device(s.user_agent)), s.current ? el("span", { class: "badge badge-success" }, "This device") : null,
          s.current ? null : el("button", { class: "btn btn-sm btn-ghost ml-auto", type: "button", onclick: async () => {
            try { await api(`/auth/sessions/${s.id}`, { method: "DELETE" }); toast("Device signed out", "success"); load(); } catch (err) { toast(err.message, "error"); }
          } }, "Sign out")),
        el("div", { class: "tl-meta" }, `Last active ${fmt.relative(s.last_seen_at)} · signed in ${fmt.datetime(s.created_at)}${s.ip_address ? ` · ${s.ip_address}` : ""}`))))));
    } catch (err) {
      list.replaceChildren(el("p", { class: "form-error card-body" }, err.message));
    }
  }
  load();
  return card;
}

function agentWorkText(o) {
  const where = o.worker_in_process ? "in the web process" : "in a separate worker process";
  return `${o.parallel_agents} agents in parallel per request · ${o.task_workers} requests at a time · ${where}`;
}

function costLimitText(o) {
  const perRequest = Number(o.per_request_limit) > 0 ? `${fmt.money(o.per_request_limit)} per request` : "no per-request limit";
  const perMonth = Number(o.per_month_limit) > 0 ? `${fmt.money(o.per_month_limit)} per month` : "no monthly limit";
  return `${perRequest} · ${perMonth}`;
}

function configured(flag) {
  return flag ? el("span", { class: "badge badge-success" }, "Configured") : el("span", { class: "badge" }, "Not set");
}

function passwordCard() {
  const error = el("div", { class: "form-error hidden", role: "alert" });
  const form = el(
    "form",
    { novalidate: true },
    error,
    el("div", { class: "field" }, el("label", { for: "cur-pw" }, "Current password"), el("input", { class: "input", id: "cur-pw", name: "current", type: "password", autocomplete: "current-password", required: true })),
    el("div", { class: "field" }, el("label", { for: "new-pw" }, "New password"), el("input", { class: "input", id: "new-pw", name: "next", type: "password", autocomplete: "new-password", required: true }), el("span", { class: "field-hint" }, "At least 10 characters, mixed case, and a digit. Your other sessions will be signed out.")),
    el("button", { class: "btn btn-primary", type: "submit" }, "Change password"),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await api("/auth/change-password", { method: "POST", body: { current_password: form.current.value, new_password: form.next.value } });
      form.reset();
      toast("Password changed. Other sessions were signed out.", "success");
    } catch (err) {
      showFormError(error, err);
    } finally {
      button.disabled = false;
    }
  });
  return el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Change password")), el("div", { class: "card-body" }, form));
}

function usersCard(currentUser) {
  const tbody = el("tbody");
  const table = el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Name", "Email", "Role", "Status", "Last login", ""].map((h) => el("th", {}, h)))), tbody));
  const privileged = ["FOUNDER", "ADMIN"];
  const canManage = (u) => u.id !== currentUser.id && (currentUser.role === "FOUNDER" || !privileged.includes(u.role));

  async function loadUsers() {
    try {
      const users = await api("/auth/users");
      tbody.replaceChildren(
        ...users.map((u) =>
          el("tr", {}, el("td", {}, u.full_name), el("td", {}, u.email), el("td", {}, fmt.label(u.role)), el("td", {}, statusBadge(u.is_active ? "ACTIVE" : "INACTIVE")), el("td", {}, fmt.datetime(u.last_login_at)),
            el("td", {}, canManage(u) ? el("button", { class: "btn btn-sm", type: "button", onclick: async () => {
              const deactivate = u.is_active;
              if (deactivate && !(await confirmDialog({ title: `Deactivate ${u.full_name}?`, message: "They are signed out everywhere and can't sign in until reactivated. Their records stay.", confirmLabel: "Deactivate" }))) return;
              try {
                await api(`/auth/users/${u.id}`, { method: "PATCH", body: { is_active: !deactivate } });
                toast(deactivate ? "User deactivated" : "User reactivated", "success");
                loadUsers();
              } catch (err) {
                toast(err.message, "error");
              }
            } }, u.is_active ? "Deactivate" : "Reactivate") : null)),
        ),
      );
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "6" }, err.message)));
    }
  }

  const roles = currentUser.role === "FOUNDER" ? ["STAFF", "READ_ONLY", "ADMIN", "FOUNDER"] : ["STAFF", "READ_ONLY"];
  const error = el("div", { class: "form-error hidden", role: "alert" });
  const form = el(
    "form",
    { novalidate: true },
    error,
    el("div", { class: "form-row" },
      el("div", { class: "field" }, el("label", { for: "nu-name" }, "Full name"), el("input", { class: "input", id: "nu-name", name: "full_name", required: true })),
      el("div", { class: "field" }, el("label", { for: "nu-email" }, "Email"), el("input", { class: "input", id: "nu-email", name: "email", type: "email", required: true })),
    ),
    el("div", { class: "form-row" },
      el("div", { class: "field" }, el("label", { for: "nu-pw" }, "Initial password"), el("input", { class: "input", id: "nu-pw", name: "password", type: "password", autocomplete: "new-password", required: true })),
      el("div", { class: "field" }, el("label", { for: "nu-role" }, "Role"), el("select", { class: "input", id: "nu-role", name: "role" }, roles.map((r) => el("option", { value: r }, fmt.label(r))))),
    ),
    el("button", { class: "btn btn-primary", type: "submit" }, "Add user"),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const user = await api("/auth/users", { method: "POST", body: { full_name: form.full_name.value, email: form.email.value, password: form.password.value, role: form.role.value } });
      form.reset();
      toast(`Added ${user.email}`, "success");
      loadUsers();
    } catch (err) {
      showFormError(error, err);
    } finally {
      button.disabled = false;
    }
  });

  loadUsers();
  return el(
    "section",
    { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "Team members")),
    table,
    el("div", { class: "card-body" }, el("h3", { class: "mb-16" }, "Add a team member"), form),
  );
}

export function renderSettings({ view, user, system }) {
  const profile = el(
    "section",
    { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "Your profile")),
    el("div", { class: "card-body" }, kv([["Name", user.full_name], ["Email", user.email], ["Role", fmt.label(user.role)], ["Last sign-in", fmt.datetime(user.last_login_at)]])),
  );
  const sys = el(
    "section",
    { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, "System status")),
    el(
      "div",
      { class: "card-body" },
      kv([
        ["Version", system.version],
        ["Environment", system.environment],
        ["Database", system.database_backend],
        ["LLM provider", system.llm.provider],
        ["OpenAI key", configured(system.llm.openai_configured)],
        ["OpenAI model", system.llm.openai_model],
        ["Anthropic key", configured(system.llm.anthropic_configured)],
        ["Anthropic model", system.llm.anthropic_model],
        ["Demo login", system.demo_login_enabled ? "Enabled (development)" : "Disabled"],
        ["External sending", system.external_sending_enabled ? "Enabled" : "Disabled — drafts only"],
        ["Session length", `${system.session_ttl_hours} hours`],
        ["Agent work", agentWorkText(system.orchestrator)],
        ["Queued tasks", String(system.orchestrator.queued)],
        ["AI cost limits", costLimitText(system.orchestrator)],
        ["AI spend this month (est.)", fmt.money(system.orchestrator.month_spend)],
        ["API docs (/docs)", system.api_docs_enabled ? "On" : "Off"],
      ]),
      system.warnings?.length ? el("div", { class: "notice notice-warning mt-16" }, el("strong", {}, "Configuration warnings"), el("ul", { class: "list-plain" }, system.warnings.map((w) => el("li", {}, w)))) : el("p", { class: "small score-high mt-16" }, "✓ No configuration warnings."),
      el("p", { class: "field-hint mt-16" }, "Configuration is read from the .env file on the server. API keys are never sent to the browser."),
    ),
  );

  const isAdmin = user.role === "FOUNDER" || user.role === "ADMIN";
  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Settings"), el("p", { class: "muted" }, "Your account, team and system configuration."))),
    el("div", { class: "grid two-col mb-16" }, profile, passwordCard()),
    el("div", { class: "grid two-col mb-16" }, securityCard(), sessionsCard()),
    el("div", { class: "grid mb-16" }, sys),
    ...(isAdmin ? [usersCard(user)] : []),
  );
}

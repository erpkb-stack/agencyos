// Settings: profile, password change, system status, user management (founder/admin).
import { api, showFormError } from "../api.js";
import { el, fmt, statusBadge, toast } from "../ui.js";

function kv(rows) {
  return el("dl", { class: "kv" }, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
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
  const table = el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Name", "Email", "Role", "Status", "Last login"].map((h) => el("th", {}, h)))), tbody));

  async function loadUsers() {
    try {
      const users = await api("/auth/users");
      tbody.replaceChildren(
        ...users.map((u) =>
          el("tr", {}, el("td", {}, u.full_name), el("td", {}, u.email), el("td", {}, fmt.label(u.role)), el("td", {}, statusBadge(u.is_active ? "ACTIVE" : "PAUSED")), el("td", {}, fmt.datetime(u.last_login_at))),
        ),
      );
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "5" }, err.message)));
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
      ]),
      el("p", { class: "field-hint mt-16" }, "Configuration is read from the .env file on the server. API keys are never sent to the browser."),
    ),
  );

  const isAdmin = user.role === "FOUNDER" || user.role === "ADMIN";
  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Settings"), el("p", { class: "muted" }, "Your account, team and system configuration."))),
    el("div", { class: "grid two-col mb-16" }, profile, passwordCard()),
    el("div", { class: "grid mb-16" }, sys),
    isAdmin ? usersCard(user) : null,
  );
}

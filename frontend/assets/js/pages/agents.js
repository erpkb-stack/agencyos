// Agent management: registry, details, editing, custom agents, test runs, history.
import { api, showFormError } from "../api.js";
import { canAdmin, canWrite, confirmDialog, el, errorState, field, fmt, loadingState, modal, statusBadge, toast } from "../ui.js";

const TOOL_LABELS = {
  delegate: "Delegate to other agents",
  draft_content: "Draft content and reports",
  read_business_data: "Read agency metrics (read-only)",
  propose_external_action: "Propose external actions (become approval requests)",
};

function successRate(stats) {
  return stats.executions ? `${Math.round((stats.succeeded / stats.executions) * 100)}%` : "—";
}

function providerLabel(a) {
  return `${a.effective_provider} / ${a.effective_model}${a.model_provider === "default" ? " (server default)" : ""}`;
}

export async function renderAgents(ctx) {
  if (ctx.params[0]) return renderAgentDetail(ctx, Number(ctx.params[0]));
  const { view, user } = ctx;
  view.replaceChildren(loadingState());
  let data;
  try {
    data = await api("/agents");
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderAgents(ctx)));
    return;
  }
  const cards = data.items.map((a) =>
    el(
      "a",
      { class: "card agent-card", href: `#/agents/${a.id}` },
      el("div", { class: "row" }, el("h2", {}, a.name), el("span", { class: "spacer" }), statusBadge(a.is_active ? "ACTIVE" : "PAUSED")),
      el("div", { class: "small faint" }, `${a.department}${a.is_built_in ? "" : " · custom"}`),
      el("p", {}, a.description || "No description."),
      el("div", { class: "small muted" }, providerLabel(a)),
      el(
        "div",
        { class: "agent-stats" },
        el("span", {}, el("strong", {}, fmt.int(a.stats.executions)), " runs"),
        el("span", {}, el("strong", {}, successRate(a.stats)), " success"),
        el("span", {}, el("strong", {}, fmt.int(a.stats.open_tasks)), " open tasks"),
        el("span", {}, a.last_execution_at ? `last run ${fmt.relative(a.last_execution_at)}` : "never run"),
      ),
    ),
  );
  view.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", {}, "Agents"), el("p", { class: "muted" }, `${data.items.length} agents. The AI CEO delegates to active specialists; edit prompts and models per agent.`)),
      canAdmin(user) ? el("button", { class: "btn btn-primary", type: "button", onclick: () => openCreate(data.options) }, "New agent") : null,
    ),
    el("div", { class: "agent-grid" }, cards),
  );
}

function agentForm(prefix, values, options, { builtIn }) {
  const f = {
    name: el("input", { class: "input", id: `${prefix}-name`, value: values.name ?? "", required: true, maxlength: "120" }),
    department: el("input", { class: "input", id: `${prefix}-dept`, value: values.department ?? "", required: true, maxlength: "120" }),
    description: el("input", { class: "input", id: `${prefix}-desc`, value: values.description ?? "", maxlength: "1000" }),
    capabilities: el("input", { class: "input", id: `${prefix}-caps`, value: (values.capabilities ?? []).join(", ") }),
    provider: el("select", { class: "input", id: `${prefix}-prov` }, options.providers.map((p) => el("option", { value: p, selected: p === (values.model_provider ?? "default") }, p === "default" ? "Server default (LLM_PROVIDER)" : p))),
    model: el("input", { class: "input", id: `${prefix}-model`, value: values.model_name ?? "", maxlength: "100", placeholder: "Leave empty for the provider's default model" }),
    temperature: el("input", { class: "input", id: `${prefix}-temp`, type: "number", min: "0", max: "1", step: "0.1", value: String(values.temperature ?? 0.3) }),
    prompt: el("textarea", { class: "input tall", id: `${prefix}-prompt`, required: true, maxlength: "20000" }),
  };
  f.prompt.value = values.system_prompt ?? "";
  const toolBoxes = builtIn
    ? null
    : options.custom_agent_tools.map((t) => el("label", { class: "check" }, el("input", { type: "checkbox", value: t, checked: (values.allowed_tools ?? ["draft_content"]).includes(t) }), TOOL_LABELS[t] ?? t));
  const body = el(
    "div",
    {},
    el("div", { class: "form-row" }, field("Name", f.name), field("Department", f.department)),
    field("Description", f.description),
    field("Capabilities", f.capabilities, "Comma-separated, shown to the team."),
    el("div", { class: "form-row" }, field("Model provider", f.provider), field("Model", f.model)),
    field("Temperature", f.temperature, "0 = focused, 1 = creative. Used by OpenAI and mock; current Claude models do not accept it, so Anthropic calls ignore it."),
    field("System prompt", f.prompt, "Application safety rules are added above this prompt and cannot be overridden by it."),
    toolBoxes ? el("div", { class: "field" }, el("label", {}, "Permissions"), toolBoxes) : null,
  );
  const read = () => ({
    name: f.name.value,
    department: f.department.value,
    description: f.description.value,
    capabilities: f.capabilities.value.split(",").map((s) => s.trim()).filter(Boolean),
    model_provider: f.provider.value,
    model_name: f.model.value.trim(),
    temperature: Number(f.temperature.value),
    system_prompt: f.prompt.value,
    ...(toolBoxes ? { allowed_tools: toolBoxes.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value) } : {}),
  });
  return { body, read };
}

function openCreate(options) {
  const error = el("div", { class: "form-error hidden", role: "alert" });
  const form = agentForm("new", { system_prompt: "You are a … specialist for a small marketing and automation agency. Your job is to …" }, options, { builtIn: false });
  const save = el("button", { class: "btn btn-primary", type: "button" }, "Create agent");
  const handle = modal({ title: "New agent", wide: true, body: el("div", {}, error, form.body), actions: [el("button", { class: "btn", type: "button", onclick: () => handle.close() }, "Cancel"), save] });
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const agent = await api("/agents", { method: "POST", body: form.read() });
      handle.close();
      toast(`${agent.name} created`, "success");
      window.location.hash = `#/agents/${agent.id}`;
    } catch (err) {
      showFormError(error, err);
    } finally {
      save.disabled = false;
    }
  });
}

async function renderAgentDetail(ctx, id) {
  const { view, user } = ctx;
  view.replaceChildren(loadingState());
  let agent, options, executions, tasks;
  try {
    [agent, { options }, executions, tasks] = await Promise.all([
      api(`/agents/${id}`),
      api("/agents"),
      api(`/agents/${id}/executions?page_size=15`),
      api(`/tasks?agent_id=${id}&page_size=15`),
    ]);
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderAgentDetail(ctx, id)));
    return;
  }
  const admin = canAdmin(user);
  const reload = () => renderAgentDetail(ctx, id);

  const toggle = admin && !agent.is_ceo
    ? el("button", { class: "btn", type: "button", onclick: async () => {
        try {
          await api(`/agents/${id}`, { method: "PATCH", body: { is_active: !agent.is_active } });
          toast(agent.is_active ? "Agent deactivated" : "Agent activated", "success");
          reload();
        } catch (err) {
          toast(err.message, "error");
        }
      } }, agent.is_active ? "Deactivate" : "Activate")
    : null;

  // Test run
  const testError = el("div", { class: "form-error hidden", role: "alert" });
  const testInput = el("textarea", { class: "input", id: "test-input", maxlength: "8000", placeholder: "e.g. Draft a follow-up plan for an HVAC prospect that asked for pricing." });
  const testBtn = el("button", { class: "btn btn-primary", type: "submit", disabled: !agent.is_active }, "Run test");
  const testForm = el("form", {}, testError, field("Instruction", testInput, agent.is_active ? "Runs this agent once. Proposals still only create approval requests." : "Activate the agent to test it."), testBtn);
  testForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!testInput.value.trim()) return;
    testBtn.disabled = true;
    try {
      const { task_id } = await api(`/agents/${id}/test`, { method: "POST", body: { instructions: testInput.value.trim() } });
      window.location.hash = `#/tasks/${task_id}`;
    } catch (err) {
      showFormError(testError, err);
      testBtn.disabled = false;
    }
  });

  // Configuration
  let config;
  if (admin) {
    const error = el("div", { class: "form-error hidden", role: "alert" });
    const form = agentForm("edit", agent, options, { builtIn: agent.is_built_in });
    const save = el("button", { class: "btn btn-primary", type: "submit" }, "Save changes");
    const reset = agent.is_built_in
      ? el("button", { class: "btn", type: "button", onclick: async () => {
          if (!(await confirmDialog({ title: "Reset prompt?", message: "Replace the current system prompt with the built-in default.", confirmLabel: "Reset" }))) return;
          try {
            await api(`/agents/${id}/reset-prompt`, { method: "POST" });
            toast("Prompt reset to default", "success");
            reload();
          } catch (err) {
            toast(err.message, "error");
          }
        } }, "Reset prompt to default")
      : null;
    config = el("form", {}, error, form.body, el("div", { class: "row" }, save, reset));
    config.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      try {
        await api(`/agents/${id}`, { method: "PATCH", body: form.read() });
        toast("Agent saved", "success");
        reload();
      } catch (err) {
        showFormError(error, err);
        save.disabled = false;
      }
    });
  } else {
    config = el("div", {}, el("p", { class: "small faint" }, "Only Founders and Admins can edit agents."), el("div", { class: "pre small" }, agent.system_prompt));
  }

  const execRows = executions.items.map((e) =>
    el("tr", {}, el("td", {}, el("a", { href: `#/tasks/${e.task_id}` }, `#${e.task_id}`)), el("td", {}, statusBadge(e.status)), el("td", {}, `${e.provider} / ${e.model}`), el("td", {}, e.prompt_tokens != null ? `${fmt.int(e.prompt_tokens)} / ${fmt.int(e.completion_tokens)}` : "—"), el("td", {}, e.estimated_cost ? `$${e.estimated_cost}` : "—"), el("td", {}, e.duration_ms != null ? `${fmt.int(e.duration_ms)} ms` : "—"), el("td", {}, fmt.datetime(e.started_at)), el("td", { class: "wrap small" }, e.error ?? "")),
  );
  const taskRows = tasks.items.map((t) =>
    el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/tasks/${t.id}`) }, el("td", {}, `#${t.id}`), el("td", { class: "wrap" }, t.title), el("td", {}, statusBadge(t.phase)), el("td", {}, fmt.datetime(t.created_at))),
  );

  view.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("a", { href: "#/agents", class: "small" }, "← All agents"), el("h1", {}, agent.name), el("p", { class: "muted" }, agent.description)),
      el("div", { class: "row" }, statusBadge(agent.is_active ? "ACTIVE" : "PAUSED"), toggle),
    ),
    el(
      "div",
      { class: "grid two-col" },
      el(
        "section",
        { class: "card" },
        el("div", { class: "card-head" }, el("h2", {}, "Details")),
        el(
          "div",
          { class: "card-body" },
          el("dl", { class: "kv" }, [
            ["Key", el("span", { class: "mono" }, agent.key)],
            ["Department", agent.department],
            ["Type", agent.is_built_in ? "Built-in" : "Custom"],
            ["Model", providerLabel(agent)],
            ["Temperature", String(agent.temperature)],
            ["Capabilities", agent.capabilities.join(", ") || "—"],
            ["Permissions", el("ul", { class: "list-plain" }, agent.allowed_tools.map((t) => el("li", {}, TOOL_LABELS[t] ?? t)))],
            ["Runs", `${fmt.int(agent.stats.executions)} (${successRate(agent.stats)} success, ${fmt.int(agent.stats.tokens)} tokens${agent.stats.estimated_cost ? `, ~$${agent.stats.estimated_cost}` : ""})`],
            ["Created", fmt.datetime(agent.created_at)],
            ["Last execution", fmt.datetime(agent.last_execution_at)],
          ].flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
        ),
      ),
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Test this agent")), el("div", { class: "card-body" }, canWrite(user) ? testForm : el("p", { class: "muted" }, "Your role is read-only."))),
    ),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Configuration")), el("div", { class: "card-body" }, config)),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, `Assigned tasks (${tasks.total})`)), tasks.items.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Task", "Title", "Status", "Created"].map((h) => el("th", {}, h)))), el("tbody", {}, taskRows))) : el("div", { class: "card-body empty-inline" }, "No tasks yet.")),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, `Execution history (${executions.total})`)), executions.items.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Task", "Status", "Provider / model", "Tokens in / out", "Est. cost", "Duration", "Started", "Error"].map((h) => el("th", {}, h)))), el("tbody", {}, execRows))) : el("div", { class: "card-body empty-inline" }, "This agent has not run yet.")),
  );
}

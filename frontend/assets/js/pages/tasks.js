// Tasks: filterable list, manual assignment, and a live detail view with output and history.
import { api, showFormError } from "../api.js";
import { canAdmin, canWrite, confirmDialog, el, errorState, field, fmt, isActiveStatus, loadingState, modal, poll, statusBadge, toast } from "../ui.js";
import { renderOutput, taskTree } from "./task-view.js";
import { approvalCard } from "./approvals.js";

const STATUSES = ["PENDING", "ASSIGNED", "RUNNING", "AWAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED"];
const PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT"];
const PAGE_SIZE = 25;

export async function renderTasks(ctx) {
  if (ctx.params[0]) return renderTaskDetail(ctx, Number(ctx.params[0]));
  const { view, user } = ctx;
  const state = { status: "", root: false, q: "", page: 1 };

  const status = el("select", { class: "input", "aria-label": "Status" }, el("option", { value: "" }, "All statuses"), STATUSES.map((s) => el("option", { value: s }, fmt.label(s))));
  const search = el("input", { class: "input search", type: "search", placeholder: "Search titles…", "aria-label": "Search tasks" });
  const root = el("input", { type: "checkbox" });
  const tbody = el("tbody");
  const pageInfo = el("span", { class: "small muted" });
  const prev = el("button", { class: "btn btn-sm", type: "button" }, "Previous");
  const next = el("button", { class: "btn btn-sm", type: "button" }, "Next");
  let stop = null;

  async function load() {
    const query = new URLSearchParams({ page: String(state.page), page_size: String(PAGE_SIZE) });
    if (state.status) query.set("status", state.status);
    if (state.root) query.set("root_only", "true");
    if (state.q) query.set("q", state.q);
    try {
      const data = await api(`/tasks?${query}`);
      tbody.replaceChildren(
        ...(data.items.length
          ? data.items.map((t) =>
              el(
                "tr",
                { class: "clickable", onclick: () => (window.location.hash = `#/tasks/${t.id}`) },
                el("td", {}, `#${t.id}`),
                el("td", { class: "wrap" }, t.title, t.parent_task_id ? el("div", { class: "small faint" }, `sub-task of #${t.parent_task_id}`) : null),
                el("td", {}, t.agent?.name ?? "—"),
                el("td", {}, statusBadge(t.phase)),
                el("td", {}, fmt.label(t.priority)),
                el("td", {}, fmt.datetime(t.created_at)),
              ),
            )
          : [el("tr", {}, el("td", { colspan: "6", class: "empty-inline" }, state.status || state.q || state.root ? "No tasks match these filters." : "No tasks yet. Ask the AI CEO something, or assign a task to an agent."))]),
      );
      const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
      pageInfo.textContent = `${fmt.int(data.total)} tasks · page ${state.page} of ${pages}`;
      prev.disabled = state.page <= 1;
      next.disabled = state.page >= pages;
      return data.items.some((t) => isActiveStatus(t.status));
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "6" }, err.message)));
      return false;
    }
  }

  function refresh() {
    stop?.();
    load().then((active) => {
      if (active) stop = poll(load, 2000);
    });
  }

  status.addEventListener("change", () => { state.status = status.value; state.page = 1; refresh(); });
  root.addEventListener("change", () => { state.root = root.checked; state.page = 1; refresh(); });
  let debounce;
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.q = search.value.trim(); state.page = 1; refresh(); }, 300);
  });
  prev.addEventListener("click", () => { state.page -= 1; refresh(); });
  next.addEventListener("click", () => { state.page += 1; refresh(); });

  view.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", {}, "Tasks"), el("p", { class: "muted" }, "Every request the AI CEO plans and every sub-task it delegates.")),
      canWrite(user) ? el("button", { class: "btn btn-primary", type: "button", onclick: openCreate }, "Assign task") : null,
    ),
    el(
      "section",
      { class: "card" },
      el("div", { class: "card-body" }, el("div", { class: "toolbar" }, search, status, el("label", { class: "check" }, root, "Top-level only"))),
      el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["ID", "Title", "Agent", "Status", "Priority", "Created"].map((h) => el("th", {}, h)))), tbody)),
      el("div", { class: "pager" }, pageInfo, prev, next),
    ),
  );
  refresh();
  return () => stop?.();
}

async function openCreate() {
  let agents;
  try {
    agents = (await api("/agents")).items.filter((a) => a.is_active);
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const error = el("div", { class: "form-error hidden", role: "alert" });
  const agent = el("select", { class: "input", id: "nt-agent" }, agents.map((a) => el("option", { value: String(a.id) }, a.name)));
  const title = el("input", { class: "input", id: "nt-title", maxlength: "300" });
  const instructions = el("textarea", { class: "input", id: "nt-instr", maxlength: "8000" });
  const priority = el("select", { class: "input", id: "nt-prio" }, PRIORITIES.map((p) => el("option", { value: p, selected: p === "NORMAL" }, fmt.label(p))));
  const save = el("button", { class: "btn btn-primary", type: "button" }, "Assign");
  const handle = modal({
    title: "Assign a task",
    wide: true,
    body: el("div", {}, error, field("Agent", agent, "Choosing the AI CEO lets it plan and delegate."), field("Title", title), field("Instructions", instructions), field("Priority", priority)),
    actions: [el("button", { class: "btn", type: "button", onclick: () => handle.close() }, "Cancel"), save],
  });
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const task = await api("/tasks", { method: "POST", body: { agent_id: Number(agent.value), title: title.value.trim(), instructions: instructions.value.trim(), priority: priority.value } });
      handle.close();
      window.location.hash = `#/tasks/${task.id}`;
    } catch (err) {
      showFormError(error, err);
      save.disabled = false;
    }
  });
}

function treeActive(t) {
  return isActiveStatus(t.status) || (t.children ?? []).some(treeActive);
}

async function renderTaskDetail(ctx, id) {
  const { view, user } = ctx;
  let stop = null;
  view.replaceChildren(loadingState());

  async function draw() {
    const t = await api(`/tasks/${id}`);
    const actions = [];
    if (canWrite(user) && ["PENDING", "ASSIGNED", "RUNNING", "AWAITING_APPROVAL"].includes(t.status)) {
      actions.push(el("button", { class: "btn", type: "button", onclick: async () => {
        if (!(await confirmDialog({ title: "Cancel task?", message: "The task and its unfinished sub-tasks are cancelled and their pending approvals expire.", confirmLabel: "Cancel task" }))) return;
        try {
          await api(`/tasks/${id}/cancel`, { method: "POST" });
          toast("Task cancelled", "success");
          draw();
        } catch (err) {
          toast(err.message, "error");
        }
      } }, "Cancel task"));
    }
    if (canWrite(user) && t.status === "FAILED") {
      actions.push(el("button", { class: "btn btn-primary", type: "button", onclick: async () => {
        try {
          await api(`/tasks/${id}/retry`, { method: "POST" });
          toast("Retry started", "success");
          watch();
        } catch (err) {
          toast(err.message, "error");
        }
      } }, "Retry"));
    }

    const execRows = t.executions.map((e) =>
      el("tr", {}, el("td", {}, e.agent ?? "—"), el("td", {}, statusBadge(e.status)), el("td", {}, `${e.provider} / ${e.model}`), el("td", {}, e.prompt_tokens != null ? `${fmt.int(e.prompt_tokens)} / ${fmt.int(e.completion_tokens)}` : "—"), el("td", {}, e.estimated_cost ? `$${e.estimated_cost}` : "—"), el("td", {}, e.duration_ms != null ? `${fmt.int(e.duration_ms)} ms` : "—"), el("td", {}, fmt.datetime(e.started_at)), el("td", { class: "wrap small" }, e.error ?? "")),
    );

    view.replaceChildren(
      el(
        "div",
        { class: "page-head" },
        el("div", {}, el("a", { href: t.parent_task_id ? `#/tasks/${t.parent_task_id}` : "#/tasks", class: "small" }, t.parent_task_id ? `← Parent task #${t.parent_task_id}` : "← All tasks"), el("h1", { class: "pre" }, t.title), el("p", { class: "muted" }, `#${t.id} · ${t.agent?.name ?? "Unassigned"} · ${fmt.label(t.task_type)}`)),
        el("div", { class: "row" }, statusBadge(t.phase), actions),
      ),
      el("div", { class: t.error ? "form-error" : "hidden", role: "alert" }, t.error ?? ""),
      el(
        "div",
        { class: "grid two-col" },
        el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Result")), el("div", { class: "card-body" }, renderOutput(t))),
        el(
          "div",
          { class: "grid" },
          el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Details")), el("div", { class: "card-body" }, el("dl", { class: "kv" }, [
            ["Status", statusBadge(t.phase)],
            ["Priority", fmt.label(t.priority)],
            ["Attempts", `${t.attempts} of ${t.max_attempts}`],
            ["Delegation depth", String(t.depth)],
            ["Conversation", t.conversation_id ? el("a", { href: `#/ceo/${t.conversation_id}` }, `Open #${t.conversation_id}`) : "—"],
            ["Created", fmt.datetime(t.created_at)],
            ["Started", fmt.datetime(t.started_at)],
            ["Finished", fmt.datetime(t.completed_at)],
          ].flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])))),
          t.children.length ? el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, `Delegated to ${t.children.length} agent${t.children.length > 1 ? "s" : ""}`)), el("div", { class: "card-body" }, taskTree(t, { linkRoot: false }))) : null,
          t.approvals.length ? el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Approval requests")), el("div", { class: "card-body" }, t.approvals.map((a) => approvalCard(a, { canDecide: canAdmin(user), onDecided: draw })))) : null,
          el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Instructions")), el("div", { class: "card-body pre small" }, t.input?.instructions ?? "—")),
        ),
      ),
      el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, `Executions (${t.executions.length})`)), t.executions.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Agent", "Status", "Provider / model", "Tokens in / out", "Est. cost", "Duration", "Started", "Error"].map((h) => el("th", {}, h)))), el("tbody", {}, execRows))) : el("div", { class: "card-body empty-inline" }, "No provider calls yet.")),
    );
    return treeActive(t);
  }

  function watch() {
    stop?.();
    stop = poll(draw, 1500);
  }

  try {
    if (await draw()) watch();
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderTaskDetail(ctx, id)));
  }
  return () => stop?.();
}

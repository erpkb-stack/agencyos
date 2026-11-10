// Projects: list, detail with tasks (dependencies, assignees), deliverables, timeline and progress.
import { api } from "../api.js";
import { canAdmin, canWrite, confirmDialog, el, errorState, fmt, formDialog, kvList, loadingState, pager, progressBar, statusBadge, toast } from "../ui.js";

const STATUSES = ["PLANNED", "IN_PROGRESS", "REVIEW", "AWAITING_CLIENT", "COMPLETED", "CANCELLED"];
const TASK_STATUSES = ["TODO", "IN_PROGRESS", "BLOCKED", "DONE"];
const DELIVERABLE_STATUSES = ["DRAFT", "IN_REVIEW", "APPROVED", "DELIVERED"];
const PAGE_SIZE = 25;

let optionsCache = null;
async function options() {
  optionsCache ??= await api("/projects/options");
  return optionsCache;
}

export async function openProjectForm(existing, defaults, onSaved) {
  optionsCache = null;
  const opts = await options();
  if (!existing && !opts.clients.length) {
    toast("Add a client first: every project belongs to a client", "error");
    return;
  }
  const defs = [
    { name: "name", label: "Project name", required: true },
    existing ? null : { name: "client_id", label: "Client", type: "select", options: opts.clients.map((c) => [c.id, c.name]), numeric: true },
    { name: "status", label: "Status", type: "select", options: STATUSES, default: "PLANNED" },
    { name: "budget", label: "Budget (USD)", type: "number", min: "0", step: "0.01", money: true },
    { name: "start_date", label: "Start date", type: "date" },
    { name: "due_date", label: "Due date", type: "date" },
    { name: "description", label: "Description", type: "textarea" },
    existing ? null : { name: "scope", label: "Scope", type: "textarea", hint: "What is included. Later changes need a stated reason." },
    { name: "agent_ids", label: "Assigned agents", type: "checkboxes", options: opts.agents.map((a) => [a.id, a.name]), numeric: true },
  ].filter(Boolean);
  const values = existing ? { ...existing, agent_ids: existing.agents.map((a) => a.id) } : { status: "PLANNED", ...defaults };
  formDialog({
    title: existing ? `Edit ${existing.name}` : "New project",
    defs,
    values,
    submit: async (data, { close }) => {
      const saved = existing ? await api(`/projects/${existing.id}`, { method: "PATCH", body: data }) : await api("/projects", { method: "POST", body: data });
      close();
      toast(existing ? "Project saved" : "Project created", "success");
      onSaved(saved);
    },
  });
}

export async function renderProjects(ctx) {
  if (ctx.params[0]) return renderProject(ctx, Number(ctx.params[0]));
  const { view, user } = ctx;
  const state = { q: "", status: "", client_id: "", page: 1 };
  const search = el("input", { class: "input search", type: "search", placeholder: "Search projects…", "aria-label": "Search projects" });
  const status = el("select", { class: "input", "aria-label": "Status" }, el("option", { value: "" }, "All statuses"), STATUSES.map((s) => el("option", { value: s }, fmt.label(s))));
  const client = el("select", { class: "input", "aria-label": "Client" }, el("option", { value: "" }, "All clients"));
  const tbody = el("tbody");
  const pagerHost = el("div");
  options().then((o) => client.append(...o.clients.map((c) => el("option", { value: String(c.id) }, c.name)))).catch(() => {});

  async function load() {
    const q = new URLSearchParams({ page: String(state.page), page_size: String(PAGE_SIZE) });
    if (state.q) q.set("q", state.q);
    if (state.status) q.set("status", state.status);
    if (state.client_id) q.set("client_id", state.client_id);
    try {
      const data = await api(`/projects?${q}`);
      tbody.replaceChildren(...(data.items.length
        ? data.items.map((p) => el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/projects/${p.id}`) },
            el("td", { class: "wrap" }, el("strong", {}, p.name)),
            el("td", {}, p.client_name ?? "—"),
            el("td", {}, statusBadge(p.status)),
            el("td", {}, progressBar(p.completion_percent)),
            el("td", {}, `${p.tasks_done}/${p.tasks_total}`),
            el("td", {}, p.due_date ?? "—")))
        : [el("tr", {}, el("td", { colspan: "6", class: "empty-inline" }, state.q || state.status || state.client_id ? "No projects match." : "No projects yet."))]));
      pagerHost.replaceChildren(pager(state, data.total, PAGE_SIZE, load));
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "6" }, err.message)));
    }
  }
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); state.page = 1; load(); }, 300); });
  status.addEventListener("change", () => { state.status = status.value; state.page = 1; load(); });
  client.addEventListener("change", () => { state.client_id = client.value; state.page = 1; load(); });

  view.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, "Projects"), el("p", { class: "muted" }, "Delivery work for clients. Progress is calculated from each project's tasks.")),
      canWrite(user) ? el("button", { class: "btn btn-primary", type: "button", onclick: () => openProjectForm(null, {}, (p) => (window.location.hash = `#/projects/${p.id}`)) }, "New project") : null),
    el("section", { class: "card" },
      el("div", { class: "card-body" }, el("div", { class: "toolbar" }, search, status, client)),
      el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Project", "Client", "Status", "Progress", "Tasks", "Due"].map((h) => el("th", {}, h)))), tbody)),
      pagerHost),
  );
  load();
}

async function renderProject(ctx, id) {
  const { view, user } = ctx;
  view.replaceChildren(loadingState());
  let p, opts;
  try {
    [p, opts] = await Promise.all([api(`/projects/${id}`), options()]);
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderProject(ctx, id)));
    return;
  }
  const reload = () => renderProject(ctx, id);
  const writer = canWrite(user);
  const base = `/projects/${id}`;
  const act = async (fn, ok) => {
    try {
      await fn();
      if (ok) toast(ok, "success");
      reload();
    } catch (err) {
      toast(err.message, "error");
      reload();
    }
  };

  const statusSelect = el("select", { class: "input inline-select", "aria-label": "Project status", disabled: !writer }, STATUSES.map((s) => el("option", { value: s, selected: s === p.status }, fmt.label(s))));
  statusSelect.addEventListener("change", () => act(() => api(base, { method: "PATCH", body: { status: statusSelect.value } }), "Status updated"));

  const taskDefs = (task) => [
    { name: "title", label: "Title", required: true, full: true },
    { name: "description", label: "Description", type: "textarea" },
    { name: "status", label: "Status", type: "select", options: TASK_STATUSES, default: "TODO" },
    { name: "due_date", label: "Due date", type: "date" },
    { name: "assignee_user_id", label: "Assigned person", type: "select", options: [["", "Nobody"], ...opts.users.map((u) => [u.id, u.name])], numeric: true },
    { name: "assignee_agent_id", label: "Assigned agent", type: "select", options: [["", "None"], ...opts.agents.map((a) => [a.id, a.name])], numeric: true },
    { name: "depends_on_task_id", label: "Depends on", type: "select", options: [["", "Nothing"], ...p.tasks.filter((t) => !task || t.id !== task.id).map((t) => [t.id, t.title])], numeric: true, full: true },
  ];
  const taskValues = (t) => ({ ...t, assignee_user_id: t.assignee_user?.id, assignee_agent_id: t.assignee_agent?.id, depends_on_task_id: t.depends_on?.id });
  const openTask = (task) => formDialog({
    title: task ? "Edit task" : "Add task",
    defs: taskDefs(task),
    values: task ? taskValues(task) : { status: "TODO" },
    submit: async (data, { close }) => {
      await api(task ? `${base}/tasks/${task.id}` : `${base}/tasks`, { method: task ? "PATCH" : "POST", body: data });
      close();
      reload();
    },
  });

  const taskRows = p.tasks.map((t) => {
    const sel = el("select", { class: "input inline-select", "aria-label": `Status of ${t.title}`, disabled: !writer }, TASK_STATUSES.map((s) => el("option", { value: s, selected: s === t.status }, fmt.label(s))));
    sel.addEventListener("change", () => act(() => api(`${base}/tasks/${t.id}`, { method: "PATCH", body: { status: sel.value } })));
    return el("tr", {},
      el("td", { class: "wrap" }, el("strong", {}, t.title), t.description ? el("div", { class: "small muted pre" }, t.description) : null,
        t.depends_on ? el("div", { class: `small ${t.blocked ? "score-mid" : "faint"}` }, `${t.blocked ? "Blocked by" : "After"}: ${t.depends_on.title}`) : null),
      el("td", {}, sel),
      el("td", {}, t.assignee_user?.name ?? t.assignee_agent?.name ?? "—"),
      el("td", {}, t.due_date ?? "—"),
      el("td", {}, writer ? el("div", { class: "row" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => openTask(t) }, "Edit"),
        t.assignee_agent ? el("button", { class: "btn btn-sm", type: "button", onclick: async () => {
          try {
            const { task_id } = await api(`${base}/tasks/${t.id}/run-agent`, { method: "POST" });
            toast(`${t.assignee_agent.name} is working on it`, "success");
            window.location.hash = `#/tasks/${task_id}`;
          } catch (err) {
            toast(err.message, "error");
          }
        } }, "Send to agent") : null,
        el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: async () => {
          if (await confirmDialog({ title: "Delete task?", message: `"${t.title}" will be removed.`, confirmLabel: "Delete" })) act(() => api(`${base}/tasks/${t.id}`, { method: "DELETE" }), "Task deleted");
        } }, "Delete")) : null));
  });

  const deliverableRows = p.deliverables.map((d) => {
    const sel = el("select", { class: "input inline-select", "aria-label": `Status of ${d.title}`, disabled: !writer }, DELIVERABLE_STATUSES.map((s) => el("option", { value: s, selected: s === d.status }, fmt.label(s))));
    sel.addEventListener("change", () => act(() => api(`${base}/deliverables/${d.id}`, { method: "PATCH", body: { status: sel.value } })));
    return el("tr", {},
      el("td", { class: "wrap" }, d.url ? el("a", { href: d.url, target: "_blank", rel: "noopener noreferrer" }, d.title) : d.title, d.description ? el("div", { class: "small muted" }, d.description) : null),
      el("td", {}, sel), el("td", {}, d.due_date ?? "—"), el("td", {}, d.delivered_at ? fmt.datetime(d.delivered_at) : "—"));
  });

  const comment = el("textarea", { class: "input", id: "proj-comment", placeholder: "Add a comment…", maxlength: "5000" });
  const commentForm = el("form", { class: "mb-16" }, comment, el("div", { class: "row mt-8" }, el("button", { class: "btn btn-primary btn-sm ml-auto", type: "submit" }, "Comment")));
  commentForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (comment.value.trim()) act(() => api(`${base}/comments`, { method: "POST", body: { body: comment.value.trim() } }));
  });

  const head = [];
  if (writer) {
    head.push(el("button", { class: "btn", type: "button", onclick: () => openProjectForm(p, {}, reload) }, "Edit"));
    head.push(el("button", { class: "btn", type: "button", onclick: () => formDialog({
      title: "Change scope",
      defs: [
        { name: "scope", label: "New scope", type: "textarea" },
        { name: "scope_change_reason", label: "Reason for the change", type: "textarea", hint: "Required. Recorded on the project timeline, e.g. the client's change request." },
      ],
      values: { scope: p.scope },
      submit: async (data, { close }) => {
        await api(base, { method: "PATCH", body: data });
        close();
        toast("Scope updated and logged", "success");
        reload();
      },
    }) }, "Change scope"));
  }
  if (canAdmin(user)) {
    head.push(el("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
      if (!(await confirmDialog({ title: `Delete ${p.name}?`, message: "The project is hidden from lists. Its history stays in the audit log.", confirmLabel: "Delete" }))) return;
      try {
        await api(base, { method: "DELETE" });
        toast("Project deleted", "success");
        window.location.hash = "#/projects";
      } catch (err) {
        toast(err.message, "error");
      }
    } }, "Delete"));
  }

  const tableOf = (headers, rows, empty) => (rows.length
    ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))), el("tbody", {}, rows)))
    : el("div", { class: "card-body empty-inline" }, empty));

  view.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("a", { href: "#/projects", class: "small" }, "← All projects"), el("h1", {}, p.name), el("p", { class: "muted" }, "Client: ", el("a", { href: `#/clients/${p.client_id}` }, p.client_name ?? "—"))),
      el("div", { class: "row" }, statusSelect, head)),
    el("div", { class: "grid two-col" },
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Overview")), el("div", { class: "card-body" }, kvList([
        ["Progress", el("div", {}, progressBar(p.completion_percent), el("div", { class: "small faint" }, p.completion_from_tasks ? `${p.tasks_done} of ${p.tasks_total} tasks done` : "Add tasks to track progress automatically"))],
        ["Budget", p.budget ? fmt.money(p.budget) : null],
        ["Dates", [p.start_date, p.due_date].some(Boolean) ? `${p.start_date ?? "?"} → ${p.due_date ?? "?"}` : null],
        ["Agents", p.agents.map((a) => a.name).join(", ") || null],
      ]),
      p.description ? el("p", { class: "pre section-gap" }, p.description) : null,
      el("div", { class: "section-gap" }, el("h3", {}, "Scope"), el("p", { class: "pre" }, p.scope || "No scope written yet.")))),
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Timeline")), el("div", { class: "card-body" }, writer ? commentForm : null,
        p.timeline.length ? el("ul", { class: "timeline" }, p.timeline.map((e) => el("li", { class: e.kind === "comment" ? "tl-comment" : "" }, el("div", { class: "pre" }, e.text), el("div", { class: "tl-meta" }, `${e.actor ?? "System"} · ${fmt.datetime(e.at)}`)))) : el("p", { class: "empty-inline" }, "Nothing yet.")))),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, `Tasks (${p.tasks_done}/${p.tasks_total} done)`), writer ? el("button", { class: "btn btn-sm", type: "button", onclick: () => openTask(null) }, "Add task") : null),
      tableOf(["Task", "Status", "Assignee", "Due", ""], taskRows, "No tasks yet. Add tasks to plan the work and track progress.")),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Deliverables"), writer ? el("button", { class: "btn btn-sm", type: "button", onclick: () => formDialog({
      title: "Add deliverable",
      defs: [{ name: "title", label: "Title", required: true }, { name: "due_date", label: "Due date", type: "date" }, { name: "url", label: "Link", type: "url", full: true }, { name: "description", label: "Description", type: "textarea" }],
      submit: async (data, { close }) => {
        await api(`${base}/deliverables`, { method: "POST", body: data });
        close();
        reload();
      },
    }) }, "Add deliverable") : null),
      tableOf(["Deliverable", "Status", "Due", "Delivered"], deliverableRows, "No deliverables yet.")),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Agent work on this project")),
      tableOf(["Task", "Agent", "Status", "Created"], p.agent_tasks.map((t) => el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/tasks/${t.id}`) }, el("td", { class: "wrap" }, t.title), el("td", {}, t.agent?.name ?? "—"), el("td", {}, statusBadge(t.phase)), el("td", {}, fmt.datetime(t.created_at)))), "Assign an agent to a task and use “Send to agent”.")),
  );
}

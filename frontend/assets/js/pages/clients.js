// Clients: list with contract status, and a 360° detail view.
import { api } from "../api.js";
import { canAdmin, canWrite, confirmDialog, el, errorState, fmt, formDialog, kvList, loadingState, pager, progressBar, statusBadge, toast } from "../ui.js";
import { openProjectForm } from "./projects.js";

const CONTRACT = ["PROSPECTIVE", "ACTIVE", "PAUSED", "ENDED"];
const PAGE_SIZE = 25;

const DEFS = [
  { name: "company_name", label: "Company name", required: true },
  { name: "contract_status", label: "Contract status", type: "select", options: CONTRACT, default: "PROSPECTIVE" },
  { name: "contact_name", label: "Contact name" },
  { name: "email", label: "Email", type: "email" },
  { name: "phone", label: "Phone", type: "tel" },
  { name: "website", label: "Website", type: "url" },
  { name: "industry", label: "Industry" },
  { name: "service_package", label: "Service package" },
  { name: "address", label: "Address", full: true },
  { name: "start_date", label: "Start date", type: "date" },
  { name: "end_date", label: "End date", type: "date" },
  { name: "monthly_fee", label: "Monthly fee (USD)", type: "number", min: "0", step: "0.01", money: true },
  { name: "setup_fee", label: "One-time setup fee (USD)", type: "number", min: "0", step: "0.01", money: true },
  { name: "notes", label: "Notes", type: "textarea" },
];

function openClientForm(existing, onSaved) {
  formDialog({
    title: existing ? `Edit ${existing.company_name}` : "Add client",
    defs: DEFS,
    values: existing ?? { contract_status: "PROSPECTIVE" },
    submit: async (values, { close }) => {
      const body = { ...values, monthly_fee: values.monthly_fee ?? "0", setup_fee: values.setup_fee ?? "0" };
      const saved = existing ? await api(`/clients/${existing.id}`, { method: "PATCH", body }) : await api("/clients", { method: "POST", body });
      close();
      toast(existing ? "Client saved" : "Client added", "success");
      onSaved(saved);
    },
  });
}

export async function renderClients(ctx) {
  if (ctx.params[0]) return renderClient(ctx, Number(ctx.params[0]));
  const { view, user } = ctx;
  const state = { q: "", status: "", page: 1 };
  const search = el("input", { class: "input search", type: "search", placeholder: "Search clients…", "aria-label": "Search clients" });
  const status = el("select", { class: "input", "aria-label": "Contract status" }, el("option", { value: "" }, "All statuses"), CONTRACT.map((s) => el("option", { value: s }, fmt.label(s))));
  const tbody = el("tbody");
  const pagerHost = el("div");

  async function load() {
    const q = new URLSearchParams({ page: String(state.page), page_size: String(PAGE_SIZE) });
    if (state.q) q.set("q", state.q);
    if (state.status) q.set("contract_status", state.status);
    try {
      const data = await api(`/clients?${q}`);
      tbody.replaceChildren(...(data.items.length
        ? data.items.map((c) => el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/clients/${c.id}`) },
            el("td", { class: "wrap" }, el("strong", {}, c.company_name), c.contact_name ? el("div", { class: "small faint" }, c.contact_name) : null),
            el("td", {}, statusBadge(c.contract_status)),
            el("td", {}, c.service_package ?? "—"),
            el("td", {}, fmt.money(c.monthly_fee)),
            el("td", {}, String(c.active_projects)),
            el("td", {}, c.start_date ?? "—")))
        : [el("tr", {}, el("td", { colspan: "6", class: "empty-inline" }, state.q || state.status ? "No clients match." : "No clients yet. Add one, or convert a won prospect."))]));
      pagerHost.replaceChildren(pager(state, data.total, PAGE_SIZE, load));
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "6" }, err.message)));
    }
  }
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); state.page = 1; load(); }, 300); });
  status.addEventListener("change", () => { state.status = status.value; state.page = 1; load(); });

  view.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, "Clients"), el("p", { class: "muted" }, "Active MRR on the dashboard is the sum of monthly fees of active clients.")),
      canWrite(user) ? el("button", { class: "btn btn-primary", type: "button", onclick: () => openClientForm(null, (c) => (window.location.hash = `#/clients/${c.id}`)) }, "Add client") : null),
    el("section", { class: "card" },
      el("div", { class: "card-body" }, el("div", { class: "toolbar" }, search, status)),
      el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Client", "Contract", "Package", "Monthly fee", "Active projects", "Start"].map((h) => el("th", {}, h)))), tbody)),
      pagerHost),
  );
  load();
}

function table(headers, rows, empty) {
  return rows.length
    ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))), el("tbody", {}, rows)))
    : el("div", { class: "card-body empty-inline" }, empty);
}

async function renderClient(ctx, id) {
  const { view, user } = ctx;
  view.replaceChildren(loadingState());
  let c;
  try {
    c = await api(`/clients/${id}`);
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderClient(ctx, id)));
    return;
  }
  const reload = () => renderClient(ctx, id);
  const actions = [];
  if (canWrite(user)) {
    actions.push(el("button", { class: "btn", type: "button", onclick: () => openClientForm(c, reload) }, "Edit"));
    actions.push(el("button", { class: "btn btn-primary", type: "button", onclick: () => openProjectForm(null, { client_id: c.id }, (p) => (window.location.hash = `#/projects/${p.id}`)) }, "New project"));
  }
  if (canAdmin(user)) {
    actions.push(el("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
      if (!(await confirmDialog({ title: `Delete ${c.company_name}?`, message: "The client is hidden from lists. Clients with active projects cannot be deleted.", confirmLabel: "Delete" }))) return;
      try {
        await api(`/clients/${id}`, { method: "DELETE" });
        toast("Client deleted", "success");
        window.location.hash = "#/clients";
      } catch (err) {
        toast(err.message, "error");
      }
    } }, "Delete"));
  }
  const link = (url) => (url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, url) : null);

  view.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("a", { href: "#/clients", class: "small" }, "← All clients"), el("h1", {}, c.company_name), el("p", { class: "muted" }, [c.industry, c.service_package].filter(Boolean).join(" · ") || "—")),
      el("div", { class: "row" }, statusBadge(c.contract_status), actions)),
    el("div", { class: "grid two-col" },
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Details")), el("div", { class: "card-body" }, kvList([
        ["Contact", c.contact_name], ["Email", c.email], ["Phone", c.phone], ["Website", link(c.website)], ["Address", c.address],
        ["Contract", `${fmt.label(c.contract_status)}${c.start_date ? ` · from ${c.start_date}` : ""}${c.end_date ? ` to ${c.end_date}` : ""}`],
        ["Monthly fee", fmt.money(c.monthly_fee)], ["Setup fee", fmt.money(c.setup_fee)],
        ["Converted from", c.prospect_id ? el("a", { href: `#/prospects/${c.prospect_id}` }, "prospect record") : null],
      ]), c.notes ? el("p", { class: "pre section-gap" }, c.notes) : null)),
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Activity")), el("div", { class: "card-body" },
        c.activity.length ? el("ul", { class: "timeline" }, c.activity.map((a) => el("li", {}, el("div", {}, a.summary), el("div", { class: "tl-meta" }, `${a.actor ?? "System"} · ${fmt.datetime(a.created_at)}`)))) : el("p", { class: "empty-inline" }, "No activity yet.")))),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, `Projects (${c.projects.length})`)),
      table(["Project", "Status", "Progress", "Due"], c.projects.map((p) => el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/projects/${p.id}`) }, el("td", {}, p.name), el("td", {}, statusBadge(p.status)), el("td", {}, progressBar(p.completion_percent)), el("td", {}, p.due_date ?? "—"))), "No projects yet.")),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, `Deliverables (${c.deliverables.length})`)),
      table(["Deliverable", "Project", "Status", "Due"], c.deliverables.map((d) => el("tr", {}, el("td", {}, d.url ? el("a", { href: d.url, target: "_blank", rel: "noopener noreferrer" }, d.title) : d.title), el("td", {}, el("a", { href: `#/projects/${d.project_id}` }, d.project)), el("td", {}, statusBadge(d.status)), el("td", {}, d.due_date ?? "—"))), "No deliverables yet.")),
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Invoices and payments")),
      c.invoices.length || c.payments.length
        ? el("div", {}, table(["Invoice", "Status", "Total", "Issued", "Due"], c.invoices.map((i) => el("tr", {}, el("td", {}, i.number), el("td", {}, statusBadge(i.status)), el("td", {}, fmt.money(i.total)), el("td", {}, i.issue_date ?? "—"), el("td", {}, i.due_date ?? "—"))), "No invoices."),
            table(["Payment", "Amount", "Method"], c.payments.map((p) => el("tr", {}, el("td", {}, p.paid_on), el("td", {}, fmt.money(p.amount)), el("td", {}, p.method ?? "—"))), "No payments."))
        : el("div", { class: "card-body empty-inline" }, "No invoices or payments recorded. Invoicing arrives with Finance in Phase 5.")),
  );
}

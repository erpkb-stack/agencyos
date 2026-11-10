// Finance: overview, invoices, payments, revenue, expenses and AI usage. Paid status comes only from payments.
import { api } from "../api.js";
import { canAdmin, canWrite, confirmDialog, el, errorState, fmt, formDialog, kvList, loadingState, modal, statusBadge, toast } from "../ui.js";

const TABS = [["overview", "Overview"], ["invoices", "Invoices"], ["payments", "Payments"], ["revenue", "Revenue"], ["expenses", "Expenses"], ["ai", "AI usage"]];
const REV_CATS = ["RETAINER", "SETUP_FEE", "ADDITIONAL_SERVICE", "OTHER"];
const EXP_CATS = ["AI_API", "HOSTING", "DOMAINS", "MARKETING_TOOLS", "SOFTWARE", "OTHER"];

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const table = (headers, rows, empty) => (rows.length
  ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))), el("tbody", {}, rows)))
  : el("div", { class: "card-body empty-inline" }, empty));
const card = (title, body, action) => el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, title), action ?? null), body);
const metric = (label, value, note) => el("div", { class: "metric" }, el("div", { class: "kpi-label" }, label), el("div", { class: "kpi-value" }, value), note ? el("div", { class: "small faint" }, note) : null);
const clientOptions = async (none = "No client") => [["", none], ...(await api("/projects/options")).clients.map((c) => [c.id, c.name])];

export async function renderFinance(ctx) {
  const { view, user } = ctx;
  const tab = TABS.some(([k]) => k === ctx.params[0]) ? ctx.params[0] : "overview";
  const content = el("div", {}, loadingState());
  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Finance"), el("p", { class: "muted" }, "Cash-basis records. Invoices are marked paid only by recorded payments; nothing is emailed or charged from here."))),
    el("div", { class: "tabs", role: "tablist" }, TABS.map(([k, l]) => el("a", { class: `tab${k === tab ? " active" : ""}`, href: `#/finance/${k}`, role: "tab", "aria-selected": String(k === tab) }, l))),
    content,
  );
  const tabs = { overview, invoices, payments, revenue, expenses, ai: aiUsage };
  try {
    return await tabs[tab](content, user, ctx);
  } catch (err) {
    content.replaceChildren(errorState(err, () => renderFinance(ctx)));
  }
}

function periodPicker(onChange) {
  const today = fmt.today();
  const start = el("input", { class: "input", type: "date", value: `${today.slice(0, 8)}01`, "aria-label": "Start date" });
  const end = el("input", { class: "input", type: "date", value: today, "aria-label": "End date" });
  const presets = [["This month", () => [`${fmt.today().slice(0, 8)}01`, fmt.today()]], ["Last 90 days", () => { const d = new Date(); d.setDate(d.getDate() - 89); return [localDate(d), fmt.today()]; }], ["This year", () => [`${fmt.today().slice(0, 4)}-01-01`, fmt.today()]]];
  const bar = el("div", { class: "toolbar" }, start, end, presets.map(([l, f]) => el("button", { class: "btn btn-sm", type: "button", onclick: () => { [start.value, end.value] = f(); onChange(); } }, l)));
  [start, end].forEach((i) => i.addEventListener("change", onChange));
  return { bar, value: () => ({ start: start.value, end: end.value }) };
}

function localDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function overview(host) {
  let chart = null;
  const body = el("div", {});
  const period = periodPicker(load);
  host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-body" }, period.bar)), body);
  async function load() {
    const { start, end } = period.value();
    try {
      const s = await api(`/finance/summary?start=${start}&end=${end}`);
      chart?.destroy();
      const canvas = el("canvas", { "aria-label": "Revenue, expenses and profit by month", role: "img" });
      body.replaceChildren(
        el("div", { class: "metric-grid section-gap" },
          metric("Revenue", fmt.money(s.total_revenue)), metric("Expenses", fmt.money(s.total_expenses)),
          metric("Gross profit", fmt.money(s.gross_profit), s.margin_percent != null ? `${s.margin_percent}% margin` : "no revenue"),
          metric("MRR", fmt.money(s.monthly_recurring_revenue), "active clients' monthly fees"),
          metric("Outstanding", fmt.money(s.outstanding), `${s.open_invoices} open invoice(s)`),
          metric("Overdue", fmt.money(s.overdue), `${s.overdue_invoices} invoice(s)`),
          metric("All-time revenue", fmt.money(s.all_time_revenue))),
        card("Last 12 months", el("div", { class: "card-body" }, el("div", { class: "chart-box" }, canvas))),
        el("div", { class: "grid two-col section-gap" },
          el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Revenue by category")), el("div", { class: "card-body" }, kvList(Object.entries(s.revenue_by_category).map(([k, v]) => [fmt.label(k), fmt.money(v)])))),
          el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Expenses by category")), el("div", { class: "card-body" }, kvList(Object.entries(s.expenses_by_category).map(([k, v]) => [fmt.label(k), fmt.money(v)]))))),
        card("Client profitability (estimate)", el("div", {},
          el("p", { class: "small faint card-pad" }, s.profitability.method),
          table(["Client", "Revenue", "Direct AI cost", "Allocated expenses", "Est. profit", "Margin"], s.profitability.rows.map((r) =>
            el("tr", {}, el("td", {}, el("a", { href: `#/clients/${r.client_id}` }, r.client_name)), el("td", {}, fmt.money(r.revenue)), el("td", {}, fmt.money(r.direct_ai_cost)), el("td", {}, fmt.money(r.allocated_expenses)), el("td", {}, fmt.money(r.estimated_profit)), el("td", {}, r.margin_percent != null ? `${r.margin_percent}%` : "—"))),
          "No client revenue in this period."))),
      );
      if (window.Chart) {
        chart = new window.Chart(canvas, {
          type: "bar",
          data: { labels: s.monthly.labels, datasets: [
            { label: "Revenue", data: s.monthly.revenue.map(Number), backgroundColor: cssVar("--primary") },
            { label: "Expenses", data: s.monthly.expenses.map(Number), backgroundColor: cssVar("--danger") },
            { type: "line", label: "Profit", data: s.monthly.profit.map(Number), borderColor: cssVar("--success"), backgroundColor: cssVar("--success"), tension: 0.25 },
          ] },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: cssVar("--text-muted"), boxWidth: 12 } } },
            scales: { x: { grid: { display: false }, ticks: { color: cssVar("--text-muted") } }, y: { beginAtZero: true, grid: { color: cssVar("--border") }, ticks: { color: cssVar("--text-muted") } } } },
        });
      }
    } catch (err) {
      body.replaceChildren(errorState(err, load));
    }
  }
  await load();
  return () => chart?.destroy();
}

// -- invoices ----------------------------------------------------------------------------------

function itemsEditor(items) {
  const host = el("div", {});
  const rows = [];
  const total = el("div", { class: "small muted mt-8" });
  const recalc = () => {
    const sum = rows.reduce((acc, r) => acc + Math.round(Number(r.q.value || 0) * Number(r.p.value || 0) * 100), 0);
    total.textContent = `Total ${fmt.money(sum / 100)} (calculated exactly on the server)`;
  };
  const add = (it = { description: "", quantity: "1", unit_price: "" }) => {
    const d = el("input", { class: "input", value: it.description, placeholder: "Description", "aria-label": "Item description" });
    const q = el("input", { class: "input", type: "number", min: "0.01", step: "0.01", value: it.quantity, "aria-label": "Quantity" });
    const p = el("input", { class: "input", type: "number", min: "0", step: "0.01", value: it.unit_price, placeholder: "Unit price", "aria-label": "Unit price" });
    const entry = { d, q, p };
    const row = el("div", { class: "field-row" }, d, q, p, el("span"), el("span"), el("button", { class: "btn btn-sm btn-ghost", type: "button", "aria-label": "Remove item", onclick: () => { row.remove(); rows.splice(rows.indexOf(entry), 1); recalc(); } }, "×"));
    [q, p].forEach((i) => i.addEventListener("input", recalc));
    rows.push(entry);
    host.append(row);
    recalc();
  };
  (items.length ? items : [undefined]).forEach((it) => add(it));
  return { el: el("div", {}, el("h3", {}, "Line items"), host, el("button", { class: "btn btn-sm mt-8", type: "button", onclick: () => add() }, "Add item"), total),
    read: () => rows.map((r) => ({ description: r.d.value.trim(), quantity: r.q.value || "1", unit_price: r.p.value || "0" })) };
}

async function openInvoiceForm(existing, reload) {
  const items = itemsEditor(existing?.items ?? []);
  const defs = [
    existing ? null : { name: "client_id", label: "Client", type: "select", options: (await clientOptions("Choose…")), numeric: true, full: true },
    { name: "issue_date", label: "Issue date", type: "date" }, { name: "due_date", label: "Due date", type: "date" },
    { name: "notes", label: "Notes (shown on the invoice)", type: "textarea" },
  ].filter(Boolean);
  formDialog({
    title: existing ? `Edit ${existing.number}` : "New invoice draft", defs, values: existing ?? { issue_date: fmt.today() }, intro: items.el,
    submit: async (data, { close }) => {
      const body = { ...data, items: items.read() };
      const inv = existing ? await api(`/finance/invoices/${existing.id}`, { method: "PATCH", body }) : await api("/finance/invoices", { method: "POST", body });
      close();
      toast("Invoice saved as draft", "success");
      reload(inv);
    },
  });
}

function openPayment(inv, reload) {
  formDialog({
    title: inv ? `Record payment for ${inv.number}` : "Record a payment",
    defs: [
      { name: "amount", label: "Amount (USD)", type: "number", min: "0.01", step: "0.01", money: true, required: true },
      { name: "paid_on", label: "Received on", type: "date", required: true },
      { name: "method", label: "Method", hint: "e.g. ACH, card, check" }, { name: "reference", label: "Reference" },
      { name: "category", label: "Revenue category", type: "select", options: REV_CATS, default: "RETAINER" },
    ],
    values: { amount: inv?.balance, paid_on: fmt.today(), category: "RETAINER" },
    intro: el("p", { class: "small muted mb-16" }, "Record money you have actually received. This creates the matching revenue entry and updates the invoice status."),
    submit: async (data, { close }) => {
      await api("/finance/payments", { method: "POST", body: { ...data, invoice_id: inv?.id } });
      close();
      toast("Payment recorded", "success");
      reload();
    },
  });
}

async function invoiceDetail(id, user, back) {
  const inv = await api(`/finance/invoices/${id}`);
  const reload = () => { handle.close(); invoiceDetail(id, user, back); back(); };
  const actions = [el("a", { class: "btn", href: `/api/v1/finance/invoices/${id}/print`, target: "_blank", rel: "noopener" }, "Print / PDF")];
  if (canWrite(user) && inv.status === "DRAFT") {
    actions.push(el("button", { class: "btn", type: "button", onclick: () => { handle.close(); openInvoiceForm(inv, () => { invoiceDetail(id, user, back); back(); }); } }, "Edit"));
    actions.push(el("button", { class: "btn btn-primary", type: "button", onclick: async () => {
      if (!(await confirmDialog({ title: "Mark as sent?", message: "Send the invoice yourself (e.g. print to PDF and email it). This only records that it was sent and locks editing.", confirmLabel: "Mark as sent" }))) return;
      try { await api(`/finance/invoices/${id}/mark-sent`, { method: "POST" }); reload(); } catch (e) { toast(e.message, "error"); }
    } }, "Mark as sent"));
    actions.push(el("button", { class: "btn btn-ghost", type: "button", onclick: async () => { if (await confirmDialog({ title: "Delete draft?", message: inv.number, confirmLabel: "Delete" })) { await api(`/finance/invoices/${id}`, { method: "DELETE" }); handle.close(); back(); } } }, "Delete draft"));
  }
  if (canWrite(user) && ["SENT", "PARTIALLY_PAID"].includes(inv.status)) actions.push(el("button", { class: "btn btn-primary", type: "button", onclick: () => { handle.close(); openPayment(inv, () => { invoiceDetail(id, user, back); back(); }); } }, "Record payment"));
  if (canAdmin(user) && ["SENT"].includes(inv.status) && !inv.payments.length) actions.push(el("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
    if (await confirmDialog({ title: "Void invoice?", message: `${inv.number} will no longer count as outstanding.`, confirmLabel: "Void" })) { try { await api(`/finance/invoices/${id}/void`, { method: "POST" }); reload(); } catch (e) { toast(e.message, "error"); } }
  } }, "Void"));
  const handle = modal({
    title: `Invoice ${inv.number}`, wide: true,
    body: el("div", {},
      el("div", { class: "row mb-16" }, statusBadge(inv.status), inv.overdue ? el("span", { class: "badge badge-danger" }, "Overdue") : null),
      kvList([["Client", inv.client_name], ["Issued", inv.issue_date], ["Due", inv.due_date], ["Total", fmt.money(inv.total)], ["Paid", fmt.money(inv.paid)], ["Balance", fmt.money(inv.balance)]]),
      table(["Description", "Qty", "Unit price", "Amount"], inv.items.map((i) => el("tr", {}, el("td", { class: "wrap" }, i.description), el("td", {}, i.quantity), el("td", {}, fmt.money(i.unit_price)), el("td", {}, fmt.money(i.amount)))), "No items."),
      el("h3", { class: "section-gap" }, "Payments"),
      inv.payments.length ? el("ul", { class: "list-plain" }, inv.payments.map((p) => el("li", {}, `${p.paid_on} · ${fmt.money(p.amount)}${p.method ? ` · ${p.method}` : ""}${p.reference ? ` · ${p.reference}` : ""}`))) : el("p", { class: "empty-inline" }, "No payments recorded; the invoice is not paid."),
      inv.notes ? el("p", { class: "pre section-gap" }, inv.notes) : null),
    actions,
  });
}

async function invoices(host, user, ctx) {
  const status = el("select", { class: "input", "aria-label": "Status" }, el("option", { value: "" }, "All statuses"), ["DRAFT", "SENT", "PARTIALLY_PAID", "PAID", "VOID"].map((s) => el("option", { value: s }, fmt.label(s))));
  const overdue = el("input", { type: "checkbox" });
  const listHost = el("div", {});
  async function load() {
    const q = new URLSearchParams();
    if (status.value) q.set("status", status.value);
    if (overdue.checked) q.set("overdue", "true");
    const { items } = await api(`/finance/invoices?${q}`);
    listHost.replaceChildren(table(["Invoice", "Client", "Status", "Total", "Paid", "Balance", "Due"], items.map((i) => el("tr", { class: "clickable", onclick: () => invoiceDetail(i.id, user, load) },
      el("td", {}, i.number), el("td", {}, i.client_name ?? "—"), el("td", {}, el("div", { class: "row" }, statusBadge(i.status), i.overdue ? el("span", { class: "badge badge-danger" }, "Overdue") : null)),
      el("td", {}, fmt.money(i.total)), el("td", {}, fmt.money(i.paid)), el("td", {}, fmt.money(i.balance)), el("td", {}, i.due_date ?? "—"))), "No invoices match."));
  }
  [status, overdue].forEach((n) => n.addEventListener("change", load));
  const fromClient = async () => formDialog({
    title: "Invoice a client's agreed fees",
    defs: [{ name: "client_id", label: "Client", type: "select", options: await clientOptions("Choose…"), numeric: true, full: true }, { name: "period_label", label: "Billing period label", hint: "e.g. March 2026" }, { name: "due_in_days", label: "Due in (days)", type: "number", min: "0", max: "120" }, { name: "include_setup_fee", label: "Also bill", type: "checkboxes", options: [["setup", "One-time setup fee"]] }],
    values: { due_in_days: 14 },
    submit: async (d, { close }) => {
      const inv = await api(`/finance/invoices/from-client/${d.client_id}`, { method: "POST", body: { period_label: d.period_label, due_in_days: d.due_in_days ?? 14, include_setup_fee: (d.include_setup_fee ?? []).length > 0 } });
      close(); toast(`${inv.number} drafted`, "success"); await load(); invoiceDetail(inv.id, user, load);
    },
  });
  host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Invoices"), canWrite(user) ? el("div", { class: "row" },
    el("button", { class: "btn btn-sm", type: "button", onclick: fromClient }, "From client fees"),
    el("button", { class: "btn btn-sm btn-primary", type: "button", onclick: () => openInvoiceForm(null, (inv) => { load(); invoiceDetail(inv.id, user, load); }) }, "New invoice")) : null),
    el("div", { class: "card-body" }, el("div", { class: "toolbar" }, status, el("label", { class: "check" }, overdue, "Overdue only"))), listHost));
  await load();
}

// -- payments, revenue, expenses ---------------------------------------------------------------

async function payments(host, user) {
  async function load() {
    const { items } = await api("/finance/payments");
    host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Payments received"),
      canWrite(user) ? el("button", { class: "btn btn-sm btn-primary", type: "button", onclick: async () => {
        const opts = await clientOptions("Choose…");
        formDialog({ title: "Payment without an invoice", defs: [{ name: "client_id", label: "Client", type: "select", options: opts, numeric: true, full: true }, { name: "amount", label: "Amount (USD)", type: "number", min: "0.01", step: "0.01", money: true }, { name: "paid_on", label: "Received on", type: "date" }, { name: "method", label: "Method" }, { name: "reference", label: "Reference" }, { name: "category", label: "Revenue category", type: "select", options: REV_CATS }],
          values: { paid_on: fmt.today(), category: "ADDITIONAL_SERVICE" },
          submit: async (d, { close }) => { await api("/finance/payments", { method: "POST", body: d }); close(); toast("Payment recorded", "success"); load(); } });
      } }, "Record payment") : null),
      table(["Date", "Client", "Invoice", "Amount", "Method", "Reference", ""], items.map((p) => el("tr", {}, el("td", {}, p.paid_on), el("td", {}, p.client_name ?? "—"), el("td", {}, p.invoice_number ?? "—"), el("td", {}, fmt.money(p.amount)), el("td", {}, p.method ?? "—"), el("td", {}, p.reference ?? "—"),
        el("td", {}, canAdmin(user) ? el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: async () => { if (await confirmDialog({ title: "Delete payment?", message: "Its revenue entry is removed and the invoice status is recalculated.", confirmLabel: "Delete" })) { await api(`/finance/payments/${p.id}`, { method: "DELETE" }); load(); } } }, "Delete") : null))), "No payments recorded yet.")));
  }
  await load();
}

async function revenue(host, user) {
  async function load() {
    const { items } = await api("/finance/revenue");
    const opts = await clientOptions();
    const form = (existing) => formDialog({ title: existing ? "Edit revenue" : "Add revenue", defs: [{ name: "category", label: "Category", type: "select", options: REV_CATS }, { name: "client_id", label: "Client", type: "select", options: opts, numeric: true }, { name: "amount", label: "Amount (USD)", type: "number", min: "0.01", step: "0.01", money: true }, { name: "received_on", label: "Received on", type: "date" }, { name: "description", label: "Description", full: true }],
      values: existing ?? { received_on: fmt.today(), category: "OTHER" },
      intro: el("p", { class: "small muted mb-16" }, "For money received without an invoice. Prefer recording payments, which create revenue automatically."),
      submit: async (d, { close }) => { await api(existing ? `/finance/revenue/${existing.id}` : "/finance/revenue", { method: existing ? "PATCH" : "POST", body: d }); close(); load(); } });
    host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Revenue (cash received)"), canAdmin(user) ? el("button", { class: "btn btn-sm btn-primary", type: "button", onclick: () => form(null) }, "Add revenue") : null),
      table(["Date", "Client", "Category", "Description", "Amount", ""], items.map((r) => el("tr", {}, el("td", {}, r.received_on), el("td", {}, r.client_name ?? "—"), el("td", {}, fmt.label(r.category)), el("td", { class: "wrap" }, r.description ?? "—"), el("td", {}, fmt.money(r.amount)),
        el("td", {}, r.from_payment ? el("span", { class: "small faint" }, "from payment") : canAdmin(user) ? el("div", { class: "row" }, el("button", { class: "btn btn-sm", type: "button", onclick: () => form(r) }, "Edit"), el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: async () => { if (await confirmDialog({ title: "Delete revenue entry?", message: fmt.money(r.amount), confirmLabel: "Delete" })) { await api(`/finance/revenue/${r.id}`, { method: "DELETE" }); load(); } } }, "Delete")) : null))), "No revenue recorded yet.")));
  }
  await load();
}

async function expenses(host, user) {
  async function load() {
    const { items } = await api("/finance/expenses");
    const form = (existing) => formDialog({ title: existing ? "Edit expense" : "Add expense", defs: [{ name: "category", label: "Category", type: "select", options: EXP_CATS }, { name: "vendor", label: "Vendor" }, { name: "amount", label: "Amount (USD)", type: "number", min: "0.01", step: "0.01", money: true }, { name: "incurred_on", label: "Date", type: "date" }, { name: "description", label: "Description", full: true }, { name: "is_recurring", label: "Recurring", type: "checkboxes", options: [["yes", "Recurring monthly cost"]] }],
      values: existing ? { ...existing, is_recurring: existing.is_recurring ? ["yes"] : [] } : { incurred_on: fmt.today(), category: "SOFTWARE" },
      submit: async (d, { close }) => { const body = { ...d, is_recurring: (d.is_recurring ?? []).length > 0 }; await api(existing ? `/finance/expenses/${existing.id}` : "/finance/expenses", { method: existing ? "PATCH" : "POST", body }); close(); load(); } });
    host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Expenses"), canWrite(user) ? el("button", { class: "btn btn-sm btn-primary", type: "button", onclick: () => form(null) }, "Add expense") : null),
      table(["Date", "Category", "Vendor", "Description", "Amount", "Recurring", ""], items.map((e) => el("tr", {}, el("td", {}, e.incurred_on), el("td", {}, fmt.label(e.category)), el("td", {}, e.vendor ?? "—"), el("td", { class: "wrap" }, e.description ?? "—"), el("td", {}, fmt.money(e.amount)), el("td", {}, e.is_recurring ? "Yes" : "No"),
        el("td", {}, el("div", { class: "row" }, canWrite(user) ? el("button", { class: "btn btn-sm", type: "button", onclick: () => form(e) }, "Edit") : null, canAdmin(user) ? el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: async () => { if (await confirmDialog({ title: "Delete expense?", message: fmt.money(e.amount), confirmLabel: "Delete" })) { await api(`/finance/expenses/${e.id}`, { method: "DELETE" }); load(); } } }, "Delete") : null)))), "No expenses recorded yet.")));
  }
  await load();
}

async function aiUsage(host, user) {
  const month = el("input", { class: "input", type: "month", value: fmt.today().slice(0, 7), "aria-label": "Month" });
  const body = el("div", {});
  async function load() {
    const u = await api(`/finance/ai-usage?month=${month.value}`);
    body.replaceChildren(
      el("div", { class: "metric-grid section-gap" }, metric("Estimated AI cost", `$${u.estimated_total}`, "from recorded calls, list prices"), metric("AI expenses recorded", fmt.money(u.recorded_ai_expenses)), u.calls_without_price ? metric("Calls without a price", String(u.calls_without_price), "model not in the price list") : null),
      card("By model", table(["Provider", "Model", "Calls", "Input tokens", "Output tokens", "Est. cost"], u.by_model.map((r) => el("tr", {}, el("td", {}, r.provider), el("td", {}, r.model), el("td", {}, fmt.int(r.calls)), el("td", {}, fmt.int(r.prompt_tokens)), el("td", {}, fmt.int(r.completion_tokens)), el("td", {}, `$${r.estimated_cost}`))), "No AI calls this month."),
        canWrite(user) ? el("button", { class: "btn btn-sm", type: "button", onclick: () => formDialog({ title: `Record AI expense for ${u.month}`, defs: [{ name: "amount", label: "Amount billed (USD)", type: "number", min: "0.01", step: "0.01", money: true }, { name: "vendor", label: "Vendor", hint: "e.g. Anthropic, OpenAI" }], values: { amount: Number(u.estimated_total) > 0 ? u.estimated_total : null },
          intro: el("p", { class: "small muted mb-16" }, "Use the amount on your provider's invoice when you have it; the estimate excludes discounts, caching and taxes."),
          submit: async (d, { close }) => { await api("/finance/ai-usage/expense", { method: "POST", body: { ...d, month: u.month } }); close(); toast("AI expense recorded", "success"); load(); } }) }, "Record as expense") : null));
  }
  month.addEventListener("change", load);
  host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-body" }, el("div", { class: "toolbar" }, month))), body);
  await load();
}

// Email Marketing: template library, AI drafts, personalisation preview, suppression list, sender settings.
import { api } from "../api.js";
import { canAdmin, canWrite, confirmDialog, el, errorState, fmt, formDialog, kvList, loadingState, modal, poll, statusBadge, toast } from "../ui.js";
import { aiTaskList, anyActive, importFlow, issueList, openEmailEditor } from "./marketing-shared.js";

const TABS = [["templates", "Templates"], ["suppression", "Suppression list"], ["check", "Check a recipient list"]];

export async function renderEmail(ctx) {
  const { view, user } = ctx;
  const tab = TABS.some(([k]) => k === ctx.params[0]) ? ctx.params[0] : "templates";
  const content = el("div", {}, loadingState());
  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Email Marketing"), el("p", { class: "muted" }, "Drafts only. AgencyOS never sends email; approved campaigns are sent from your own email tool."))),
    el("div", { class: "tabs", role: "tablist" }, TABS.map(([k, l]) => el("a", { class: `tab${k === tab ? " active" : ""}`, href: `#/email/${k}`, role: "tab", "aria-selected": String(k === tab) }, l))),
    content,
  );
  try {
    if (tab === "templates") return await templatesTab(content, user);
    if (tab === "suppression") return await suppressionTab(content, user);
    return checkTab(content);
  } catch (err) {
    content.replaceChildren(errorState(err, () => renderEmail(ctx)));
  }
}

async function senderCard() {
  const s = await api("/emails/settings");
  return el("section", { class: "card mb-16" }, el("div", { class: "card-head" }, el("h2", {}, "Sender identity"), s.configured ? el("span", { class: "badge badge-success" }, "Configured") : el("span", { class: "badge badge-warning" }, "Not configured")),
    el("div", { class: "card-body" }, kvList([["Agency", s.agency_name || null], ["Postal address", s.agency_address || null], ["Sender", [s.sender_name, s.sender_email].filter(Boolean).join(" · ") || null]]),
      s.configured ? null : el("p", { class: "small faint mt-8" }, "Set AGENCY_NAME, AGENCY_ADDRESS, SENDER_NAME and SENDER_EMAIL in .env and restart. Until then previews show [missing: …] instead of invented values.")));
}

async function templatesTab(host, user) {
  let stop = null;
  const writer = canWrite(user);
  const aiHost = el("div", {});
  const aiList = (items) => aiTaskList(items, { importLabel: "Import as templates", onImport: (t) => importFlow(`/emails/import/${t.id}`, (r) => `${r.items.length} template(s) imported`, draw) });
  async function draw() {
    const [{ items }, tasks, sender] = await Promise.all([api("/emails/templates"), api("/emails/ai-tasks"), senderCard()]);
    const rows = items.map((t) => el("tr", {},
      el("td", { class: "wrap" }, el("strong", {}, t.name), el("div", { class: "small faint" }, t.subject)),
      el("td", {}, fmt.label(t.kind)),
      el("td", {}, t.campaign_id ? el("a", { href: `#/campaigns/${t.campaign_id}` }, `Campaign #${t.campaign_id}`) : "Library"),
      el("td", {}, t.compliant ? el("span", { class: "badge badge-success" }, "Compliant") : el("span", { class: "badge badge-danger" }, "Needs fixes")),
      el("td", {}, statusBadge(t.status)),
      el("td", {}, el("div", { class: "row" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => previewDialog(t) }, "Preview"),
        writer && !t.campaign_id ? el("button", { class: "btn btn-sm", type: "button", onclick: () => openEmailEditor({ title: "Edit email", values: t, save: async (d, { close }) => { await api(`/emails/templates/${t.id}`, { method: "PATCH", body: d }); close(); draw(); } }) }, "Edit") : null,
        writer && !t.campaign_id ? el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: async () => { if (await confirmDialog({ title: "Delete email?", message: t.name, confirmLabel: "Delete" })) { await api(`/emails/templates/${t.id}`, { method: "DELETE" }); draw(); } } }, "Delete") : null))));
    const generate = writer ? el("button", { class: "btn btn-sm", type: "button", onclick: () => formDialog({
      title: "Draft emails with AI",
      defs: [
        { name: "purpose", label: "What do you need?", type: "select", options: [["sequence", "3-step follow-up sequence"], ["single", "One email"], ["subject_lines", "10 subject lines"]], full: true },
        { name: "brief", label: "Brief", type: "textarea", hint: "Audience, offer, tone. Don't paste personal data about recipients." },
      ],
      submitLabel: "Start drafting",
      submit: async (d, { close }) => { await api("/emails/generate", { method: "POST", body: d }); close(); toast("Email Marketing Agent is drafting", "success"); watch(); },
    }) }, "Draft with AI") : null;
    host.replaceChildren(
      sender,
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, `Emails (${items.length})`), el("div", { class: "row" }, generate,
        writer ? el("button", { class: "btn btn-sm btn-primary", type: "button", onclick: () => openEmailEditor({ title: "New email template", values: { body_text: "Hi {{first_name}},\n\n\n\n—\n{{sender_name}}, {{agency_name}}\n{{agency_address}}\nUnsubscribe: {{unsubscribe_link}}" }, save: async (d, { close }) => { await api("/emails/templates", { method: "POST", body: d }); close(); draw(); } }) }, "New template") : null)),
        rows.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Email", "Kind", "Used in", "Compliance", "Status", ""].map((h) => el("th", {}, h)))), el("tbody", {}, rows))) : el("div", { class: "card-body empty-inline" }, "No emails yet.")),
      el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "AI drafts")), el("div", { class: "card-body" }, aiHost)),
    );
    aiHost.replaceChildren(aiList(tasks.items));
    return anyActive(tasks.items);
  }
  async function refreshAI() {
    const tasks = await api("/emails/ai-tasks");
    aiHost.replaceChildren(aiList(tasks.items));
    return anyActive(tasks.items);
  }
  function watch() { stop?.(); stop = poll(refreshAI, 1500); }
  if (await draw()) watch();
  return () => stop?.();
}

async function previewDialog(t) {
  const { items } = await api("/prospects?page_size=100&sort=company_name&order=asc");
  const select = el("select", { class: "input", "aria-label": "Preview for prospect" }, el("option", { value: "" }, "No prospect (merge fields stay visible)"), items.map((p) => el("option", { value: String(p.id) }, `${p.company_name}${p.email ? "" : " (no email)"}`)));
  const out = el("div", { class: "mt-16" });
  const render = async () => {
    try {
      const r = await api(`/emails/templates/${t.id}/preview${select.value ? `?prospect_id=${select.value}` : ""}`);
      out.replaceChildren(
        r.recipient_suppressed ? el("p", { class: "notice notice-warning" }, "This prospect's address is suppressed; they must not be emailed.") : null,
        r.missing_fields.length ? el("p", { class: "small score-mid" }, `Missing values: ${r.missing_fields.join(", ")}`) : null,
        el("div", { class: "deliverable" }, el("div", { class: "deliverable-head" }, el("strong", {}, r.subject)), el("div", { class: "deliverable-body pre" }, r.body_text)),
        el("div", { class: "section-gap" }, el("h3", {}, "Compliance"), issueList(t.compliance)),
      );
    } catch (e) {
      out.replaceChildren(el("div", { class: "form-error" }, e.message));
    }
  };
  select.addEventListener("change", render);
  modal({ title: `Preview: ${t.name}`, wide: true, body: el("div", {}, select, out) });
  render();
}

async function suppressionTab(host, user) {
  async function draw() {
    const { items, total } = await api("/emails/suppressions?page_size=200");
    const email = el("input", { class: "input", type: "email", placeholder: "address@example.com", "aria-label": "Email to suppress" });
    const reason = el("select", { class: "input", "aria-label": "Reason" }, ["MANUAL", "UNSUBSCRIBED", "BOUNCED", "COMPLAINT"].map((r) => el("option", { value: r }, fmt.label(r))));
    const add = el("form", { class: "toolbar" }, email, reason, el("button", { class: "btn btn-primary", type: "submit" }, "Suppress"));
    add.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api("/emails/suppressions", { method: "POST", body: { email: email.value, reason: reason.value } }); toast("Address suppressed", "success"); draw(); } catch (err) { toast(err.message, "error"); }
    });
    host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, `Suppressed addresses (${total})`)),
      el("div", { class: "card-body" }, el("p", { class: "small faint" }, "These addresses are excluded from every audience. Unsubscribes (including clicks on unsubscribe links) and complaints can never be removed."), canWrite(user) ? add : null),
      items.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Email", "Reason", "Source", "Added", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, items.map((s) => el("tr", {}, el("td", {}, s.email), el("td", {}, fmt.label(s.reason)), el("td", {}, s.source ?? "—"), el("td", {}, fmt.datetime(s.created_at)),
          el("td", {}, canAdmin(user) && ["MANUAL", "BOUNCED"].includes(s.reason) ? el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: async () => {
            if (await confirmDialog({ title: "Remove from suppression list?", message: `${s.email} could be emailed again.`, confirmLabel: "Remove" })) { try { await api(`/emails/suppressions/${s.id}`, { method: "DELETE" }); draw(); } catch (err) { toast(err.message, "error"); } }
          } }, "Remove") : null)))))) : el("div", { class: "card-body empty-inline" }, "No suppressed addresses.")));
  }
  await draw();
}

function checkTab(host) {
  const area = el("textarea", { class: "input tall", id: "recipients", placeholder: "Paste email addresses, one per line or comma-separated" });
  const out = el("div", { class: "mt-16" });
  const btn = el("button", { class: "btn btn-primary mt-8", type: "button", onclick: async () => {
    const emails = area.value.split(/[\s,;]+/).filter(Boolean);
    if (!emails.length) return;
    try {
      const r = await api("/emails/recipients/check", { method: "POST", body: { emails } });
      out.replaceChildren(el("div", { class: "metric-grid" }, [["Eligible", r.eligible], ["Suppressed", r.suppressed], ["Invalid", r.invalid], ["Duplicates", r.duplicates]].map(([l, v]) => el("div", { class: "metric" }, el("div", { class: "kpi-label" }, l), el("div", { class: "kpi-value" }, String(v.length))))),
        r.suppressed.length ? el("p", { class: "small mt-8" }, `Do not email: ${r.suppressed.join(", ")}`) : null,
        r.invalid.length ? el("p", { class: "small mt-8" }, `Invalid: ${r.invalid.join(", ")}`) : null);
    } catch (e) { toast(e.message, "error"); }
  } }, "Check list");
  host.replaceChildren(el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Check a recipient list")), el("div", { class: "card-body" },
    el("p", { class: "small faint" }, "Before sending from another tool, check a list against the suppression list. Nothing is stored."), area, btn, out)));
}

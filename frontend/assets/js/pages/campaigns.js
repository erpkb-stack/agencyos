// Campaigns: drafts, AI-assisted content, audience, approval gate, manual status and metrics.
import { api } from "../api.js";
import { canWrite, confirmDialog, el, errorState, fmt, formDialog, kvList, loadingState, pager, poll, statusBadge, toast } from "../ui.js";
import { aiTaskList, anyActive, importFlow, issueList, openEmailEditor, showText } from "./marketing-shared.js";

const TYPES = ["COLD_EMAIL", "PROMOTIONAL_EMAIL", "NEWSLETTER", "LEAD_NURTURING", "REENGAGEMENT", "GOOGLE_ADS", "META_ADS"];
const STATUSES = ["DRAFT", "AWAITING_APPROVAL", "APPROVED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"];
const STAGES = ["NEW", "RESEARCHED", "QUALIFIED", "CONTACTED", "RESPONDED", "MEETING_SCHEDULED", "PROPOSAL_SENT", "NEGOTIATION", "WON", "LOST"];
const CONSENT = [["", "Choose…"], ["b2b_cold_outreach", "Business contacts (B2B cold outreach with opt-out)"], ["opted_in", "Contacts who opted in"], ["existing_customers", "Existing customers"]];
const STATUS_LABELS = { ACTIVE: "Mark as running", PAUSED: "Mark paused", COMPLETED: "Mark completed", CANCELLED: "Cancel campaign" };
const PAGE_SIZE = 25;

async function clientOptions() {
  const { clients } = await api("/projects/options");
  return [["", "No client (agency's own)"], ...clients.map((c) => [c.id, c.name])];
}

async function openCampaignForm(existing, onSaved) {
  const clients = await clientOptions();
  formDialog({
    title: existing ? `Edit ${existing.name}` : "New campaign",
    defs: [
      { name: "name", label: "Campaign name", required: true },
      existing ? null : { name: "campaign_type", label: "Type", type: "select", options: TYPES, default: "COLD_EMAIL" },
      { name: "client_id", label: "Client", type: "select", options: clients, numeric: true },
      { name: "budget", label: "Budget (USD)", type: "number", min: "0", step: "0.01", money: true, hint: existing && ["APPROVED", "ACTIVE", "PAUSED"].includes(existing.status) ? "Changing the budget now needs a new approval." : undefined },
      { name: "start_date", label: "Start date", type: "date" },
      { name: "end_date", label: "End date", type: "date" },
      { name: "target_audience", label: "Target audience", type: "textarea" },
      { name: "objectives", label: "Objectives", type: "textarea" },
    ].filter(Boolean),
    values: existing ?? { campaign_type: "COLD_EMAIL" },
    submit: async (data, { close }) => {
      const body = existing && ["APPROVED", "ACTIVE", "PAUSED"].includes(existing.status) ? { budget: data.budget } : data;
      const saved = existing ? await api(`/campaigns/${existing.id}`, { method: "PATCH", body }) : await api("/campaigns", { method: "POST", body });
      close();
      toast(existing ? "Campaign saved" : "Campaign created", "success");
      onSaved(saved);
    },
  });
}

export async function renderCampaigns(ctx) {
  if (ctx.params[0]) return renderCampaign(ctx, Number(ctx.params[0]));
  const { view, user } = ctx;
  const state = { q: "", status: "", type: "", page: 1 };
  const search = el("input", { class: "input search", type: "search", placeholder: "Search campaigns…", "aria-label": "Search campaigns" });
  const status = el("select", { class: "input", "aria-label": "Status" }, el("option", { value: "" }, "All statuses"), STATUSES.map((s) => el("option", { value: s }, fmt.label(s))));
  const type = el("select", { class: "input", "aria-label": "Type" }, el("option", { value: "" }, "All types"), TYPES.map((s) => el("option", { value: s }, fmt.label(s))));
  const tbody = el("tbody");
  const pagerHost = el("div");
  async function load() {
    const q = new URLSearchParams({ page: String(state.page), page_size: String(PAGE_SIZE) });
    if (state.q) q.set("q", state.q);
    if (state.status) q.set("status", state.status);
    if (state.type) q.set("campaign_type", state.type);
    try {
      const data = await api(`/campaigns?${q}`);
      tbody.replaceChildren(...(data.items.length
        ? data.items.map((c) => el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/campaigns/${c.id}`) },
            el("td", { class: "wrap" }, el("strong", {}, c.name)), el("td", {}, fmt.label(c.campaign_type)), el("td", {}, c.client_name ?? "Agency"),
            el("td", {}, statusBadge(c.status)), el("td", {}, c.is_email ? String(c.email_count) : "—"), el("td", {}, c.budget ? fmt.money(c.budget) : "—")))
        : [el("tr", {}, el("td", { colspan: "6", class: "empty-inline" }, state.q || state.status || state.type ? "No campaigns match." : "No campaigns yet."))]));
      pagerHost.replaceChildren(pager(state, data.total, PAGE_SIZE, load));
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "6" }, err.message)));
    }
  }
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); state.page = 1; load(); }, 300); });
  status.addEventListener("change", () => { state.status = status.value; state.page = 1; load(); });
  type.addEventListener("change", () => { state.type = type.value; state.page = 1; load(); });
  view.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, "Campaigns"), el("p", { class: "muted" }, "Plan and draft campaigns. Approved email campaigns are sent by AgencyOS once you start them; ads launch in your own tools.")),
      canWrite(user) ? el("button", { class: "btn btn-primary", type: "button", onclick: () => openCampaignForm(null, (c) => (window.location.hash = `#/campaigns/${c.id}`)) }, "New campaign") : null),
    el("section", { class: "card" }, el("div", { class: "card-body" }, el("div", { class: "toolbar" }, search, status, type)),
      el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Campaign", "Type", "Client", "Status", "Emails", "Budget"].map((h) => el("th", {}, h)))), tbody)), pagerHost),
  );
  load();
}

function audienceCard(c, writer, reload) {
  const a = c.audience ?? { stages: [], industry: null, city: null, min_score: null };
  const stageBoxes = STAGES.map((s) => el("label", { class: "check" }, el("input", { type: "checkbox", value: s, checked: (a.stages ?? []).includes(s), disabled: !writer || c.status !== "DRAFT" }), fmt.label(s)));
  const industry = el("input", { class: "input", placeholder: "Industry (exact)", value: a.industry ?? "", "aria-label": "Industry" });
  const city = el("input", { class: "input", placeholder: "City (exact)", value: a.city ?? "", "aria-label": "City" });
  const score = el("input", { class: "input", type: "number", min: "0", max: "100", placeholder: "Min lead score", value: a.min_score ?? "", "aria-label": "Minimum lead score" });
  const result = el("div", {});
  const show = (s) => result.replaceChildren(el("div", { class: "metric-grid mt-8" },
    [["Matched prospects", s.matched], ["With email", s.with_email], ["Suppressed (excluded)", s.suppressed], ["Eligible", s.eligible]].map(([l, v]) => el("div", { class: "metric" }, el("div", { class: "kpi-label" }, l), el("div", { class: "kpi-value" }, String(v))))),
    s.sample?.length ? el("p", { class: "small faint mt-8" }, `e.g. ${s.sample.map((p) => p.company_name).join(", ")}`) : null);
  if (c.audience_summary) show(c.audience_summary);
  const body = () => ({ stages: stageBoxes.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value), industry: industry.value.trim() || null, city: city.value.trim() || null, min_score: score.value === "" ? null : Number(score.value) });
  const previewBtn = el("button", { class: "btn btn-sm", type: "button", onclick: async () => { try { show(await api("/emails/segments/preview", { method: "POST", body: body() })); } catch (e) { toast(e.message, "error"); } } }, "Preview");
  const saveBtn = el("button", { class: "btn btn-sm btn-primary", type: "button", onclick: async () => { try { await api(`/campaigns/${c.id}/audience`, { method: "PUT", body: body() }); toast("Audience saved", "success"); reload(); } catch (e) { toast(e.message, "error"); } } }, "Save audience");
  const editable = writer && c.status === "DRAFT";
  return el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Audience (from your prospects)")),
    el("div", { class: "card-body" },
      el("div", { class: "chips" }, stageBoxes),
      el("div", { class: "toolbar mt-8" }, industry, city, score, editable ? previewBtn : null, editable ? saveBtn : null),
      el("p", { class: "small faint" }, "Suppressed and unsubscribed addresses are always excluded."),
      result));
}

const MSG_STATUSES = ["PENDING_APPROVAL", "SCHEDULED", "SENDING", "SENT", "FAILED", "SKIPPED", "CANCELLED"];
const MSG_LABELS = { PENDING_APPROVAL: "Awaiting approval", SCHEDULED: "Scheduled", SENDING: "Sending", SENT: "Sent", FAILED: "Failed", SKIPPED: "Skipped", CANCELLED: "Cancelled" };

// Every rendered email, exactly as approved; status updates as the sender works through them.
function outboxCard(c, sending) {
  const state = { status: "", page: 1 };
  const size = 25;
  const counts = c.outbox;
  const filter = el("select", { class: "input", "aria-label": "Email status" }, el("option", { value: "" }, "All emails"),
    MSG_STATUSES.filter((s) => counts[s]).map((s) => el("option", { value: s }, `${MSG_LABELS[s]} (${counts[s]})`)));
  const tbody = el("tbody");
  const pagerHost = el("div");
  const when = (m) => m.sent_at ? `Sent ${fmt.datetime(m.sent_at)}` : m.status === "SCHEDULED"
    ? (m.send_after ? `Due ${fmt.datetime(m.send_after)}` : m.step > 1 ? `${m.delay_days} day(s) after email ${m.step - 1}` : "When sending starts") : "—";
  async function load() {
    const q = new URLSearchParams({ page: String(state.page), page_size: String(size) });
    if (state.status) q.set("status", state.status);
    try {
      const data = await api(`/campaigns/${c.id}/outbox?${q}`);
      tbody.replaceChildren(...(data.items.length ? data.items.map((m) => el("tr", {},
        el("td", { class: "wrap" }, el("strong", {}, m.company_name ?? "—"), el("div", { class: "small faint" }, m.to_email)),
        el("td", {}, String(m.step)),
        el("td", { class: "wrap" }, m.subject, m.error ? el("div", { class: "small faint" }, m.error) : null),
        el("td", {}, statusBadge(m.status)),
        el("td", { class: "small" }, when(m)),
        el("td", {}, el("button", { class: "btn btn-sm", type: "button", onclick: () => showText(`To ${m.to_email}: ${m.subject}`, m.body_text) }, "Read"))))
        : [el("tr", {}, el("td", { colspan: "6", class: "empty-inline" }, "No emails with this status."))]));
      pagerHost.replaceChildren(pager(state, data.total, size, load));
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "6" }, err.message)));
    }
  }
  filter.addEventListener("change", () => { state.status = filter.value; state.page = 1; load(); });
  load();
  const sent = counts.SENT ?? 0;
  const total = Object.values(counts).reduce((a, b) => a + b, 0) - (counts.CANCELLED ?? 0);
  let notice = null;
  if (sending && !sending.enabled && (counts.SCHEDULED || counts.PENDING_APPROVAL)) {
    notice = el("p", { class: "notice notice-warning" }, "Email sending is turned off, so nothing will go out yet. Set it up in ", el("a", { href: "#/settings" }, "Settings → Email sending"), ".");
  } else if (sending?.last_error && counts.SCHEDULED && c.status === "ACTIVE") {
    notice = el("p", { class: "notice notice-warning" }, `Sending is held up: ${sending.last_error}`);
  } else if (sending?.redirect_to && counts.SCHEDULED) {
    notice = el("p", { class: "notice" }, `Test mode: every email goes to ${sending.redirect_to} instead of the prospect.`);
  }
  return el("section", { class: "card section-gap" },
    el("div", { class: "card-head" }, el("h2", {}, `Outbox · ${sent} of ${total} sent`), filter),
    el("div", { class: "card-body" },
      notice,
      el("p", { class: "small faint" }, c.status === "AWAITING_APPROVAL"
        ? "These are the exact emails the approver is deciding on."
        : "Exactly the emails that were approved. Follow-ups stop if the prospect unsubscribes or replies (stage moves to Responded or beyond).")),
    el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Recipient", "Step", "Subject", "Status", "When", ""].map((h) => el("th", {}, h)))), tbody)),
    pagerHost);
}

async function renderCampaign(ctx, id) {
  const { view, user } = ctx;
  let stop = null;
  view.replaceChildren(loadingState());
  const writer = canWrite(user);

  const aiHost = el("div", {});
  const aiList = (c) => aiTaskList(c.ai_tasks, { onImport: (t) => importFlow(`/campaigns/${id}/import/${t.id}`, (r) => `Imported: ${r.emails_created} email(s), ${r.drafts_saved} draft(s)`, draw) });

  async function draw() {
    const c = await api(`/campaigns/${id}`);
    const sending = c.is_email ? await api("/emails/sending").catch(() => null) : null;
    const queued = (c.outbox?.SCHEDULED ?? 0) + (c.outbox?.SENDING ?? 0);
    const reload = () => draw();
    const act = (fn, ok) => async () => { try { await fn(); if (ok) toast(ok, "success"); reload(); } catch (e) { toast(e.message, "error"); } };
    // Starting AI work: refresh, then keep polling until the agent finishes.
    const startAI = (path, body, ok) => async () => { try { await api(path, { method: "POST", body }); toast(ok, "success"); watch(); } catch (e) { toast(e.message, "error"); } };

    const head = [];
    if (writer && ["DRAFT", "APPROVED", "ACTIVE", "PAUSED"].includes(c.status)) head.push(el("button", { class: "btn", type: "button", onclick: () => openCampaignForm(c, reload) }, c.status === "DRAFT" ? "Edit" : "Change budget"));
    if (writer && c.status === "DRAFT") head.push(el("button", { class: "btn btn-primary", type: "button", onclick: () => requestApproval(c, reload) }, "Request approval"));
    const sends = c.is_email && queued > 0;
    const labels = sends ? { ...STATUS_LABELS, ACTIVE: c.status === "PAUSED" ? "Resume sending" : "Start sending", PAUSED: "Pause sending", CANCELLED: "Cancel unsent emails" } : STATUS_LABELS;
    if (writer) for (const s of c.allowed_status_changes) head.push(el("button", { class: s === "ACTIVE" && sends ? "btn btn-primary" : "btn", type: "button", onclick: async () => {
      let msg = `Mark this campaign as ${fmt.label(s).toLowerCase()}?`;
      if (s === "ACTIVE" && sends) {
        msg = sending?.enabled
          ? `${queued} approved email(s) will be sent from ${sending.from_name ? `${sending.from_name} <${sending.from}>` : sending.from}, one every ${sending.interval_seconds} seconds and at most ${sending.daily_limit} a day. Follow-ups go out on their scheduled day. You can pause at any time.`
          : "Email sending is turned off (EMAIL_TRANSPORT in .env), so the campaign will start but nothing is sent until it is set up. See Settings → Email sending.";
        if (sending?.redirect_to) msg += ` Test mode: every email goes to ${sending.redirect_to} instead.`;
      } else if (s === "ACTIVE") {
        msg = "AgencyOS does not launch ads. Only mark the campaign as running once you have launched it in your own ads tool.";
      } else if (s === "CANCELLED" && sends) {
        msg = `${queued} approved email(s) have not been sent yet; they will be cancelled. Sent emails are not affected.`;
      }
      if (await confirmDialog({ title: labels[s], message: msg, confirmLabel: labels[s] })) act(() => api(`/campaigns/${id}/status`, { method: "POST", body: { status: s } }), s === "ACTIVE" && sends ? "Sending started" : "Status updated")();
    } }, labels[s]));
    if (writer && !["ACTIVE", "AWAITING_APPROVAL"].includes(c.status)) head.push(el("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
      if (await confirmDialog({ title: "Delete campaign?", message: "The campaign and its drafts are hidden. History stays in the audit log.", confirmLabel: "Delete" })) {
        try { await api(`/campaigns/${id}`, { method: "DELETE" }); window.location.hash = "#/campaigns"; } catch (e) { toast(e.message, "error"); }
      }
    } }, "Delete"));

    const genButtons = writer && c.status === "DRAFT" ? el("div", { class: "row" }, [["strategy", "Strategy"], ...(c.is_email ? [["emails", "Email sequence"]] : []), ...(c.is_ads ? [["ads", "Ad copy"]] : []), ["audience", "Audience ideas"]].map(([k, l]) =>
      el("button", { class: "btn btn-sm", type: "button", onclick: startAI(`/campaigns/${id}/generate`, { kind: k }, "Agent is drafting") }, `Draft ${l}`))) : null;

    const emailRows = c.emails.map((e) => el("tr", {},
      el("td", {}, String(e.sequence_order ?? "—")), el("td", {}, `Day ${e.delay_days ?? 0}`),
      el("td", { class: "wrap" }, el("strong", {}, e.subject), el("div", { class: "small faint" }, e.name)),
      el("td", {}, e.compliant ? el("span", { class: "badge badge-success" }, "Compliant") : el("span", { class: "badge badge-danger" }, "Needs fixes")),
      el("td", {}, statusBadge(e.status)),
      el("td", {}, el("div", { class: "row" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => showText(e.subject, e.body_text) }, "View"),
        writer ? el("button", { class: "btn btn-sm", type: "button", title: "Send this email to your own address", onclick: () => sendTest(e) }, "Test to me") : null,
        writer && ["DRAFT", "APPROVED"].includes(c.status) ? el("button", { class: "btn btn-sm", type: "button", onclick: () => openEmailEditor({ title: "Edit email", values: e, extraDefs: [{ name: "delay_days", label: "Send on day", type: "number", min: "0" }], save: async (data, { close }) => { await api(`/emails/templates/${e.id}`, { method: "PATCH", body: data }); close(); reload(); } }) }, "Edit") : null,
        writer && ["DRAFT", "APPROVED"].includes(c.status) ? el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: async () => { if (await confirmDialog({ title: "Delete email?", message: e.subject, confirmLabel: "Delete" })) act(() => api(`/emails/templates/${e.id}`, { method: "DELETE" }))(); } }, "Delete") : null))));

    const m = c.metrics ?? {};
    const dm = c.derived_metrics;
    const metricsCard = el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Performance (entered manually)"),
      writer ? el("div", { class: "row" },
        el("button", { class: "btn btn-sm", type: "button", onclick: () => formDialog({
          title: "Update metrics",
          defs: [["sent", "Sent"], ["delivered", "Delivered"], ["opens", "Opens"], ["clicks", "Clicks"], ["replies", "Replies"], ["impressions", "Impressions"], ["leads", "Leads"], ["conversions", "Conversions"]].map(([n, l]) => ({ name: n, label: l, type: "number", min: "0" })).concat([{ name: "spend", label: "Spend (USD)", type: "number", min: "0", step: "0.01", money: true }]),
          values: m,
          intro: el("p", { class: "small muted mb-16" }, "Copy these from your email or ads platform. AgencyOS calculates the rates."),
          submit: async (data, { close }) => { await api(`/campaigns/${id}/metrics`, { method: "PUT", body: data }); close(); reload(); },
        }) }, "Update metrics"),
        Object.keys(m).length ? el("button", { class: "btn btn-sm", type: "button", onclick: startAI(`/campaigns/${id}/analyze`, undefined, "Analytics Agent is reviewing the numbers") }, "Analyse with AI") : null) : null),
      el("div", { class: "card-body" }, Object.keys(m).length ? el("div", { class: "metric-grid" },
        [["Sent", m.sent], ["Opens", m.opens], ["Clicks", m.clicks], ["Replies", m.replies], ["Impressions", m.impressions], ["Leads", m.leads], ["Conversions", m.conversions], ["Spend", m.spend != null ? fmt.money(m.spend) : null],
          ["Open rate", dm.open_rate != null ? `${dm.open_rate}%` : null], ["Click rate", dm.click_rate != null ? `${dm.click_rate}%` : null], ["Reply rate", dm.reply_rate != null ? `${dm.reply_rate}%` : null], ["CTR", dm.ctr != null ? `${dm.ctr}%` : null], ["Conversion", dm.conversion_rate != null ? `${dm.conversion_rate}%` : null], ["Cost per lead", dm.cost_per_lead ? fmt.money(dm.cost_per_lead) : null]]
          .filter(([, v]) => v != null).map(([l, v]) => el("div", { class: "metric" }, el("div", { class: "kpi-label" }, l), el("div", { class: "kpi-value" }, String(v)))))
        : el("p", { class: "empty-inline" }, "No metrics yet. Enter them once the campaign has run.")));

    view.replaceChildren(
      el("div", { class: "page-head" },
        el("div", {}, el("a", { href: "#/campaigns", class: "small" }, "← All campaigns"), el("h1", {}, c.name), el("p", { class: "muted" }, `${fmt.label(c.campaign_type)} · ${c.client_name ?? "Agency's own"}`)),
        el("div", { class: "row" }, statusBadge(c.status), head)),
      c.status === "AWAITING_APPROVAL" ? el("p", { class: "notice notice-warning mb-16" }, "Waiting for a Founder/Admin decision in ", el("a", { href: "#/approvals" }, "Approvals"), ". Content is locked until then.") : null,
      el("div", { class: "grid two-col" },
        el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Overview")), el("div", { class: "card-body" }, kvList([
          ["Budget", c.budget ? fmt.money(c.budget) : null], ["Dates", c.start_date || c.end_date ? `${c.start_date ?? "?"} → ${c.end_date ?? "?"}` : null],
          ["Target audience", c.target_audience ? el("span", { class: "pre" }, c.target_audience) : null], ["Objectives", c.objectives ? el("span", { class: "pre" }, c.objectives) : null],
        ]))),
        el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Ready for approval?")), el("div", { class: "card-body" }, issueList(c.readiness, "Ready to request approval."),
          c.approvals.length ? el("div", { class: "section-gap" }, el("h3", {}, "Approval history"), el("ul", { class: "list-plain small" }, c.approvals.map((a) => el("li", {}, `${fmt.label(a.action_type)} · ${fmt.label(a.status)} · ${fmt.datetime(a.decided_at ?? a.created_at)}${a.comments ? ` — ${a.comments}` : ""}`)))) : null))),
      c.is_email ? audienceCard(c, writer, reload) : el("span"),
      el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "AI drafts"), genButtons),
        el("div", { class: "card-body" }, el("p", { class: "small faint" }, "Agents draft; you choose what to import. Their suggestions to send or launch are not queued here; use Request approval."),
          aiHost)),
      c.is_email ? el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, `Email sequence (${c.emails.length})`),
        writer && c.status === "DRAFT" ? el("button", { class: "btn btn-sm", type: "button", onclick: () => openEmailEditor({ title: "Add email", values: { body_text: "Hi {{first_name}},\n\n\n\n—\n{{sender_name}}, {{agency_name}}\n{{agency_address}}\nUnsubscribe: {{unsubscribe_link}}" }, extraDefs: [{ name: "delay_days", label: "Send on day", type: "number", min: "0" }], save: async (data, { close }) => { await api("/emails/templates", { method: "POST", body: { ...data, campaign_id: c.id } }); close(); reload(); } }) }, "Add email") : null),
        emailRows.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["#", "When", "Subject", "Compliance", "Status", ""].map((h) => el("th", {}, h)))), el("tbody", {}, emailRows))) : el("div", { class: "card-body empty-inline" }, "No emails yet. Draft a sequence with AI or add one.")) : el("span"),
      c.outbox && Object.keys(c.outbox).length ? outboxCard(c, sending) : el("span"),
      c.drafts.length ? el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Saved drafts")),
        el("div", { class: "card-body" }, c.drafts.map((d, i) => el("div", { class: "deliverable" }, el("div", { class: "deliverable-head" }, el("strong", {}, d.title), el("div", { class: "row" }, el("span", { class: "badge" }, fmt.label(d.kind)), writer ? el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: act(() => api(`/campaigns/${id}/drafts/${i}`, { method: "DELETE" })) }, "Remove") : null)), el("div", { class: "deliverable-body pre" }, d.content))))) : el("span"),
      metricsCard,
    );
    aiHost.replaceChildren(aiList(c));
    return anyActive(c.ai_tasks);
  }

  // While agents work, refresh only the AI section so forms on the page keep their input.
  async function refreshAI() {
    const c = await api(`/campaigns/${id}`);
    aiHost.replaceChildren(aiList(c));
    return anyActive(c.ai_tasks);
  }

  function requestApproval(c, reload) {
    const isEmail = c.is_email;
    formDialog({
      title: `Request approval: ${c.name}`,
      defs: isEmail ? [
        { name: "consent_basis", label: "Why may you email these people?", type: "select", options: CONSENT, full: true },
        { name: "notes", label: "Notes for the approver", type: "textarea" },
      ] : [{ name: "notes", label: "Notes for the approver", type: "textarea" }],
      intro: el("div", { class: "mb-16" },
        el("p", { class: "notice" }, isEmail ? "Each recipient's emails are written out now, with their details filled in. The approver can read every one in the Outbox. After approval, AgencyOS sends exactly those emails once you click Start sending." : "Approval allows the campaign to be launched in your ads tool. AgencyOS does not launch it."),
        isEmail ? el("label", { class: "check mt-8" }, el("input", { type: "checkbox", id: "confirm-compliance" }), "Every email identifies the sender, includes our postal address and an unsubscribe link, and suppressed addresses will be excluded.") : null),
      submitLabel: "Send for approval",
      submit: async (data, { close }) => {
        const confirm = document.getElementById("confirm-compliance");
        await api(`/campaigns/${c.id}/request-approval`, { method: "POST", body: { ...data, confirm_compliance: !!confirm?.checked } });
        close();
        toast("Sent for approval", "success");
        reload();
      },
    });
  }

  async function sendTest(e) {
    const { items } = await api("/prospects?page_size=100").catch(() => ({ items: [] }));
    formDialog({
      title: "Send a test to yourself",
      defs: [{ name: "prospect_id", label: "Fill in details as if sent to", type: "select", numeric: true, full: true,
        options: [["", "No prospect (merge fields show [missing: …])"], ...items.filter((p) => p.email).map((p) => [p.id, `${p.company_name} (${p.email})`])] }],
      intro: el("p", { class: "small muted mb-16" }, `"${e.subject}" goes only to your own address (${user.email}), marked [Test]. No prospect receives it.`),
      submitLabel: "Send test",
      submit: async (data, { close }) => {
        const r = await api(`/emails/templates/${e.id}/test-send`, { method: "POST", body: { prospect_id: data.prospect_id ?? null } });
        close();
        toast(`Test sent to ${r.sent_to}`, "success");
      },
    });
  }

  function watch() { stop?.(); stop = poll(refreshAI, 1500); }
  try {
    if (await draw()) watch();
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderCampaign(ctx, id)));
  }
  return () => stop?.();
}


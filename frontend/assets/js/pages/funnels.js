// Funnels: industry templates, form builder, landing-page preview (desktop/mobile), validation, publish approval.
import { api } from "../api.js";
import { canWrite, confirmDialog, el, errorState, formDialog, loadingState, pager, poll, statusBadge, toast } from "../ui.js";
import { aiTaskList, anyActive, importFlow, issueList } from "./marketing-shared.js";

const FIELD_TYPES = ["text", "email", "tel", "select", "textarea", "number"];
const PAGE_SIZE = 25;

export async function renderFunnels(ctx) {
  if (ctx.params[0]) return renderFunnel(ctx, Number(ctx.params[0]));
  const { view, user } = ctx;
  const state = { page: 1 };
  const tbody = el("tbody");
  const pagerHost = el("div");
  async function load() {
    try {
      const data = await api(`/funnels?page=${state.page}&page_size=${PAGE_SIZE}`);
      tbody.replaceChildren(...(data.items.length
        ? data.items.map((f) => el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/funnels/${f.id}`) },
            el("td", { class: "wrap" }, el("strong", {}, f.name)), el("td", {}, f.industry ?? "—"), el("td", {}, f.client_name ?? "—"),
            el("td", {}, statusBadge(f.status)), el("td", {}, f.ready ? el("span", { class: "badge badge-success" }, "Ready") : el("span", { class: "badge badge-warning" }, "Needs work"))))
        : [el("tr", {}, el("td", { colspan: "5", class: "empty-inline" }, "No funnels yet. Start from an industry template."))]));
      pagerHost.replaceChildren(pager(state, data.total, PAGE_SIZE, load));
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "5" }, err.message)));
    }
  }
  const create = async () => {
    const [{ items }, { clients }] = await Promise.all([api("/funnels/templates"), api("/projects/options")]);
    formDialog({
      title: "New funnel",
      defs: [
        { name: "name", label: "Funnel name", required: true, full: true },
        { name: "template_key", label: "Start from", type: "select", options: [["", "Blank"], ...items.map((t) => [t.key, `${t.industry}: ${t.name}`])] },
        { name: "client_id", label: "Client", type: "select", options: [["", "None"], ...clients.map((c) => [c.id, c.name])], numeric: true },
      ],
      submit: async (d, { close }) => { const f = await api("/funnels", { method: "POST", body: d }); close(); window.location.hash = `#/funnels/${f.id}`; },
    });
  };
  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Funnels"), el("p", { class: "muted" }, "Landing pages with lead-capture forms. AgencyOS generates and previews the page; you host it after approval.")),
      canWrite(user) ? el("button", { class: "btn btn-primary", type: "button", onclick: create }, "New funnel") : null),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Funnel", "Industry", "Client", "Status", "Validation"].map((h) => el("th", {}, h)))), tbody)), pagerHost),
  );
  load();
}

function fieldEditor(fields) {
  const host = el("div", {});
  const rows = [];
  const addRow = (f = { name: "", label: "", type: "text", required: false, options: [] }) => {
    const name = el("input", { class: "input", value: f.name, placeholder: "name", "aria-label": "Field name" });
    const label = el("input", { class: "input", value: f.label, placeholder: "Label", "aria-label": "Field label" });
    const type = el("select", { class: "input", "aria-label": "Field type" }, FIELD_TYPES.map((t) => el("option", { value: t, selected: t === f.type }, t)));
    const req = el("input", { type: "checkbox", checked: !!f.required, "aria-label": "Required" });
    const options = el("input", { class: "input", value: (f.options ?? []).join(", "), placeholder: "Options (for select)", "aria-label": "Options" });
    const row = el("div", { class: "field-row" }, name, label, type, el("label", { class: "check" }, req, "Req."), options,
      el("button", { class: "btn btn-sm btn-ghost", type: "button", "aria-label": "Remove field", onclick: () => { row.remove(); rows.splice(rows.indexOf(entry), 1); } }, "×"));
    const entry = { row, read: () => ({ name: name.value.trim(), label: label.value.trim(), type: type.value, required: req.checked, options: options.value.split(",").map((s) => s.trim()).filter(Boolean) }) };
    rows.push(entry);
    host.append(row);
  };
  fields.forEach(addRow);
  const wrap = el("div", {}, host, el("button", { class: "btn btn-sm mt-8", type: "button", onclick: () => addRow() }, "Add field"));
  return { el: wrap, read: () => rows.map((r) => r.read()) };
}

async function renderFunnel(ctx, id) {
  const { view, user } = ctx;
  const writer = canWrite(user);
  let stop = null;
  let mobile = false;
  const aiHost = el("div", {});
  const aiList = (f, reload) => aiTaskList(f.ai_tasks, { importLabel: "Use this copy", onImport: (t) => importFlow(`/funnels/${id}/import/${t.id}`, (r) => `Imported: ${r.used.join(", ")}`, reload) });
  view.replaceChildren(loadingState());

  async function draw() {
    const f = await api(`/funnels/${id}`);
    const editable = writer && f.status !== "AWAITING_APPROVAL";
    const copy = f.form_config.copy ?? {};
    const reload = () => draw();
    const act = (fn, ok) => async () => { try { await fn(); if (ok) toast(ok, "success"); reload(); } catch (e) { toast(e.message, "error"); } };

    const head = [el("a", { class: "btn", href: `/api/v1/funnels/${id}/download`, download: "" }, "Download HTML")];
    if (writer && f.status === "DRAFT") head.push(el("button", { class: "btn btn-primary", type: "button", onclick: act(() => api(`/funnels/${id}/request-approval`, { method: "POST" }), "Sent for approval") }, "Request publish approval"));
    if (writer && ["APPROVED", "PUBLISHED"].includes(f.status)) head.push(el("button", { class: "btn btn-primary", type: "button", onclick: () => formDialog({
      title: "Mark as published", defs: [{ name: "landing_page_url", label: "Live URL", type: "url", required: true, full: true }], values: { landing_page_url: f.landing_page_url },
      intro: el("p", { class: "small muted mb-16" }, "Upload the downloaded HTML to your hosting first, then record where it lives."),
      submit: async (d, { close }) => { await api(`/funnels/${id}/published`, { method: "POST", body: d }); close(); reload(); },
    }) }, "Mark as published"));
    if (writer && f.status !== "AWAITING_APPROVAL") head.push(el("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
      if (await confirmDialog({ title: "Delete funnel?", message: f.name, confirmLabel: "Delete" })) { await api(`/funnels/${id}`, { method: "DELETE" }); window.location.hash = "#/funnels"; }
    } }, "Delete"));

    // Content form
    const inputs = {
      headline: el("input", { class: "input", id: "fn-headline", value: copy.headline ?? "" }),
      subheadline: el("input", { class: "input", id: "fn-sub", value: copy.subheadline ?? "" }),
      benefits: el("textarea", { class: "input", id: "fn-benefits" }),
      proof: el("input", { class: "input", id: "fn-proof", value: copy.proof ?? "" }),
      business_name: el("input", { class: "input", id: "fn-biz", value: copy.business_name ?? "" }),
      phone_display: el("input", { class: "input", id: "fn-phone", value: copy.phone_display ?? "" }),
      cta: el("input", { class: "input", id: "fn-cta", value: f.call_to_action ?? "" }),
      submit_label: el("input", { class: "input", id: "fn-submit", value: f.form_config.submit_label ?? "" }),
      consent: el("textarea", { class: "input", id: "fn-consent" }),
      target: el("input", { class: "input", id: "fn-target", value: f.target_conversion ?? "" }),
    };
    inputs.benefits.value = (copy.benefits ?? []).join("\n");
    inputs.consent.value = f.form_config.consent_text ?? "";
    const fieldsEd = fieldEditor(f.lead_fields);
    const lab = (t, n, hint) => el("div", { class: "field" }, el("label", { for: n.id }, t), n, hint ? el("span", { class: "field-hint" }, hint) : null);
    const save = el("button", { class: "btn btn-primary", type: "button", disabled: !editable, onclick: act(() => api(`/funnels/${id}`, { method: "PATCH", body: {
      call_to_action: inputs.cta.value.trim() || null, target_conversion: inputs.target.value.trim() || null,
      lead_fields: fieldsEd.read(),
      form_config: { ...f.form_config, submit_label: inputs.submit_label.value.trim() || null, consent_text: inputs.consent.value.trim() || null,
        copy: { headline: inputs.headline.value.trim() || null, subheadline: inputs.subheadline.value.trim() || null, proof: inputs.proof.value.trim() || null,
          business_name: inputs.business_name.value.trim() || null, phone_display: inputs.phone_display.value.trim() || null,
          benefits: inputs.benefits.value.split("\n").map((s) => s.trim()).filter(Boolean) } },
    } }), f.status === "DRAFT" ? "Saved" : "Saved; the funnel needs approval again") }, "Save changes");

    const frame = el("iframe", { class: `preview-frame${mobile ? " mobile" : ""}`, src: `/api/v1/funnels/${id}/preview?v=${encodeURIComponent(f.updated_at)}`, sandbox: "", title: "Landing page preview", loading: "lazy" });
    const toggle = el("div", { class: "row" }, ["Desktop", "Mobile"].map((l) => el("button", { class: `btn btn-sm${(l === "Mobile") === mobile ? " btn-primary" : ""}`, type: "button", onclick: () => { mobile = l === "Mobile"; frame.classList.toggle("mobile", mobile); toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("btn-primary", (b.textContent === "Mobile") === mobile)); } }, l)));

    view.replaceChildren(
      el("div", { class: "page-head" },
        el("div", {}, el("a", { href: "#/funnels", class: "small" }, "← All funnels"), el("h1", {}, f.name), el("p", { class: "muted" }, [f.industry, f.client_name, f.landing_page_url].filter(Boolean).join(" · ") || "—")),
        el("div", { class: "row" }, statusBadge(f.status), head)),
      f.status === "AWAITING_APPROVAL" ? el("p", { class: "notice notice-warning mb-16" }, "Waiting for a decision in ", el("a", { href: "#/approvals" }, "Approvals"), ". Editing is locked.") : el("span"),
      f.status === "APPROVED" ? el("p", { class: "notice mb-16" }, "Approved. Download the HTML, host it, then mark it as published with the live URL. Connect the form to a form handler or CRM first.") : el("span"),
      el("div", { class: "grid two-col" },
        el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Form & content checks")), el("div", { class: "card-body" }, issueList(f.validation, "Form and content are ready."))),
        el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Mobile checks")), el("div", { class: "card-body" }, el("ul", { class: "list-plain small" }, f.mobile_checks.map((c) => el("li", { class: c.passed ? "score-high" : "text-danger" }, `${c.passed ? "✓" : "✗"} ${c.check}`)))))),
      el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Preview"), toggle),
        el("div", { class: "card-body" }, el("div", { class: "preview-frame-wrap" }, frame), el("p", { class: "small faint mt-8" }, "Sandboxed preview: no scripts run and the form cannot submit. ",
          el("a", { href: `/api/v1/funnels/${id}/preview`, target: "_blank", rel: "noopener" }, "Open preview in a new tab")))),
      el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Page content"),
        editable && f.status === "DRAFT" ? el("button", { class: "btn btn-sm", type: "button", onclick: async () => { try { await api(`/funnels/${id}/generate`, { method: "POST", body: {} }); toast("Funnel Agent is writing copy", "success"); watch(); } catch (e) { toast(e.message, "error"); } } }, "Draft copy with AI") : null),
        el("div", { class: "card-body" },
          el("div", { class: "form-row" }, lab("Business name", inputs.business_name), lab("Phone shown", inputs.phone_display)),
          lab("Headline", inputs.headline), lab("Subheadline", inputs.subheadline),
          lab("Benefits (one per line)", inputs.benefits), lab("Proof", inputs.proof, "A real review, licence number or result. Never invent one; leave empty if you have none."),
          el("div", { class: "form-row" }, lab("Call to action", inputs.cta), lab("Button text", inputs.submit_label)),
          lab("Target conversion", inputs.target), lab("Consent text", inputs.consent),
          el("h3", { class: "section-gap" }, "Lead form fields"), fieldsEd.el,
          el("div", { class: "row section-gap" }, save))),
      el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "Follow-up workflow")), el("div", { class: "card-body" },
        f.follow_up_steps.length ? el("ol", { class: "list-plain" }, f.follow_up_steps.map((s) => el("li", {}, `${s.delay_minutes ? `After ${s.delay_minutes >= 1440 ? `${s.delay_minutes / 1440} day(s)` : `${s.delay_minutes} min`}` : "Immediately"}: ${s.description}`))) : el("p", { class: "empty-inline" }, "No follow-up steps."),
        el("p", { class: "small faint" }, "Plan only. These steps run in your CRM/automation tool, not in AgencyOS."))),
      el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "AI copy drafts")), el("div", { class: "card-body" }, aiHost)),
    );
    aiHost.replaceChildren(aiList(f, reload));
    return anyActive(f.ai_tasks);
  }
  // Poll only the AI section; the rest of the page (and any unsaved edits) stays as it is.
  async function refreshAI() {
    const f = await api(`/funnels/${id}`);
    aiHost.replaceChildren(aiList(f, draw));
    return anyActive(f.ai_tasks);
  }
  function watch() { stop?.(); stop = poll(refreshAI, 1500); }
  try {
    if (await draw()) watch();
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderFunnel(ctx, id)));
  }
  return () => stop?.();
}


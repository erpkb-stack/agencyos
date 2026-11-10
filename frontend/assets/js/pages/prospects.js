// Prospects: searchable pipeline list, CSV import/export, detail with activity timeline.
import { api } from "../api.js";
import { canWrite, confirmDialog, el, errorState, fmt, formDialog, kvList, loadingState, modal, pager, statusBadge, toast } from "../ui.js";

const STAGES = ["NEW", "RESEARCHED", "QUALIFIED", "CONTACTED", "RESPONDED", "MEETING_SCHEDULED", "PROPOSAL_SENT", "NEGOTIATION", "WON", "LOST"];
const SORTS = [["created_at", "Newest"], ["updated_at", "Recently updated"], ["company_name", "Company A–Z"], ["lead_score", "Lead score"], ["next_follow_up_on", "Next follow-up"], ["stage", "Stage"]];
const PAGE_SIZE = 25;

const DEFS = [
  { name: "company_name", label: "Company name", required: true },
  { name: "website", label: "Website", type: "url" },
  { name: "industry", label: "Industry" },
  { name: "lead_source", label: "Lead source", hint: "Where this record came from, e.g. referral, directory export" },
  { name: "contact_name", label: "Contact person" },
  { name: "contact_title", label: "Contact title" },
  { name: "phone", label: "Business phone", type: "tel" },
  { name: "email", label: "Business email", type: "email" },
  { name: "address", label: "Business address", full: true },
  { name: "city", label: "City" },
  { name: "state", label: "State" },
  { name: "zip_code", label: "ZIP code" },
  { name: "lead_score", label: "Lead score (0–100)", type: "number", min: "0", max: "100" },
  { name: "stage", label: "Sales stage", type: "select", options: STAGES, default: "NEW" },
  { name: "next_follow_up_on", label: "Next follow-up", type: "date" },
  { name: "observations", label: "Business observations", type: "textarea" },
  { name: "opportunity", label: "Potential opportunity", type: "textarea" },
  { name: "notes", label: "Notes", type: "textarea" },
];

function scoreEl(score) {
  return el("span", { class: `score${score >= 70 ? " score-high" : score >= 40 ? " score-mid" : ""}` }, String(score));
}

function dupeError(err) {
  const dupes = err.details?.duplicates;
  if (!dupes?.length) return null;
  return el("div", {}, "Possible duplicates:", el("ul", { class: "dupe-list" }, dupes.map((d) => el("li", {}, el("a", { href: `#/prospects/${d.id}` }, d.company_name), ` (${d.reasons.join(", ")})`))));
}

export function openProspectForm(existing, onSaved) {
  const allow = el("label", { class: "check hidden" }, el("input", { type: "checkbox" }), "Save anyway, I checked it is not a duplicate");
  formDialog({
    title: existing ? `Edit ${existing.company_name}` : "Add prospect",
    defs: DEFS,
    values: existing ?? { stage: "NEW" },
    intro: el("div", { class: "mb-16" }, el("p", { class: "small muted" }, "Enter only real details you have verified. Leave unknown fields empty."), allow),
    submit: async (values, { close, error }) => {
      try {
        const saved = existing
          ? await api(`/prospects/${existing.id}`, { method: "PATCH", body: values })
          : await api("/prospects", { method: "POST", body: { ...values, allow_duplicate: allow.querySelector("input").checked } });
        close();
        toast(existing ? "Prospect saved" : "Prospect added", "success");
        onSaved(saved);
      } catch (err) {
        const extra = err.status === 409 ? dupeError(err) : null;
        if (extra) {
          allow.classList.remove("hidden");
          error.replaceChildren(document.createTextNode(err.message), extra);
          error.classList.remove("hidden");
          return;
        }
        throw err;
      }
    },
  });
}

function openImport(onDone) {
  const file = el("input", { class: "input file-input", type: "file", accept: ".csv,text/csv", id: "csv-file" });
  const skip = el("input", { type: "checkbox", checked: true });
  const result = el("div", {});
  const run = el("button", { class: "btn btn-primary", type: "button" }, "Import");
  const handle = modal({
    title: "Import prospects from CSV",
    wide: true,
    body: el("div", {},
      el("p", { class: "small muted" }, "UTF-8 CSV, up to 2 MB / 5,000 rows. A company name column is required. Recognised columns include Company, Website, Industry, Address, City, State, ZIP, Phone, Email, Contact, Title, Source, Score, Stage, Notes."),
      el("div", { class: "field" }, el("label", { for: "csv-file" }, "CSV file"), file),
      el("label", { class: "check mb-16" }, skip, "Skip rows that match an existing prospect"),
      result),
    actions: [el("button", { class: "btn", type: "button", onclick: () => handle.close() }, "Close"), run],
  });
  run.addEventListener("click", async () => {
    if (!file.files.length) return toast("Choose a CSV file first", "error");
    const form = new FormData();
    form.append("file", file.files[0]);
    run.disabled = true;
    try {
      const r = await api(`/prospects/import?skip_duplicates=${skip.checked}`, { method: "POST", body: form });
      result.replaceChildren(
        el("p", { class: "notice" }, `${r.created} added · ${r.skipped_duplicates.length} duplicates skipped · ${r.errors.length} rows with errors`),
        r.ignored_columns.length ? el("p", { class: "small muted" }, `Ignored columns: ${r.ignored_columns.join(", ")}`) : null,
        r.skipped_duplicates.length ? el("ul", { class: "list-plain small" }, r.skipped_duplicates.slice(0, 20).map((s) => el("li", {}, `Row ${s.row}: ${s.company_name} (${s.reasons.join(", ")})`))) : null,
        r.errors.length ? el("ul", { class: "list-plain small" }, r.errors.slice(0, 20).map((e) => el("li", {}, `Row ${e.row}: ${e.error}`))) : null,
      );
      onDone();
    } catch (err) {
      result.replaceChildren(el("div", { class: "form-error" }, err.message));
    } finally {
      run.disabled = false;
    }
  });
}

export async function renderProspects(ctx) {
  if (ctx.params[0]) return renderProspect(ctx, Number(ctx.params[0]));
  const { view, user } = ctx;
  const state = { q: "", stage: "", industry: "", city: "", follow_up_due: false, sort: "created_at", order: "desc", page: 1 };
  const search = el("input", { class: "input search", type: "search", placeholder: "Search company, contact, email, city…", "aria-label": "Search prospects" });
  const stage = el("select", { class: "input", "aria-label": "Stage" }, el("option", { value: "" }, "All stages"), STAGES.map((s) => el("option", { value: s }, fmt.label(s))));
  const industry = el("select", { class: "input", "aria-label": "Industry" }, el("option", { value: "" }, "All industries"));
  const city = el("select", { class: "input", "aria-label": "City" }, el("option", { value: "" }, "All cities"));
  const sort = el("select", { class: "input", "aria-label": "Sort" }, SORTS.map(([v, l]) => el("option", { value: v }, l)));
  const due = el("input", { type: "checkbox" });
  const tbody = el("tbody");
  const pagerHost = el("div");
  const exportLink = el("a", { class: "btn", href: "/api/v1/prospects/export", download: "" }, "Export CSV");

  function query() {
    const p = new URLSearchParams();
    for (const k of ["q", "stage", "industry", "city"]) if (state[k]) p.set(k, state[k]);
    if (state.follow_up_due) p.set("follow_up_due", "true");
    p.set("sort", state.sort);
    p.set("order", state.sort === "company_name" || state.sort === "next_follow_up_on" ? "asc" : "desc");
    return p;
  }

  function fillFacet(select, values, current) {
    select.replaceChildren(select.firstChild, ...values.map((v) => el("option", { value: v, selected: v === current }, v)));
  }

  async function load() {
    const q = query();
    exportLink.href = `/api/v1/prospects/export?${q}`;
    q.set("page", String(state.page));
    q.set("page_size", String(PAGE_SIZE));
    try {
      const data = await api(`/prospects?${q}`);
      fillFacet(industry, data.facets.industries, state.industry);
      fillFacet(city, data.facets.cities, state.city);
      const filtered = state.q || state.stage || state.industry || state.city || state.follow_up_due;
      tbody.replaceChildren(...(data.items.length
        ? data.items.map((p) => el("tr", { class: "clickable", onclick: () => (window.location.hash = `#/prospects/${p.id}`) },
            el("td", { class: "wrap" }, el("strong", {}, p.company_name), p.contact_name ? el("div", { class: "small faint" }, p.contact_name) : null),
            el("td", {}, [p.city, p.state].filter(Boolean).join(", ") || "—"),
            el("td", {}, p.industry ?? "—"),
            el("td", {}, statusBadge(p.stage)),
            el("td", {}, scoreEl(p.lead_score)),
            el("td", {}, p.next_follow_up_on ?? "—"),
            el("td", {}, fmt.relative(p.updated_at))))
        : [el("tr", {}, el("td", { colspan: "7", class: "empty-inline" }, filtered ? "No prospects match these filters." : "No prospects yet. Add one or import a CSV."))]));
      pagerHost.replaceChildren(pager(state, data.total, PAGE_SIZE, load));
    } catch (err) {
      tbody.replaceChildren(el("tr", {}, el("td", { colspan: "7" }, err.message)));
    }
  }
  const reset = () => { state.page = 1; load(); };
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); reset(); }, 300); });
  stage.addEventListener("change", () => { state.stage = stage.value; reset(); });
  industry.addEventListener("change", () => { state.industry = industry.value; reset(); });
  city.addEventListener("change", () => { state.city = city.value; reset(); });
  sort.addEventListener("change", () => { state.sort = sort.value; reset(); });
  due.addEventListener("change", () => { state.follow_up_due = due.checked; reset(); });

  view.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, "Prospects"), el("p", { class: "muted" }, "Your sales pipeline. Records are entered by you or imported; agents never invent contact details.")),
      el("div", { class: "row" }, exportLink,
        canWrite(user) ? el("button", { class: "btn", type: "button", onclick: () => openImport(load) }, "Import CSV") : null,
        canWrite(user) ? el("button", { class: "btn btn-primary", type: "button", onclick: () => openProspectForm(null, (p) => (window.location.hash = `#/prospects/${p.id}`)) }, "Add prospect") : null)),
    el("section", { class: "card" },
      el("div", { class: "card-body" }, el("div", { class: "toolbar" }, search, stage, industry, city, sort, el("label", { class: "check" }, due, "Follow-up due"))),
      el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, ["Company", "Location", "Industry", "Stage", "Score", "Follow-up", "Updated"].map((h) => el("th", {}, h)))), tbody)),
      pagerHost),
  );
  load();
}

async function renderProspect(ctx, id) {
  const { view, user } = ctx;
  view.replaceChildren(loadingState());
  let p;
  try {
    p = await api(`/prospects/${id}`);
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderProspect(ctx, id)));
    return;
  }
  const reload = () => renderProspect(ctx, id);
  const writer = canWrite(user);

  const stageSelect = el("select", { class: "input inline-select", "aria-label": "Sales stage", disabled: !writer }, STAGES.map((s) => el("option", { value: s, selected: s === p.stage }, fmt.label(s))));
  stageSelect.addEventListener("change", async () => {
    try {
      await api(`/prospects/${id}`, { method: "PATCH", body: { stage: stageSelect.value } });
      toast("Stage updated", "success");
      reload();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  const actions = writer ? [
    el("button", { class: "btn", type: "button", onclick: () => openProspectForm(p, reload) }, "Edit"),
    el("button", { class: "btn", type: "button", onclick: async () => {
      try {
        const { task_id } = await api(`/prospects/${id}/research`, { method: "POST", body: {} });
        toast("Sales Agent is researching this record", "success");
        window.location.hash = `#/tasks/${task_id}`;
      } catch (err) {
        toast(err.message, "error");
      }
    } }, "Ask Sales Agent"),
    p.client_id
      ? el("a", { class: "btn", href: `#/clients/${p.client_id}` }, "View client")
      : el("button", { class: "btn btn-primary", type: "button", onclick: () => formDialog({
          title: `Convert ${p.company_name} to a client`,
          defs: [
            { name: "service_package", label: "Service package" },
            { name: "start_date", label: "Start date", type: "date" },
            { name: "monthly_fee", label: "Monthly fee (USD)", type: "number", min: "0", step: "0.01", money: true },
            { name: "setup_fee", label: "One-time setup fee (USD)", type: "number", min: "0", step: "0.01", money: true },
          ],
          values: { start_date: fmt.today() },
          submitLabel: "Convert",
          intro: el("p", { class: "small muted mb-16" }, "Creates an active client from this record and marks the prospect as Won."),
          submit: async (values, { close }) => {
            const client = await api(`/prospects/${id}/convert`, { method: "POST", body: { ...values, monthly_fee: values.monthly_fee ?? "0", setup_fee: values.setup_fee ?? "0" } });
            close();
            toast("Client created", "success");
            window.location.hash = `#/clients/${client.id}`;
          },
        }) }, "Convert to client"),
    el("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
      if (!(await confirmDialog({ title: `Delete ${p.company_name}?`, message: "The prospect is removed from lists and search. Its history is kept for the audit log.", confirmLabel: "Delete" }))) return;
      try {
        await api(`/prospects/${id}`, { method: "DELETE" });
        toast("Prospect deleted", "success");
        window.location.hash = "#/prospects";
      } catch (err) {
        toast(err.message, "error");
      }
    } }, "Delete"),
  ] : [];

  // Activity
  const actType = el("select", { class: "input inline-select", "aria-label": "Activity type" }, ["note", "call", "email", "meeting", "follow_up"].map((t) => el("option", { value: t }, fmt.label(t))));
  const actText = el("textarea", { class: "input", id: "act-text", placeholder: "What happened?", maxlength: "5000" });
  const contacted = el("input", { type: "checkbox" });
  const actForm = el("form", { class: "mb-16" }, actText, el("div", { class: "row mt-8" }, actType, el("label", { class: "check" }, contacted, "Counts as contact"), el("button", { class: "btn btn-primary btn-sm ml-auto", type: "submit" }, "Log activity")));
  actForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!actText.value.trim()) return;
    try {
      await api(`/prospects/${id}/activities`, { method: "POST", body: { activity_type: actType.value, description: actText.value.trim(), mark_contacted: contacted.checked } });
      reload();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  const timeline = el("ul", { class: "timeline" }, p.activities.map((a) => el("li", {},
    el("div", { class: "row" }, el("span", { class: "badge" }, fmt.label(a.activity_type)), el("span", { class: "tl-meta" }, `${a.user ?? a.agent ?? "System"} · ${fmt.datetime(a.created_at)}`)),
    el("div", { class: "pre mt-8" }, a.description))));

  const link = (url) => (url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, url) : null);
  view.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("a", { href: "#/prospects", class: "small" }, "← All prospects"), el("h1", {}, p.company_name), el("p", { class: "muted" }, [p.industry, [p.city, p.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ") || "No industry or location yet")),
      el("div", { class: "row" }, stageSelect, actions)),
    el("div", { class: "grid two-col" },
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Details")), el("div", { class: "card-body" }, kvList([
        ["Lead score", scoreEl(p.lead_score)],
        ["Website", link(p.website)],
        ["Contact", [p.contact_name, p.contact_title].filter(Boolean).join(", ") || null],
        ["Phone", p.phone],
        ["Email", p.email],
        ["Address", [p.address, p.city, p.state, p.zip_code].filter(Boolean).join(", ") || null],
        ["Lead source", p.lead_source],
        ["Last contacted", p.last_contacted_at ? fmt.datetime(p.last_contacted_at) : null],
        ["Next follow-up", p.next_follow_up_on],
        ["Added", fmt.datetime(p.created_at)],
      ]),
      p.observations ? el("div", { class: "section-gap" }, el("h3", {}, "Business observations"), el("p", { class: "pre" }, p.observations)) : null,
      p.opportunity ? el("div", { class: "section-gap" }, el("h3", {}, "Potential opportunity"), el("p", { class: "pre" }, p.opportunity)) : null,
      p.notes ? el("div", { class: "section-gap" }, el("h3", {}, "Notes"), el("p", { class: "pre" }, p.notes)) : null)),
      el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, "Activity timeline")), el("div", { class: "card-body" }, writer ? actForm : null, p.activities.length ? timeline : el("p", { class: "empty-inline" }, "No activity yet.")))),
  );
}

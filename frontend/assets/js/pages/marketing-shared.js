// Shared pieces for marketing pages: AI drafting task lists, compliance lists, email editor.
import { api } from "../api.js";
import { el, fmt, formDialog, isActiveStatus, modal, statusBadge, toast } from "../ui.js";

export function issueList(issues, emptyText = "No issues found.") {
  if (!issues?.length) return el("p", { class: "small score-high" }, `✓ ${emptyText}`);
  return el("ul", { class: "list-plain small" }, issues.map((i) => el("li", { class: i.level === "error" ? "text-danger" : "score-mid" }, `${i.level === "error" ? "✗" : "!"} ${i.message}`)));
}

// AI drafting tasks linked to a record, with deliverable import buttons.
export function aiTaskList(tasks, { onImport, importLabel = "Import" }) {
  if (!tasks.length) return el("p", { class: "empty-inline" }, "No AI drafts yet.");
  return el("ul", { class: "timeline" }, tasks.map((t) => el("li", {},
    el("div", { class: "row" }, el("strong", {}, t.agent?.name ?? "Agent"), statusBadge(t.phase), el("a", { href: `#/tasks/${t.id}`, class: "small" }, `task #${t.id}`), el("span", { class: "tl-meta ml-auto" }, fmt.relative(t.created_at))),
    t.error ? el("div", { class: "small text-danger" }, t.error) : null,
    t.deliverables?.length ? el("div", { class: "small muted mt-8" }, t.deliverables.map((d) => d.title).join(" · ")) : null,
    t.status === "COMPLETED" && onImport ? el("div", { class: "row mt-8" }, el("button", { class: "btn btn-sm", type: "button", onclick: () => onImport(t) }, importLabel)) : null)));
}

export const anyActive = (tasks) => tasks.some((t) => isActiveStatus(t.status));

// Email editor with a live compliance check.
export function openEmailEditor({ title, values = {}, extraDefs = [], save }) {
  const checkHost = el("div", { class: "mb-16" });
  const handle = formDialog({
    title,
    defs: [
      { name: "name", label: "Name", required: true },
      ...extraDefs,
      { name: "subject", label: "Subject", required: true, full: true },
      { name: "body_text", label: "Body", type: "textarea", max: "20000", hint: "Merge fields: {{first_name}} {{company_name}} {{city}} {{observation}} · Required: {{sender_name}} or {{agency_name}}, {{agency_address}}, {{unsubscribe_link}}" },
    ],
    values,
    intro: checkHost,
    submit: save,
  });
  const dialog = [...document.querySelectorAll(".modal")].pop();
  const subject = dialog?.querySelector("[id$='-subject']");
  const body = dialog?.querySelector("[id$='-body_text']");
  body?.classList.add("tall");
  let timer;
  const run = async () => {
    try {
      const r = await api("/emails/check", { method: "POST", body: { subject: subject.value, body_text: body.value } });
      checkHost.replaceChildren(el("h3", {}, "Compliance check"), issueList(r.issues, "Ready: sender, address and unsubscribe are present."));
    } catch {
      /* ignore while typing */
    }
  };
  [subject, body].forEach((n) => n?.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 400); }));
  run();
  return handle;
}

export function showText(title, text) {
  modal({ title, wide: true, body: el("div", { class: "pre" }, text) });
}

export async function importFlow(path, okMessage, after) {
  try {
    const r = await api(path, { method: "POST", body: {} });
    toast(okMessage(r), "success");
    after(r);
  } catch (err) {
    toast(err.message, "error");
  }
}

// Shared rendering for tasks: delegation tree and agent output. Text only (no innerHTML).
import { el, fmt, statusBadge } from "../ui.js";

export function taskTree(task, { linkRoot = true } = {}) {
  const row = (t) =>
    el(
      "div",
      { class: "tree-row" },
      el("span", { class: "tree-agent" }, t.agent?.name ?? "Unassigned"),
      statusBadge(t.phase ?? t.status),
      el("a", { href: `#/tasks/${t.id}`, class: "tree-title small" }, `#${t.id}`),
      t.attempts > 1 ? el("span", { class: "faint small" }, `attempt ${t.attempts}/${t.max_attempts}`) : null,
    );
  const children = (t) =>
    t.children?.length
      ? el("ul", { class: "tree" }, t.children.map((c) => el("li", {}, row(c), c.error ? el("div", { class: "small muted" }, c.error) : null, children(c))))
      : null;
  return el("div", {}, linkRoot ? row(task) : null, children(task));
}

function list(title, items) {
  if (!items?.length) return null;
  return el("div", { class: "section-gap" }, el("h3", {}, title), el("ul", { class: "list-plain" }, items.map((i) => el("li", {}, i))));
}

function deliverables(items) {
  if (!items?.length) return null;
  return el(
    "div",
    { class: "section-gap" },
    el("h3", {}, `Deliverables (${items.length})`),
    items.map((d) =>
      el(
        "div",
        { class: "deliverable" },
        el("div", { class: "deliverable-head" }, el("strong", {}, d.title), el("span", { class: "badge" }, fmt.label(d.kind))),
        el("div", { class: "deliverable-body pre" }, d.content),
      ),
    ),
  );
}

// Specialist result or CEO plan/summary, depending on what the task produced.
export function renderOutput(task) {
  const out = task.output ?? {};
  const parts = [];
  if (out.final) {
    parts.push(el("div", { class: "pre" }, out.final.summary));
    parts.push(list("Next steps", out.final.next_steps));
  }
  if (out.plan) {
    parts.push(el("div", { class: "section-gap" }, el("h3", {}, "CEO plan"), el("p", { class: "muted" }, out.plan.understanding), el("p", { class: "small faint" }, `Classified as: ${fmt.label(out.plan.task_type)}`)));
  }
  if (out.rejected_subtasks?.length) {
    parts.push(list("Sub-tasks the orchestrator refused", out.rejected_subtasks.map((r) => `${r.agent_key}: ${r.reason}`)));
  }
  const r = out.result;
  if (r) {
    parts.push(el("div", { class: "pre" }, r.summary));
    parts.push(deliverables(r.deliverables));
    parts.push(list("Recommendations", r.recommendations));
    parts.push(list("Proposed external actions (approval required)", r.proposed_actions?.map((a) => `${fmt.label(a.action_type)}: ${a.title} — ${a.details}`)));
    parts.push(list("Assumptions", r.assumptions));
    parts.push(list("Open questions", r.open_questions));
  }
  if (out.notes?.length) parts.push(list("Notes", out.notes));
  if (out.provider) parts.push(el("p", { class: "small faint section-gap" }, `Produced by ${out.provider} / ${out.model}`));
  const content = parts.filter(Boolean);
  return content.length ? el("div", {}, content) : el("p", { class: "empty-inline" }, task.status === "FAILED" ? "No output: the task failed." : "No output yet.");
}

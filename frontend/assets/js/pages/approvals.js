// Approvals: every external action an agent proposes waits here for a human decision.
import { api, showFormError } from "../api.js";
import { canAdmin, el, errorState, field, fmt, loadingState, modal, statusBadge, toast } from "../ui.js";

const STATUSES = ["PENDING", "APPROVED", "REJECTED", "EXPIRED"];

function decide(approval, decision, onDecided) {
  const error = el("div", { class: "form-error hidden", role: "alert" });
  const comments = el("textarea", { class: "input", id: "ap-comments", maxlength: "2000", placeholder: decision === "REJECTED" ? "Why is this rejected? (optional)" : "Optional note" });
  const confirm = el("button", { class: "btn btn-primary", type: "button" }, decision === "APPROVED" ? "Approve" : "Reject");
  const handle = modal({
    title: `${decision === "APPROVED" ? "Approve" : "Reject"}: ${approval.title}`,
    body: el(
      "div",
      {},
      error,
      el("p", { class: "notice mb-16" }, "No email, ads or publishing integration is connected yet, so approving records your decision and executes nothing."),
      field("Comments", comments),
    ),
    actions: [el("button", { class: "btn", type: "button", onclick: () => handle.close() }, "Cancel"), confirm],
  });
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    try {
      await api(`/approvals/${approval.id}/decision`, { method: "POST", body: { decision, comments: comments.value.trim() || null } });
      handle.close();
      toast(decision === "APPROVED" ? "Approved. Nothing was sent: no integration is connected." : "Rejected", "success");
      onDecided?.();
    } catch (err) {
      showFormError(error, err);
      confirm.disabled = false;
    }
  });
}

export function approvalCard(a, { canDecide, onDecided }) {
  return el(
    "div",
    { class: "deliverable" },
    el("div", { class: "deliverable-head" }, el("strong", {}, a.title), el("div", { class: "row" }, el("span", { class: "badge" }, fmt.label(a.action_type)), statusBadge(a.status))),
    el(
      "div",
      { class: "deliverable-body" },
      a.payload?.details ? el("p", { class: "pre" }, a.payload.details) : null,
      el("div", { class: "small faint" }, `Requested by ${a.requested_by_agent ?? "—"} · ${fmt.datetime(a.created_at)}`, a.task_id ? el("span", {}, " · ", el("a", { href: `#/tasks/${a.task_id}` }, `task #${a.task_id}`)) : null),
      a.status === "PENDING" && a.expires_at ? el("div", { class: "small faint" }, `Expires ${fmt.datetime(a.expires_at)}`) : null,
      a.decided_at ? el("div", { class: "small faint" }, `${fmt.label(a.status)}${a.decided_by ? ` by ${a.decided_by}` : ""} · ${fmt.datetime(a.decided_at)}${a.comments ? ` — ${a.comments}` : ""}`) : null,
      a.status === "PENDING" && canDecide
        ? el("div", { class: "row mt-8" }, el("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => decide(a, "APPROVED", onDecided) }, "Approve"), el("button", { class: "btn btn-sm", type: "button", onclick: () => decide(a, "REJECTED", onDecided) }, "Reject"))
        : null,
    ),
  );
}

export async function renderApprovals(ctx) {
  const { view, user } = ctx;
  const status = el("select", { class: "input", "aria-label": "Status" }, el("option", { value: "" }, "All"), STATUSES.map((s) => el("option", { value: s, selected: s === "PENDING" }, fmt.label(s))));
  const listEl = el("div", {});

  async function load() {
    listEl.replaceChildren(loadingState());
    try {
      const data = await api(`/approvals?page_size=100${status.value ? `&status=${status.value}` : ""}`);
      listEl.replaceChildren(
        ...(data.items.length
          ? data.items.map((a) => approvalCard(a, { canDecide: canAdmin(user), onDecided: load }))
          : [el("p", { class: "empty-inline" }, status.value === "PENDING" ? "Nothing is waiting for approval." : "No approvals match this filter.")]),
      );
    } catch (err) {
      listEl.replaceChildren(errorState(err, load));
    }
  }
  status.addEventListener("change", load);

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Approvals"), el("p", { class: "muted" }, "Agents cannot send, publish, launch or spend. They propose; you decide here."))),
    el("p", { class: "notice notice-warning mb-16" }, canAdmin(user) ? "External integrations are not connected yet, so an approval records the decision only. Nothing is sent or published." : "Only Founders and Admins can decide approvals."),
    el("section", { class: "card" }, el("div", { class: "card-body" }, el("div", { class: "toolbar" }, status), listEl)),
  );
  load();
}

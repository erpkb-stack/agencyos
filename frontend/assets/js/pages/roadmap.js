// Shown for modules whose phase has not shipped. Honest status, no fake controls.
import { el } from "../ui.js";

const PLANS = {
  ceo: ["Natural-language chat with the AI CEO", "Planning → delegation to specialised agents", "Persistent, multi-conversation history", "Live task status: planning, running, completed, failed, awaiting approval"],
  agents: ["Eleven default agents with editable prompts, models and temperature", "Activate / deactivate, test runs", "Execution history and assigned tasks per agent"],
  tasks: ["Task state machine with audit trail", "Bounded retries, timeouts, no circular delegation", "Cancel and retry from the UI"],
  prospects: ["Add, edit, soft-delete with confirmation", "Search, filter, sort, pagination", "CSV import/export with duplicate detection", "Activity timeline per prospect"],
  clients: ["Client records, contracts and fees", "Linked projects, deliverables, invoices and payments"],
  projects: ["Projects per client with PLANNED → COMPLETED workflow", "Tasks, dependencies, comments, deliverables, timeline"],
  approvals: ["Central queue of approval requests from agents and staff", "Approve / reject with comments; expiry", "No external action runs without a decision"],
  campaigns: ["Cold email, newsletters, Google and Meta ads", "AI-generated strategy, copy and audience suggestions", "Launch only after approval"],
  email: ["Templates, drafts and follow-up sequences", "Suppression list and unsubscribe handling", "Drafts only in v1 — nothing is sent"],
  funnels: ["Industry templates: HVAC, plumbing, roofing, accounting, real estate", "AI landing-page drafts with local preview", "Publishing requires approval"],
  finance: ["Revenue, expenses, invoices and payments", "MRR, gross profit, outstanding balances, client profitability", "Monthly financial reports"],
  analytics: ["Daily, weekly and monthly reports", "Pipeline, campaign, project and agent performance", "CSV export"],
};

export function renderRoadmap({ view }, module) {
  const items = PLANS[module.id] ?? [];
  view.replaceChildren(
    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "state" },
        el("div", { class: "state-icon", "aria-hidden": "true" }, module.icon),
        el("h2", {}, `${module.label} is not built yet`),
        el("p", {}, `This module is scheduled for Phase ${module.phase}. Nothing on this page is functional until then.`),
        items.length ? el("ul", { class: "roadmap-list" }, items.map((t) => el("li", {}, t))) : null,
        el("p", { class: "mt-16" }, el("a", { href: "#/dashboard" }, "← Back to dashboard")),
      ),
    ),
  );
}

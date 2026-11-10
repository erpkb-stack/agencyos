// Founder dashboard. Every value comes from GET /dashboard/summary.
import { api } from "../api.js";
import { el, errorState, fmt, loadingState, statusBadge } from "../ui.js";

const KPI_CARDS = [
  { key: "total_revenue", label: "Total revenue", money: true, accent: true },
  { key: "monthly_recurring_revenue", label: "Monthly recurring revenue", money: true, accent: true, note: "Active client retainers" },
  { key: "total_clients", label: "Total clients" },
  { key: "active_clients", label: "Active clients" },
  { key: "total_prospects", label: "Total prospects" },
  { key: "qualified_leads", label: "Qualified leads", note: "Qualified stage or later" },
  { key: "discovery_meetings", label: "Discovery meetings", note: "Meeting scheduled or later" },
  { key: "proposals_sent", label: "Proposals sent" },
  { key: "closed_deals", label: "Closed deals", note: "Won" },
  { key: "active_projects", label: "Active projects" },
  { key: "pending_approvals", label: "Pending approvals" },
];

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartCard(title, subtitle, wide = false) {
  const canvas = el("canvas", { role: "img", "aria-label": title });
  const box = el("div", { class: "chart-box" }, canvas);
  const card = el(
    "section",
    { class: `card${wide ? " wide" : ""}` },
    el("div", { class: "card-head" }, el("div", {}, el("h2", {}, title), subtitle ? el("div", { class: "faint" }, subtitle) : null)),
    el("div", { class: "card-body" }, box),
  );
  return { card, canvas, box };
}

function emptyOverlay(box, text) {
  box.append(el("div", { class: "chart-empty" }, text));
}

function baseOptions(extra = {}) {
  const grid = cssVar("--border");
  const text = cssVar("--text-muted");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: { legend: { labels: { color: text, boxWidth: 12 } } },
    scales: {
      x: { grid: { display: false }, ticks: { color: text } },
      y: { beginAtZero: true, grid: { color: grid }, ticks: { color: text, precision: 0 } },
    },
    ...extra,
  };
}

function feedCard(title, items, renderItem, emptyText) {
  return el(
    "section",
    { class: "card" },
    el("div", { class: "card-head" }, el("h2", {}, title), el("span", { class: "badge" }, String(items.length))),
    el(
      "div",
      { class: "card-body" },
      items.length ? el("ul", { class: "feed" }, items.map(renderItem)) : el("div", { class: "feed-empty" }, emptyText),
    ),
  );
}

function feedItem(title, sub, right) {
  return el(
    "li",
    {},
    el("div", { class: "feed-main" }, el("div", { class: "feed-title" }, title), el("div", { class: "feed-sub" }, sub)),
    right,
  );
}

function emptyBanner() {
  return el(
    "div",
    { class: "banner" },
    el("div", { class: "state-icon", "aria-hidden": "true" }, "🚀"),
    el(
      "div",
      {},
      el("h2", {}, "No business data yet"),
      el("p", {}, "Every number on this dashboard is calculated from the database, so it starts at zero. It fills in as you work:"),
      el(
        "ol",
        {},
        el("li", {}, "Now — ", el("a", { href: "#/ceo" }, "ask the AI CEO"), " for research and plans; agent runs appear under Agent task performance."),
        el("li", {}, "Now — ", el("a", { href: "#/prospects" }, "add prospects"), " (manually or by CSV) and clients to populate the pipeline and conversion charts."),
        el("li", {}, "Now — ", el("a", { href: "#/finance/invoices" }, "invoice clients and record payments"), " to populate the revenue chart."),
      ),
    ),
  );
}

function drawCharts(charts, refs) {
  const primary = cssVar("--primary");
  const success = cssVar("--success");
  const danger = cssVar("--danger");
  const instances = [];
  const make = (canvas, config) => instances.push(new window.Chart(canvas, config));

  const rev = charts.monthly_revenue;
  const revValues = rev.values.map(Number);
  make(refs.revenue.canvas, {
    type: "bar",
    data: { labels: rev.labels, datasets: [{ label: "Revenue (USD)", data: revValues, backgroundColor: primary, borderRadius: 4 }] },
    options: baseOptions({ plugins: { legend: { display: false } } }),
  });
  if (revValues.every((v) => v === 0)) emptyOverlay(refs.revenue.box, "No revenue recorded in the last 12 months.");

  const pipe = charts.pipeline;
  make(refs.pipeline.canvas, {
    type: "bar",
    data: {
      labels: pipe.labels.map(fmt.label),
      datasets: [{ label: "Prospects", data: pipe.values, backgroundColor: pipe.labels.map((l) => (l === "LOST" ? danger : l === "WON" ? success : primary)), borderRadius: 4 }],
    },
    options: baseOptions({ indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { precision: 0, color: cssVar("--text-muted") }, grid: { color: cssVar("--border") } }, y: { grid: { display: false }, ticks: { color: cssVar("--text-muted") } } } }),
  });
  if (pipe.values.every((v) => v === 0)) emptyOverlay(refs.pipeline.box, "No prospects in the pipeline.");

  const conv = charts.lead_conversion;
  make(refs.conversion.canvas, {
    type: "line",
    data: { labels: conv.labels, datasets: [{ label: "Reached stage", data: conv.values, borderColor: primary, backgroundColor: primary + "33", fill: true, tension: 0.25 }] },
    options: baseOptions({ plugins: { legend: { display: false } } }),
  });
  if (conv.values[0] === 0) emptyOverlay(refs.conversion.box, "Conversion appears once prospects exist.");

  const proj = charts.project_completion;
  make(refs.projects.canvas, {
    type: "doughnut",
    data: { labels: proj.labels.map(fmt.label), datasets: [{ data: proj.values, backgroundColor: ["#94a3b8", primary, "#f59e0b", "#a855f7", success, danger] }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right", labels: { color: cssVar("--text-muted"), boxWidth: 12 } } } },
  });
  if (proj.values.every((v) => v === 0)) emptyOverlay(refs.projects.box, "No projects yet.");

  const ag = charts.agent_performance;
  make(refs.agents.canvas, {
    type: "bar",
    data: {
      labels: ag.labels,
      datasets: [
        { label: "Succeeded", data: ag.succeeded, backgroundColor: success, borderRadius: 4 },
        { label: "Failed", data: ag.failed, backgroundColor: danger, borderRadius: 4 },
      ],
    },
    options: baseOptions({ scales: { x: { stacked: true, grid: { display: false }, ticks: { color: cssVar("--text-muted") } }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0, color: cssVar("--text-muted") }, grid: { color: cssVar("--border") } } } }),
  });
  if (!ag.labels.length) emptyOverlay(refs.agents.box, "No agent executions yet.");

  return () => instances.forEach((c) => c.destroy());
}

export async function renderDashboard({ view }) {
  let destroyCharts = null;

  async function load() {
    if (destroyCharts) destroyCharts();
    view.replaceChildren(loadingState("Loading dashboard…"));
    let data;
    try {
      data = await api("/dashboard/summary");
    } catch (error) {
      view.replaceChildren(errorState(error, load));
      return;
    }

    const refreshBtn = el("button", { class: "btn btn-sm", type: "button", onclick: load }, "↻ Refresh");
    const head = el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", {}, "Founder dashboard"), el("p", { class: "muted" }, `Live from the database · updated ${new Date().toLocaleTimeString()}`)),
      refreshBtn,
    );

    const kpiGrid = el(
      "div",
      { class: "grid kpi-grid" },
      KPI_CARDS.map((k) =>
        el(
          "div",
          { class: `card kpi${k.accent ? " kpi-accent" : ""}` },
          el("div", { class: "kpi-label" }, k.label),
          el("div", { class: "kpi-value" }, k.money ? fmt.money(data.kpis[k.key]) : fmt.int(data.kpis[k.key])),
          k.note ? el("div", { class: "kpi-note" }, k.note) : null,
        ),
      ),
    );

    const avg = data.charts.project_completion.average_completion;
    const refs = {
      revenue: chartCard("Monthly revenue", "Last 12 months, cash received", true),
      pipeline: chartCard("Sales pipeline", "Prospects by stage"),
      conversion: chartCard("Lead conversion", "Prospects that reached each milestone"),
      projects: chartCard("Project completion", avg === null ? "Projects by status" : `Projects by status · average ${avg}% complete`),
      agents: chartCard("Agent task performance", "Executions by outcome"),
    };
    const chartGrid = el("div", { class: "grid chart-grid" }, Object.values(refs).map((r) => r.card));

    const a = data.activity;
    const activityGrid = el(
      "div",
      { class: "grid activity-grid" },
      feedCard("Recent actions", a.ceo_actions, (x) => feedItem(x.summary, `${x.actor ?? "System"} · ${fmt.relative(x.at)}`), "No actions recorded yet."),
      feedCard("Agent executions", a.agent_executions, (x) => feedItem(x.agent, `Task #${x.task_id} · ${fmt.relative(x.at)}`, statusBadge(x.status)), "No agent has run yet."),
      feedCard("Pending approvals", a.pending_approvals, (x) => feedItem(x.title, `${fmt.label(x.action_type)} · ${fmt.relative(x.at)}`, statusBadge("PENDING")), "Nothing waiting for your decision."),
      feedCard("New prospects", a.new_prospects, (x) => feedItem(x.company_name, [x.industry, x.city, fmt.relative(x.at)].filter(Boolean).join(" · "), statusBadge(x.stage)), "No prospects yet."),
      feedCard("New clients", a.new_clients, (x) => feedItem(x.company_name, fmt.relative(x.at), statusBadge(x.contract_status)), "No clients yet."),
      feedCard("Failed tasks", a.failed_tasks, (x) => feedItem(x.title, x.error ?? fmt.relative(x.at), statusBadge("FAILED")), "No failed tasks."),
    );

    view.replaceChildren(
      head,
      data.is_empty ? emptyBanner() : "",
      kpiGrid,
      chartGrid,
      el("h2", { class: "mb-16" }, "Recent activity"),
      activityGrid,
    );
    destroyCharts = window.Chart ? drawCharts(data.charts, refs) : null;
    if (!window.Chart) Object.values(refs).forEach((r) => emptyOverlay(r.box, "Chart library failed to load."));
  }

  await load();
  return () => destroyCharts && destroyCharts();
}

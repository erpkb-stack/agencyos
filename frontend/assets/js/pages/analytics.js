// Analytics: database-backed reports with a period picker, CSV export and an optional AI summary.
import { api } from "../api.js";
import { canWrite, el, errorState, loadingState, poll, toast } from "../ui.js";
import { aiTaskList, anyActive, showText } from "./marketing-shared.js";

export async function renderAnalytics(ctx) {
  const { view, user } = ctx;
  let stop = null;
  view.replaceChildren(loadingState());
  let reports;
  try {
    reports = (await api("/analytics/reports")).items;
  } catch (err) {
    view.replaceChildren(errorState(err, () => renderAnalytics(ctx)));
    return;
  }
  const current = reports.find((r) => r.key === ctx.params[0]) ?? reports[0];
  const select = el("select", { class: "input", "aria-label": "Report" }, reports.map((r) => el("option", { value: r.key, selected: r.key === current.key }, r.title)));
  const start = el("input", { class: "input", type: "date", value: current.default_period.start, "aria-label": "Start date" });
  const end = el("input", { class: "input", type: "date", value: current.default_period.end, "aria-label": "End date" });
  const csv = el("a", { class: "btn", download: "" }, "Export CSV");
  const summarize = canWrite(user) ? el("button", { class: "btn", type: "button" }, "Summarise with AI") : null;
  const body = el("div", {});
  const aiHost = el("div", {});

  async function load() {
    const q = `start=${start.value}&end=${end.value}`;
    csv.href = `/api/v1/analytics/reports/${current.key}/csv?${q}`;
    body.replaceChildren(loadingState());
    try {
      const r = await api(`/analytics/reports/${current.key}?${q}`);
      body.replaceChildren(
        el("div", { class: "metric-grid section-gap" }, r.kpis.map((k) => el("div", { class: "metric" }, el("div", { class: "kpi-label" }, k.label), el("div", { class: "kpi-value" }, String(k.value))))),
        ...r.sections.map((s) => el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, s.title)),
          s.note ? el("p", { class: "small faint card-pad" }, s.note) : null,
          s.rows.length ? el("div", { class: "table-wrap" }, el("table", { class: "table" }, el("thead", {}, el("tr", {}, s.columns.map((c) => el("th", {}, c)))), el("tbody", {}, s.rows.map((row) => el("tr", {}, row.map((cell) => el("td", { class: String(cell).length > 40 ? "wrap" : null }, String(cell)))))))) : el("div", { class: "card-body empty-inline" }, "No data for this period."))),
        el("p", { class: "small faint section-gap" }, `Generated ${new Date(r.generated_at).toLocaleString()} from AgencyOS records only.`),
      );
    } catch (err) {
      body.replaceChildren(errorState(err, load));
    }
  }

  async function refreshAI() {
    const { items } = await api("/analytics/summaries");
    aiHost.replaceChildren(aiTaskList(items, { importLabel: "Read summary", onImport: async (t) => {
      const task = await api(`/tasks/${t.id}`);
      const res = task.output?.result;
      showText(t.title ?? "AI summary", res ? [res.summary, ...res.deliverables.map((d) => `\n${d.title}\n${d.content}`), res.recommendations.length ? `\nRecommendations:\n- ${res.recommendations.join("\n- ")}` : ""].join("\n") : "No output.");
    } }));
    return anyActive(items);
  }

  select.addEventListener("change", () => { window.location.hash = `#/analytics/${select.value}`; });
  [start, end].forEach((i) => i.addEventListener("change", load));
  summarize?.addEventListener("click", async () => {
    try {
      await api(`/analytics/reports/${current.key}/summarize?start=${start.value}&end=${end.value}`, { method: "POST" });
      toast("Analytics Agent is summarising this report", "success");
      stop?.(); stop = poll(refreshAI, 1500);
      refreshAI();
    } catch (e) { toast(e.message, "error"); }
  });

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Analytics"), el("p", { class: "muted" }, "Reports calculated from your AgencyOS records. Export any report to CSV."))),
    el("section", { class: "card" }, el("div", { class: "card-body" }, el("div", { class: "toolbar" }, select, start, end, csv, summarize))),
    body,
    el("section", { class: "card section-gap" }, el("div", { class: "card-head" }, el("h2", {}, "AI summaries")), el("div", { class: "card-body" }, el("p", { class: "small faint" }, "The Analytics Agent sees only the report data and drafts a summary; it changes nothing."), aiHost)),
  );
  await load();
  if (await refreshAI()) { stop = poll(refreshAI, 1500); }
  return () => stop?.();
}

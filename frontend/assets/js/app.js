// App shell: session check, sidebar, hash router.
import { api } from "./api.js";
import { el, errorState, fmt, toast } from "./ui.js";
import { renderDashboard } from "./pages/dashboard.js";
import { renderSettings } from "./pages/settings.js";
import { renderRoadmap } from "./pages/roadmap.js";
import { renderCeo } from "./pages/ceo.js";
import { renderAgents } from "./pages/agents.js";
import { renderTasks } from "./pages/tasks.js";
import { renderApprovals } from "./pages/approvals.js";
import { renderProspects } from "./pages/prospects.js";
import { renderClients } from "./pages/clients.js";
import { renderProjects } from "./pages/projects.js";
import { renderCampaigns } from "./pages/campaigns.js";
import { renderEmail } from "./pages/email.js";
import { renderFunnels } from "./pages/funnels.js";
import { renderFinance } from "./pages/finance.js";
import { renderAnalytics } from "./pages/analytics.js";

// `phase` > 1 means the module is not built yet; its route shows the roadmap page.
// Built modules use phase 1 so no "P" badge is shown.
export const MODULES = [
  { id: "dashboard", label: "Dashboard", icon: "▦", phase: 1, render: renderDashboard },
  { id: "ceo", label: "AI CEO", icon: "✦", phase: 1, render: renderCeo },
  { id: "agents", label: "Agents", icon: "◎", phase: 1, render: renderAgents },
  { id: "tasks", label: "Tasks", icon: "☰", phase: 1, render: renderTasks },
  { section: "Business" },
  { id: "prospects", label: "Prospects", icon: "◇", phase: 1, render: renderProspects },
  { id: "clients", label: "Clients", icon: "◆", phase: 1, render: renderClients },
  { id: "projects", label: "Projects", icon: "▤", phase: 1, render: renderProjects },
  { section: "Marketing" },
  { id: "campaigns", label: "Campaigns", icon: "➚", phase: 1, render: renderCampaigns },
  { id: "email", label: "Email Marketing", icon: "✉", phase: 1, render: renderEmail },
  { id: "funnels", label: "Funnels", icon: "⏷", phase: 1, render: renderFunnels },
  { section: "Operations" },
  { id: "finance", label: "Finance", icon: "$", phase: 1, render: renderFinance },
  { id: "analytics", label: "Analytics", icon: "◔", phase: 1, render: renderAnalytics },
  { id: "approvals", label: "Approvals", icon: "✓", phase: 1, render: renderApprovals },
  { id: "settings", label: "Settings", icon: "⚙", phase: 1, render: renderSettings },
];

const state = { user: null, system: null, cleanup: null };

function buildNav() {
  const nav = document.getElementById("nav");
  nav.replaceChildren();
  for (const item of MODULES) {
    if (item.section) {
      nav.append(el("div", { class: "nav-section" }, item.section));
      continue;
    }
    nav.append(
      el(
        "a",
        { href: `#/${item.id}`, dataset: { route: item.id } },
        el("span", { class: "nav-icon", "aria-hidden": "true" }, item.icon),
        item.label,
        item.phase > 1 ? el("span", { class: "nav-phase", title: `Scheduled for Phase ${item.phase}` }, `P${item.phase}`) : null,
      ),
    );
  }
}

function routeParts() {
  return window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
}

function currentRoute() {
  const id = routeParts()[0] || "dashboard";
  return MODULES.find((m) => m.id === id) ?? MODULES[0];
}

async function route() {
  const module = currentRoute();
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === module.id));
  document.getElementById("page-title").textContent = module.label;
  document.title = `${module.label} · AgencyOS`;
  document.getElementById("shell").classList.remove("nav-open");

  if (typeof state.cleanup === "function") state.cleanup();
  state.cleanup = null;

  const view = document.getElementById("view");
  const ctx = { user: state.user, system: state.system, view, params: routeParts().slice(1) };
  try {
    const render = module.render ?? renderRoadmap;
    state.cleanup = (await render(ctx, module)) ?? null;
  } catch (error) {
    view.replaceChildren(errorState(error, route));
  }
  view.focus({ preventScroll: true });
}

function renderUser() {
  const { user, system } = state;
  document.getElementById("user-name").textContent = user.full_name;
  document.getElementById("user-role").textContent = fmt.label(user.role);
  document.getElementById("user-avatar").textContent = user.full_name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  const env = document.getElementById("env-badge");
  env.textContent = system.environment;
  env.className = `badge env-badge ${system.environment === "production" ? "badge-success" : "badge-warning"}`;
  document.getElementById("sidebar-foot").textContent = `v${system.version} · LLM: ${system.llm.provider}`;
}

async function boot() {
  try {
    [state.user, state.system] = await Promise.all([api("/auth/me"), api("/settings/system")]);
  } catch (error) {
    if (error.status !== 401) document.getElementById("view").replaceChildren(errorState(error, boot));
    return;
  }
  buildNav();
  renderUser();

  document.getElementById("logout").addEventListener("click", async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch (error) {
      toast(error.message, "error");
    }
    window.location.replace("/login");
  });
  document.getElementById("menu-toggle").addEventListener("click", () => {
    document.getElementById("shell").classList.toggle("nav-open");
  });
  window.addEventListener("hashchange", route);
  route();
}

boot();

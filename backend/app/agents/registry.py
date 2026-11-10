"""Built-in agent definitions: the code-side half of each agent.

The database row holds what the Founder may edit (prompt, model, temperature, active flag).
This module holds what they may not: the agent kind, its schemas and its allowed tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CEO_KEY = "ceo"

# Tools are permissions checked by the orchestrator, not functions the model can call.
TOOL_DELEGATE = "delegate"  # may create sub-tasks for other agents (CEO only)
TOOL_DRAFT = "draft_content"  # may return drafts and reports
TOOL_BUSINESS_DATA = "read_business_data"  # receives a read-only snapshot of agency metrics
TOOL_PROPOSE = "propose_external_action"  # proposals become approval requests

ALL_TOOLS = [TOOL_DELEGATE, TOOL_DRAFT, TOOL_BUSINESS_DATA, TOOL_PROPOSE]
SPECIALIST_TOOLS = [TOOL_DRAFT, TOOL_BUSINESS_DATA, TOOL_PROPOSE]


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    department: str
    description: str
    system_prompt: str
    capabilities: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=lambda: [TOOL_DRAFT])
    keywords: tuple[str, ...] = ()  # used only by the offline mock provider for routing


def _prompt(role: str, focus: str) -> str:
    return (
        f"You are the {role} of a small AI-assisted marketing and automation agency that serves "
        f"local service businesses (HVAC, plumbing, roofing, accounting, real estate).\n\n"
        f"{focus}\n\n"
        "Be concrete and practical. Prefer short sections and bullet points. State assumptions "
        "explicitly and list open questions instead of guessing."
    )


BUILT_IN_AGENTS: list[AgentSpec] = [
    AgentSpec(
        key=CEO_KEY,
        name="AI CEO",
        department="Executive",
        description="Understands the Founder's request, plans the work and delegates it to specialists.",
        system_prompt=_prompt(
            "AI CEO",
            "Your job is to understand the Founder's request, decide which specialist agents "
            "should do the work, and write clear, self-contained instructions for each one. "
            "Delegate only to agents that are listed as available. Use as few agents as the "
            "request needs. If the request is a simple question you can answer from the "
            "conversation, answer it directly in `reply` and return no subtasks.",
        ),
        capabilities=["planning", "delegation", "summarisation"],
        allowed_tools=[TOOL_DELEGATE, TOOL_BUSINESS_DATA],
    ),
    AgentSpec(
        key="business_strategy",
        name="Business Strategy Agent",
        department="Strategy",
        description="Market positioning, niche selection, offers, growth plans and competitor analysis.",
        system_prompt=_prompt(
            "Business Strategy lead",
            "You produce market and growth analysis: target segments, positioning, service "
            "offers and pricing hypotheses, and step-by-step plans. Base claims about specific "
            "businesses only on data you are given.",
        ),
        capabilities=["market analysis", "positioning", "offer design", "growth planning"],
        keywords=("strategy", "plan", "growth", "positioning", "market", "competitor", "niche",
                  "identify businesses", "opportunit", "research the target"),
    ),
    AgentSpec(
        key="sales",
        name="Sales Agent",
        department="Sales",
        description="Prospect research reports from supplied data, qualification, proposals and follow-ups.",
        system_prompt=_prompt(
            "Sales lead",
            "You qualify prospects, write prospect research reports from the data you are "
            "given, and draft proposals and follow-up plans. Never invent business names, "
            "people, phone numbers, emails or addresses; if data is missing, say what to "
            "collect and where.",
        ),
        capabilities=["prospect research", "lead qualification", "proposals", "follow-up plans"],
        allowed_tools=[TOOL_DRAFT, TOOL_PROPOSE],
        keywords=("prospect", "lead", "proposal", "sales", "pitch", "deal", "pipeline", "find ",
                  "companies", "qualif"),
    ),
    AgentSpec(
        key="marketing",
        name="Marketing Agent",
        department="Marketing",
        description="Campaign strategy and drafts, audience recommendations, content plans.",
        system_prompt=_prompt(
            "Marketing lead",
            "You design campaigns: objectives, audience, channels, messaging, content calendar "
            "and success metrics. You produce drafts only; launching anything requires approval.",
        ),
        capabilities=["campaign strategy", "audience recommendations", "content planning"],
        allowed_tools=[TOOL_DRAFT, TOOL_PROPOSE],
        keywords=("campaign", "marketing", "brand", "content", "social", "audience", "launch"),
    ),
    AgentSpec(
        key="funnel",
        name="Funnel Agent",
        department="Marketing",
        description="Landing pages, lead-capture forms, calls to action and follow-up workflows.",
        system_prompt=_prompt(
            "Funnel and landing-page specialist",
            "You design conversion funnels: page structure, headline and copy, call to action, "
            "lead-capture fields and the follow-up workflow. Publishing requires approval.",
        ),
        capabilities=["landing pages", "lead capture", "conversion optimisation"],
        allowed_tools=[TOOL_DRAFT, TOOL_PROPOSE],
        keywords=("funnel", "landing", "website", "conversion", "form", "page"),
    ),
    AgentSpec(
        key="email_marketing",
        name="Email Marketing Agent",
        department="Marketing",
        description="Email copy, subject lines, follow-up sequences and segmentation. Drafts only.",
        system_prompt=_prompt(
            "Email marketing specialist",
            "You write email drafts, subject lines and follow-up sequences. Every marketing "
            "email must identify the sender, include a working unsubscribe line and respect "
            "suppression lists and consent. Use merge fields like {{first_name}} instead of "
            "real recipient data. You never send email; sending requires approval.",
        ),
        capabilities=["email copy", "subject lines", "sequences", "segmentation"],
        allowed_tools=[TOOL_DRAFT, TOOL_PROPOSE],
        keywords=("email", "newsletter", "sequence", "subject line", "outreach", "cold"),
    ),
    AgentSpec(
        key="advertising",
        name="Advertising Agent",
        department="Marketing",
        description="Google and Meta ad copy, targeting suggestions and budget recommendations.",
        system_prompt=_prompt(
            "Paid advertising specialist",
            "You write ad copy and recommend targeting, structure and budgets for Google and "
            "Meta ads. Launching ads or changing budgets requires approval.",
        ),
        capabilities=["ad copy", "targeting", "budget planning"],
        allowed_tools=[TOOL_DRAFT, TOOL_PROPOSE],
        keywords=("ads", "ad copy", "google ads", "meta", "facebook", "ppc", "advertis"),
    ),
    AgentSpec(
        key="automation",
        name="Automation Agent",
        department="Operations",
        description="Workflow and CRM automation designs, integrations and process documentation.",
        system_prompt=_prompt(
            "Automation specialist",
            "You design business automations: triggers, steps, tools, data passed between "
            "them, failure handling and how to test them. You describe; you do not connect "
            "to live systems.",
        ),
        capabilities=["workflow design", "CRM automation", "integration planning"],
        keywords=("automat", "workflow", "zapier", "integration", "crm", "process"),
    ),
    AgentSpec(
        key="client_success",
        name="Client Success Agent",
        department="Delivery",
        description="Client onboarding, project status summaries, retention and satisfaction.",
        system_prompt=_prompt(
            "Client Success manager",
            "You plan onboarding, summarise project and client status from the data you are "
            "given, and flag retention risks. Report only figures present in the data.",
        ),
        capabilities=["onboarding", "status reporting", "retention"],
        allowed_tools=[TOOL_DRAFT, TOOL_BUSINESS_DATA],
        keywords=("client", "onboard", "retention", "churn", "project", "status", "satisf"),
    ),
    AgentSpec(
        key="finance",
        name="Finance Agent",
        department="Finance",
        description="Financial position, revenue and expense analysis, pricing and budgets.",
        system_prompt=_prompt(
            "Finance lead",
            "You analyse the agency's financial position from the data you are given: revenue, "
            "recurring revenue, expenses, receivables and pricing. Report only figures present "
            "in the data and say clearly when data is missing. Spending money or issuing "
            "refunds requires approval.",
        ),
        capabilities=["financial reporting", "pricing", "budgeting"],
        allowed_tools=[TOOL_DRAFT, TOOL_BUSINESS_DATA, TOOL_PROPOSE],
        keywords=("financ", "revenue", "invoice", "expense", "profit", "cash", "budget", "pricing",
                  "money"),
    ),
    AgentSpec(
        key="analytics",
        name="Analytics Agent",
        department="Analytics",
        description="KPIs, performance reports, conversion analysis and recommendations.",
        system_prompt=_prompt(
            "Analytics lead",
            "You turn the data you are given into KPIs, trends and recommendations. Report "
            "only figures present in the data; never fabricate metrics.",
        ),
        capabilities=["KPI reporting", "performance analysis", "conversion analysis"],
        allowed_tools=[TOOL_DRAFT, TOOL_BUSINESS_DATA],
        keywords=("analytic", "report", "metric", "kpi", "performance", "dashboard", "analy"),
    ),
]

SPECS_BY_KEY: dict[str, AgentSpec] = {s.key: s for s in BUILT_IN_AGENTS}


def is_built_in(key: str) -> bool:
    return key in SPECS_BY_KEY

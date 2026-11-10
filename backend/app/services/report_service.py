"""Reports built only from database records. Every report has the same shape so the UI can render
it and CSV export works without special cases:

{"key", "title", "period": {"start", "end"}, "kpis": [{"label", "value"}],
 "sections": [{"title", "columns": [...], "rows": [[...], ...], "note"?}]}
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, UTC
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.ai import Agent, AgentExecution, AgentTask
from app.models.approvals import Approval
from app.models.crm import Client, Prospect, ProspectActivity
from app.models.delivery import Project, ProjectTask
from app.models.enums import (
    ACTIVE_PROJECT_STATUSES, ApprovalStatus, ExecutionStatus, InvoiceStatus, ProjectTaskStatus, SALES_PIPELINE, SalesStage,
    TaskStatus, stages_from,
)
from app.models.finance import Invoice
from app.models.identity import AuditLog
from app.models.marketing import Campaign
from app.services import finance_service

REPORTS = {
    "daily_activity": "Daily activity summary",
    "weekly_ceo": "Weekly CEO report",
    "monthly_business": "Monthly business report",
    "prospect_performance": "Prospect performance",
    "sales_conversion": "Sales conversion",
    "campaign_performance": "Campaign performance",
    "project_progress": "Client project progress",
    "financial_summary": "Financial summary",
    "agent_performance": "Agent performance",
}


def _dt(d: date, end: bool = False) -> datetime:
    """Local-midnight boundary (start of ``d``, or start of the next day when ``end``)."""
    return finance_service.day_start(d + timedelta(days=1) if end else d)


def default_period(key: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if key == "daily_activity":
        return today, today
    if key == "weekly_ceo":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if key in ("monthly_business", "financial_summary"):
        return today.replace(day=1), today
    return today - timedelta(days=89), today


def _section(title: str, columns: list[str], rows: list[list[Any]], note: str | None = None) -> dict[str, Any]:
    return {"title": title, "columns": columns, "rows": rows, **({"note": note} if note else {})}


def _kpi(label: str, value: Any) -> dict[str, Any]:
    return {"label": label, "value": value}


def _pct(a: int | Decimal, b: int | Decimal) -> str:
    return f"{round(float(a) * 100 / float(b), 1)}%" if b else "—"


# -- building blocks ---------------------------------------------------------------------------


def _activity_counts(db: Session, start: date, end: date) -> Counter:
    return Counter(dict(db.execute(select(AuditLog.action, func.count()).where(
        AuditLog.created_at >= _dt(start), AuditLog.created_at < _dt(end, True)).group_by(AuditLog.action)).all()))


def _task_stats(db: Session, start: date, end: date) -> dict[str, int]:
    rows = dict(db.execute(select(AgentTask.status, func.count()).where(
        AgentTask.created_at >= _dt(start), AgentTask.created_at < _dt(end, True)).group_by(AgentTask.status)).all())
    return {s.value: rows.get(s, 0) for s in TaskStatus}


def _stage_moves(db: Session, start: date, end: date) -> list[list[Any]]:
    rows = db.scalars(select(ProspectActivity.description).where(
        ProspectActivity.activity_type == "stage_change", ProspectActivity.created_at >= _dt(start),
        ProspectActivity.created_at < _dt(end, True)))
    moves = Counter(d.split("Stage ", 1)[-1] for d in rows)
    return [[k, v] for k, v in moves.most_common()]


def _agent_rows(db: Session, start: date, end: date) -> list[list[Any]]:
    rows = db.execute(
        select(Agent.name, func.count(),
               func.sum(case((AgentExecution.status == ExecutionStatus.SUCCEEDED, 1), else_=0)),
               func.avg(AgentExecution.duration_ms),
               func.sum(func.coalesce(AgentExecution.prompt_tokens, 0) + func.coalesce(AgentExecution.completion_tokens, 0)),
               func.sum(AgentExecution.estimated_cost))
        .join(Agent, Agent.id == AgentExecution.agent_id)
        .where(AgentExecution.started_at >= _dt(start), AgentExecution.started_at < _dt(end, True))
        .group_by(Agent.id, Agent.name).order_by(func.count().desc())).all()
    return [[name, n, int(ok or 0), _pct(int(ok or 0), n), int(avg or 0), int(tokens or 0), f"{Decimal(str(cost or 0)):.4f}"]
            for name, n, ok, avg, tokens, cost in rows]


AGENT_COLUMNS = ["Agent", "Runs", "Succeeded", "Success rate", "Avg duration (ms)", "Tokens", "Est. cost (USD)"]


def _finance_kpis(s: dict[str, Any]) -> list[dict[str, Any]]:
    return [_kpi("Revenue", s["total_revenue"]), _kpi("Expenses", s["total_expenses"]),
            _kpi("Gross profit", s["gross_profit"]), _kpi("Margin", f"{s['margin_percent']}%" if s["margin_percent"] is not None else "—"),
            _kpi("MRR", s["monthly_recurring_revenue"]), _kpi("Outstanding", s["outstanding"]),
            _kpi("Overdue", f"{s['overdue']} ({s['overdue_invoices']} invoices)")]


# -- reports -----------------------------------------------------------------------------------


def daily_activity(db: Session, start: date, end: date) -> dict[str, Any]:
    counts = _activity_counts(db, start, end)
    tasks = _task_stats(db, start, end)
    new_prospects = db.scalar(select(func.count()).select_from(Prospect).where(
        Prospect.created_at >= _dt(start), Prospect.created_at < _dt(end, True))) or 0
    events = db.execute(select(AuditLog.created_at, AuditLog.actor_label, AuditLog.summary).where(
        AuditLog.created_at >= _dt(start), AuditLog.created_at < _dt(end, True),
        ~AuditLog.action.startswith("auth.")).order_by(AuditLog.id.desc()).limit(100)).all()
    return {
        "kpis": [_kpi("New prospects", new_prospects), _kpi("AI tasks created", sum(tasks.values())),
                 _kpi("AI tasks failed", tasks["FAILED"]), _kpi("Approvals decided",
                 counts["approval.approved"] + counts["approval.rejected"]),
                 _kpi("Payments recorded", counts["finance.payment_recorded"])],
        "sections": [
            _section("Activity by type", ["Action", "Count"],
                     [[a, n] for a, n in sorted(counts.items(), key=lambda x: -x[1]) if not a.startswith("auth.")]),
            _section("Timeline", ["Time (UTC)", "Who", "What"],
                     [[t.isoformat(timespec="minutes") if t else "", who or "System", s or ""] for t, who, s in events]),
        ],
    }


def weekly_ceo(db: Session, start: date, end: date) -> dict[str, Any]:
    prev_start, prev_end = start - timedelta(days=7), start - timedelta(days=1)
    fin, prev_fin = finance_service.summary(db, start, end), finance_service.summary(db, prev_start, prev_end)
    tasks = _task_stats(db, start, end)
    new_prospects = db.scalar(select(func.count()).select_from(Prospect).where(
        Prospect.created_at >= _dt(start), Prospect.created_at < _dt(end, True))) or 0
    won = db.scalar(select(func.count()).select_from(ProspectActivity).where(
        ProspectActivity.activity_type.in_(["stage_change", "converted"]),
        ProspectActivity.description.contains("WON") | ProspectActivity.description.contains("Won"),
        ProspectActivity.created_at >= _dt(start), ProspectActivity.created_at < _dt(end, True))) or 0
    pending = db.scalar(select(func.count()).select_from(Approval).where(Approval.status == ApprovalStatus.PENDING)) or 0
    campaigns = db.execute(select(Campaign.status, func.count()).where(Campaign.deleted_at.is_(None)).group_by(Campaign.status)).all()
    overdue_tasks = db.execute(
        select(Project.name, ProjectTask.title, ProjectTask.due_date).join(Project, Project.id == ProjectTask.project_id)
        .where(ProjectTask.status != ProjectTaskStatus.DONE, ProjectTask.due_date.is_not(None),
               ProjectTask.due_date < date.today(), Project.deleted_at.is_(None)).order_by(ProjectTask.due_date).limit(20)).all()
    return {
        "kpis": [_kpi("Revenue", fin["total_revenue"]), _kpi("Revenue last week", prev_fin["total_revenue"]),
                 _kpi("Expenses", fin["total_expenses"]), _kpi("New prospects", new_prospects),
                 _kpi("Deals won", won), _kpi("AI tasks completed", tasks["COMPLETED"]),
                 _kpi("AI tasks failed", tasks["FAILED"]), _kpi("Pending approvals", pending),
                 _kpi("Overdue invoices", fin["overdue_invoices"])],
        "sections": [
            _section("Pipeline movement", ["Stage change", "Prospects"], _stage_moves(db, start, end)),
            _section("Campaigns by status", ["Status", "Campaigns"], [[s.value, n] for s, n in campaigns]),
            _section("Overdue project tasks", ["Project", "Task", "Due"], [[p, t, d.isoformat()] for p, t, d in overdue_tasks]),
            _section("Agent work this week", AGENT_COLUMNS, _agent_rows(db, start, end)),
        ],
    }


def financial_summary(db: Session, start: date, end: date) -> dict[str, Any]:
    s = finance_service.summary(db, start, end)
    prof = finance_service.client_profitability(db, start, end)
    open_invoices = db.scalars(select(Invoice).where(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID])).order_by(Invoice.due_date))
    return {
        "kpis": _finance_kpis(s),
        "sections": [
            _section("Revenue by category", ["Category", "Amount"], [[k, v] for k, v in s["revenue_by_category"].items()]),
            _section("Expenses by category", ["Category", "Amount"], [[k, v] for k, v in s["expenses_by_category"].items()]),
            _section("Client profitability (estimate)", ["Client", "Revenue", "Direct AI cost", "Allocated expenses", "Est. profit", "Margin"],
                     [[r["client_name"], r["revenue"], r["direct_ai_cost"], r["allocated_expenses"], r["estimated_profit"],
                       f"{r['margin_percent']}%" if r["margin_percent"] is not None else "—"] for r in prof["rows"]],
                     note=prof["method"]),
            _section("Open invoices", ["Invoice", "Client", "Total", "Paid", "Balance", "Due", "Overdue"],
                     [[o["number"], o["client_name"], o["total"], o["paid"], o["balance"],
                       o["due_date"].isoformat() if o["due_date"] else "", "yes" if o["overdue"] else "no"]
                      for o in (finance_service.invoice_out(db, i) for i in open_invoices)]),
        ],
    }


def prospect_performance(db: Session, start: date, end: date) -> dict[str, Any]:
    rows = list(db.scalars(select(Prospect).where(Prospect.deleted_at.is_(None), Prospect.created_at >= _dt(start),
                                                  Prospect.created_at < _dt(end, True))))
    qualified = set(stages_from(SalesStage.QUALIFIED))

    def group(attr: str) -> list[list[Any]]:
        g: dict[str, list[Prospect]] = defaultdict(list)
        for p in rows:
            g[getattr(p, attr) or "(not set)"].append(p)
        out = []
        for k, ps in sorted(g.items(), key=lambda x: -len(x[1])):
            q = sum(p.stage in qualified for p in ps)
            w = sum(p.stage == SalesStage.WON for p in ps)
            out.append([k, len(ps), q, _pct(q, len(ps)), w, _pct(w, len(ps)), round(sum(p.lead_score for p in ps) / len(ps), 1)])
        return out

    cols = ["Group", "Prospects", "Qualified+", "Qualified rate", "Won", "Win rate", "Avg score"]
    return {
        "kpis": [_kpi("Prospects added", len(rows)), _kpi("Qualified or later", sum(p.stage in qualified for p in rows)),
                 _kpi("Won", sum(p.stage == SalesStage.WON for p in rows)), _kpi("Lost", sum(p.stage == SalesStage.LOST for p in rows))],
        "sections": [_section("By lead source", cols, group("lead_source")), _section("By industry", cols, group("industry")),
                     _section("By city", cols, group("city"))],
    }


def sales_conversion(db: Session, start: date, end: date) -> dict[str, Any]:
    rows = list(db.scalars(select(Prospect).where(Prospect.deleted_at.is_(None), Prospect.created_at >= _dt(start),
                                                  Prospect.created_at < _dt(end, True))))
    total = len(rows)
    funnel = []
    prev = total
    for stage in SALES_PIPELINE:
        reached = sum(p.stage in set(stages_from(stage)) for p in rows)
        funnel.append([stage.value, reached, _pct(reached, total), _pct(reached, prev)])
        prev = reached
    lost = sum(p.stage == SalesStage.LOST for p in rows)
    return {
        "kpis": [_kpi("Prospects", total), _kpi("Won", funnel[-1][1]), _kpi("Overall conversion", _pct(funnel[-1][1], total)),
                 _kpi("Lost", lost)],
        "sections": [_section("Pipeline funnel (prospects added in the period)", ["Stage", "Reached", "% of all", "% of previous stage"],
                              funnel, note="A prospect counts at every stage up to its current one. Lost prospects are not counted as reaching later stages.")],
    }


def campaign_performance(db: Session, start: date, end: date) -> dict[str, Any]:
    from app.services.campaign_service import derived_metrics

    rows = list(db.scalars(select(Campaign).where(Campaign.deleted_at.is_(None)).order_by(Campaign.id.desc())))
    out = []
    for c in rows:
        mt = c.metrics or {}
        d = derived_metrics(mt)
        out.append([c.name, c.campaign_type.value, c.status.value, mt.get("sent", ""), d["open_rate"] or "", d["click_rate"] or "",
                    d["reply_rate"] or "", mt.get("impressions", ""), d["ctr"] or "", mt.get("leads", ""), mt.get("conversions", ""),
                    mt.get("spend", ""), d["cost_per_lead"] or ""])
    with_metrics = [c for c in rows if c.metrics]
    return {
        "kpis": [_kpi("Campaigns", len(rows)), _kpi("With metrics", len(with_metrics)),
                 _kpi("Leads (all campaigns)", sum(int((c.metrics or {}).get("leads") or 0) for c in rows)),
                 _kpi("Spend (all campaigns)", finance_service.m(sum(Decimal(str((c.metrics or {}).get("spend") or 0)) for c in rows)))],
        "sections": [_section("Campaigns", ["Campaign", "Type", "Status", "Sent", "Open %", "Click %", "Reply %", "Impressions",
                                            "CTR %", "Leads", "Conversions", "Spend", "Cost per lead"], out,
                              note="Metrics are entered manually from your email/ads tools.")],
    }


def project_progress(db: Session, start: date, end: date) -> dict[str, Any]:
    projects = list(db.scalars(select(Project).where(Project.deleted_at.is_(None)).order_by(Project.client_id, Project.id)))
    clients = {c.id: c.company_name for c in db.scalars(select(Client))}
    counts = defaultdict(lambda: [0, 0, 0])
    today = date.today()
    for t in db.scalars(select(ProjectTask)):
        c = counts[t.project_id]
        c[0] += 1
        c[1] += t.status == ProjectTaskStatus.DONE
        c[2] += t.status != ProjectTaskStatus.DONE and t.due_date is not None and t.due_date < today
    rows = [[clients.get(p.client_id, "—"), p.name, p.status.value, f"{p.completion_percent}%", counts[p.id][1], counts[p.id][0],
             counts[p.id][2], p.due_date.isoformat() if p.due_date else "",
             "yes" if p.due_date and p.due_date < today and p.status in ACTIVE_PROJECT_STATUSES else "no"] for p in projects]
    active = [p for p in projects if p.status in ACTIVE_PROJECT_STATUSES]
    return {
        "kpis": [_kpi("Projects", len(projects)), _kpi("Active", len(active)),
                 _kpi("Average completion (active)", f"{round(sum(p.completion_percent for p in active) / len(active))}%" if active else "—"),
                 _kpi("Overdue tasks", sum(c[2] for c in counts.values()))],
        "sections": [_section("Projects", ["Client", "Project", "Status", "Complete", "Tasks done", "Tasks", "Overdue tasks", "Due", "Past due"], rows)],
    }


def agent_performance(db: Session, start: date, end: date) -> dict[str, Any]:
    rows = _agent_rows(db, start, end)
    runs = sum(r[1] for r in rows)
    ok = sum(r[2] for r in rows)
    return {
        "kpis": [_kpi("Runs", runs), _kpi("Success rate", _pct(ok, runs)), _kpi("Tokens", sum(r[5] for r in rows)),
                 _kpi("Est. cost (USD)", f"{sum(Decimal(r[6]) for r in rows):.4f}")],
        "sections": [_section("By agent", AGENT_COLUMNS, rows,
                              note="Cost is estimated from list prices; mock-provider runs cost nothing.")],
    }


def monthly_business(db: Session, start: date, end: date) -> dict[str, Any]:
    fin = financial_summary(db, start, end)
    sales = sales_conversion(db, start, end)
    proj = project_progress(db, start, end)
    agents = agent_performance(db, start, end)
    camp = campaign_performance(db, start, end)
    return {
        "kpis": fin["kpis"] + sales["kpis"][:3] + proj["kpis"][1:3],
        "sections": fin["sections"][:3] + sales["sections"] + proj["sections"] + camp["sections"] + agents["sections"],
    }


BUILDERS = {
    "daily_activity": daily_activity, "weekly_ceo": weekly_ceo, "monthly_business": monthly_business,
    "prospect_performance": prospect_performance, "sales_conversion": sales_conversion,
    "campaign_performance": campaign_performance, "project_progress": project_progress,
    "financial_summary": financial_summary, "agent_performance": agent_performance,
}


def build(db: Session, key: str, start: date | None, end: date | None) -> dict[str, Any]:
    if key not in BUILDERS:
        from app.utils.errors import NotFoundError

        raise NotFoundError("Unknown report")
    d_start, d_end = default_period(key)
    start, end = start or d_start, end or d_end
    if end < start:
        from app.utils.errors import AppError

        raise AppError("The end date is before the start date")
    if (end - start).days > 3660:
        from app.utils.errors import AppError

        raise AppError("Reports cover at most ten years")
    body = BUILDERS[key](db, start, end)
    return {"key": key, "title": REPORTS[key], "period": {"start": start, "end": end},
            "generated_at": datetime.now(UTC), **body}


def _cell(value: Any) -> str:
    text = value.isoformat() if hasattr(value, "isoformat") else ("" if value is None else str(value))
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(text) else text


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except Exception:  # noqa: BLE001
        return False


def to_csv(report: dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([report["title"]])
    w.writerow(["Period", _cell(report["period"]["start"]), _cell(report["period"]["end"])])
    w.writerow([])
    w.writerow(["Metric", "Value"])
    for k in report["kpis"]:
        w.writerow([_cell(k["label"]), _cell(k["value"])])
    for s in report["sections"]:
        w.writerow([])
        w.writerow([_cell(s["title"])])
        if s.get("note"):
            w.writerow([_cell(s["note"])])
        w.writerow([_cell(c) for c in s["columns"]])
        for row in s["rows"]:
            w.writerow([_cell(c) for c in row])
    return out.getvalue()

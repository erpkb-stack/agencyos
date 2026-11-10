"""Funnels: industry templates, lead-capture form, landing-page HTML, validation and publish approval.

AgencyOS does not host pages. "Publishing" means: request approval → approved → download the HTML
and host it → mark as published with the live URL. Any edit afterwards needs approval again.
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import approvals as gate
from app.marketing import funnel_templates, landing_page
from app.models.approvals import Approval
from app.models.crm import Client
from app.models.enums import ActorType, ApprovalActionType, ApprovalStatus, FunnelStatus as FS
from app.models.identity import User
from app.models.marketing import Funnel
from app.services import audit_service, drafting
from app.utils.errors import AppError, NotFoundError
from app.utils.time import utcnow

COPY_KEYS = {"headline": "headline", "subheadline": "subheadline", "sub-headline": "subheadline",
             "benefits": "benefits", "proof": "proof", "call to action": "call_to_action", "cta": "call_to_action"}


def get(db: Session, funnel_id: int) -> Funnel:
    f = db.get(Funnel, funnel_id)
    if f is None or f.deleted_at is not None:
        raise NotFoundError("Funnel not found")
    return f


def _audit(db: Session, user: User | None, f: Funnel, action: str, summary: str) -> None:
    audit_service.record(db, action, user=user, actor_type=ActorType.USER if user else ActorType.SYSTEM,
                         entity_type="funnel", entity_id=f.id, summary=summary)


def serialize(db: Session, f: Funnel, *, full: bool = False) -> dict[str, Any]:
    client = db.get(Client, f.client_id) if f.client_id else None
    issues = landing_page.validate(f)
    out = {
        "id": f.id, "name": f.name, "client_id": f.client_id, "client_name": client.company_name if client else None,
        "industry": f.industry, "template_key": f.template_key, "landing_page_url": f.landing_page_url,
        "target_conversion": f.target_conversion, "call_to_action": f.call_to_action, "status": f.status.value,
        "ready": not any(i["level"] == "error" for i in issues), "created_at": f.created_at, "updated_at": f.updated_at,
    }
    if full:
        pending = gate.pending_for(db, "funnel", f.id)
        out.update(
            lead_fields=f.lead_fields or [], form_config=f.form_config or {},
            follow_up_steps=(f.follow_up_workflow or {}).get("steps", []),
            validation=issues, mobile_checks=landing_page.mobile_checks(f.generated_html or ""),
            pending_approval_id=pending.id if pending else None,
            approvals=[{"id": a.id, "status": a.status.value, "title": a.title, "comments": a.comments,
                        "decided_at": a.decided_at, "created_at": a.created_at}
                       for a in gate.history_for(db, "funnel", f.id)],
            ai_tasks=[drafting.task_brief(t) for t in drafting.linked_tasks(db, "funnel", f.id)],
        )
    return out


def search(db: Session, *, q: str | None, status: FS | None, client_id: int | None, offset: int,
           limit: int) -> tuple[list[dict[str, Any]], int]:
    query = select(Funnel).where(Funnel.deleted_at.is_(None))
    if q:
        query = query.where(func.lower(Funnel.name).like(f"%{q.strip().lower()}%"))
    if status:
        query = query.where(Funnel.status == status)
    if client_id:
        query = query.where(Funnel.client_id == client_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    return [serialize(db, f) for f in db.scalars(query.order_by(Funnel.id.desc()).offset(offset).limit(limit))], total


def _apply(f: Funnel, data: dict[str, Any]) -> list[str]:
    changed = []
    for key in ("name", "client_id", "industry", "landing_page_url", "target_conversion", "call_to_action"):
        if key in data and not (key == "name" and data[key] is None) and getattr(f, key) != data[key]:
            setattr(f, key, data[key])
            changed.append(key)
    if data.get("lead_fields") is not None:
        f.lead_fields = jsonable_encoder(data["lead_fields"])
        changed.append("lead_fields")
    if data.get("form_config") is not None:
        f.form_config = jsonable_encoder(data["form_config"])
        changed.append("form_config")
    if data.get("follow_up_steps") is not None:
        f.follow_up_workflow = {"steps": jsonable_encoder(data["follow_up_steps"])}
        changed.append("follow_up_workflow")
    return changed


def regenerate(f: Funnel) -> None:
    f.generated_html = landing_page.render(f)


def create(db: Session, user: User, data: dict[str, Any]) -> Funnel:
    if data.get("client_id") is not None and (db.get(Client, data["client_id"]) is None):
        raise NotFoundError("Client not found")
    f = Funnel(name=data["name"], status=FS.DRAFT, lead_fields=[])
    key = data.get("template_key")
    if key:
        template = funnel_templates.TEMPLATES.get(key)
        if template is None:
            raise AppError(f"Unknown template '{key}'")
        t = copy.deepcopy(template)
        f.template_key, f.industry = key, t["industry"]
        f.call_to_action, f.target_conversion = t["call_to_action"], t["target_conversion"]
        f.lead_fields, f.form_config, f.follow_up_workflow = t["lead_fields"], t["form_config"], t["follow_up_workflow"]
    _apply(f, {k: v for k, v in data.items() if k != "template_key"})
    if f.form_config is None:
        f.form_config = {"copy": {"headline": f.name}, "consent_text": ""}
    regenerate(f)
    db.add(f)
    db.flush()
    _audit(db, user, f, "funnel.created", f"Funnel '{f.name}' created" + (f" from the {key} template" if key else ""))
    return f


def update(db: Session, user: User, f: Funnel, data: dict[str, Any]) -> Funnel:
    if f.status == FS.AWAITING_APPROVAL:
        raise AppError("The funnel is awaiting approval; wait for the decision before editing")
    if data.get("client_id") is not None and db.get(Client, data["client_id"]) is None:
        raise NotFoundError("Client not found")
    changed = _apply(f, data)
    if not changed:
        return f
    regenerate(f)
    note = ""
    if f.status in (FS.APPROVED, FS.PUBLISHED):
        note = f" (was {f.status.value.lower()}; needs approval again)"
        f.status = FS.DRAFT
    _audit(db, user, f, "funnel.updated", f"Updated '{f.name}': {', '.join(changed)}{note}")
    return f


def delete(db: Session, user: User, f: Funnel) -> None:
    if f.status == FS.AWAITING_APPROVAL:
        raise AppError("Wait for the approval decision before deleting this funnel")
    f.deleted_at = utcnow()
    _audit(db, user, f, "funnel.deleted", f"Funnel '{f.name}' deleted")


def request_publish(db: Session, user: User, f: Funnel) -> Approval:
    if f.status != FS.DRAFT:
        raise AppError("Only draft funnels can be sent for approval")
    errors = [i["message"] for i in landing_page.validate(f) if i["level"] == "error"]
    failed = [c["check"] for c in landing_page.mobile_checks(f.generated_html or "") if not c["passed"]]
    if errors or failed:
        raise AppError("The funnel is not ready to publish", details=errors + [f"Mobile check failed: {c}" for c in failed])
    approval = gate.request(db, user=user, action_type=ApprovalActionType.PUBLISH_WEBSITE,
                            title=f"Publish landing page '{f.name}'", entity_type="funnel", entity_id=f.id,
                            client_id=f.client_id, details=f"{len(f.lead_fields or [])} form fields; CTA "
                            f"'{f.call_to_action}'. Approval allows hosting the downloaded HTML.")
    f.status = FS.AWAITING_APPROVAL
    return approval


def _on_decided(db: Session, approval: Approval) -> None:
    f = db.get(Funnel, (approval.payload or {}).get("entity_id"))
    if f is None or f.status != FS.AWAITING_APPROVAL:
        return
    f.status = FS.APPROVED if approval.status == ApprovalStatus.APPROVED else FS.DRAFT
    _audit(db, None, f, "funnel.approval_settled", f"Publishing '{f.name}' {approval.status.value.lower()}")


gate.register("funnel", _on_decided)


def mark_published(db: Session, user: User, f: Funnel, url: str) -> Funnel:
    if f.status not in (FS.APPROVED, FS.PUBLISHED):
        raise AppError("Only approved funnels can be marked as published")
    f.landing_page_url = url
    f.status = FS.PUBLISHED
    _audit(db, user, f, "funnel.published", f"'{f.name}' marked as published at {url} (hosted outside AgencyOS)")
    return f


# -- AI copy -----------------------------------------------------------------------------------


def generate_copy(db: Session, user: User, f: Funnel, notes: str | None):
    if f.status != FS.DRAFT:
        raise AppError("Generate new copy while the funnel is a draft")
    client = db.get(Client, f.client_id) if f.client_id else None
    context = jsonable_encoder({
        "funnel": {"name": f.name, "industry": f.industry, "target_conversion": f.target_conversion,
                   "call_to_action": f.call_to_action, "current_copy": (f.form_config or {}).get("copy")},
        "client": {"company_name": client.company_name, "industry": client.industry,
                   "website": client.website} if client else None,
    })
    instructions = ("Write landing-page copy for this funnel. Return separate deliverables titled exactly "
                    "'Headline', 'Subheadline', 'Benefits' (one benefit per line, at most 6), 'Proof' and "
                    "'Call to action'. Do not invent reviews, statistics, awards, licences or guarantees; use a "
                    "bracketed placeholder such as [customer review] where real proof is needed.")
    if notes:
        instructions += f"\n\nFounder's notes: {notes}"
    return drafting.start(db, user, agent_key="funnel", title=f"{f.name}: landing page copy", instructions=instructions,
                          context=context, task_type="funnel_generate", entity_type="funnel", entity_id=f.id)


def import_copy(db: Session, user: User, f: Funnel, task_id: int) -> dict[str, Any]:
    task, deliverables = drafting.finished_deliverables(db, task_id, "funnel", f.id)
    if f.status != FS.DRAFT:
        raise AppError("Import copy while the funnel is a draft")
    config = copy.deepcopy(f.form_config or {})
    page_copy = dict(config.get("copy") or {})
    used, unused = [], []
    for d in deliverables:
        key = COPY_KEYS.get((d.get("title") or "").strip().lower())
        content = (d.get("content") or "").strip()
        if not key or not content:
            unused.append(d.get("title"))
            continue
        if key == "benefits":
            page_copy["benefits"] = [line.strip(" -•*\t") for line in content.splitlines() if line.strip(" -•*\t")][:6]
        elif key == "call_to_action":
            f.call_to_action = content.splitlines()[0][:200]
            config["submit_label"] = f.call_to_action[:60]
        else:
            page_copy[key] = content.splitlines()[0][:400] if key != "proof" else content[:400]
        used.append(d.get("title"))
    if not used:
        raise AppError("None of the deliverables matched Headline, Subheadline, Benefits, Proof or Call to action",
                       details=[t for t in unused if t])
    config["copy"] = page_copy
    f.form_config = config
    regenerate(f)
    _audit(db, user, f, "funnel.copy_imported", f"Imported AI copy from task #{task.id} into '{f.name}'")
    return {"used": used, "unused": unused}

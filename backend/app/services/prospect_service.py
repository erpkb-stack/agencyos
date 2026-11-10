"""Prospects: CRUD, search, duplicate detection, activity timeline, CSV import/export, conversion."""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.crm import Client, Prospect, ProspectActivity
from app.models.enums import ActorType, ContractStatus, SalesStage
from app.models.identity import User
from app.schemas.crm import ProspectCreate
from app.services import audit_service
from app.utils.errors import AppError, ConflictError, NotFoundError
from app.utils.time import utcnow
from app.utils.validation import company_key, domain_of, phone_digits

FIELDS = [
    "company_name", "website", "industry", "address", "city", "state", "zip_code", "phone", "email",
    "contact_name", "contact_title", "lead_source", "observations", "opportunity", "lead_score",
    "stage", "next_follow_up_on", "notes",
]
SORTABLE = {
    "company_name": Prospect.company_name, "lead_score": Prospect.lead_score,
    "created_at": Prospect.created_at, "updated_at": Prospect.updated_at,
    "next_follow_up_on": Prospect.next_follow_up_on, "stage": Prospect.stage, "city": Prospect.city,
}
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000


def serialize(p: Prospect) -> dict[str, Any]:
    return {
        "id": p.id, **{f: getattr(p, f) for f in FIELDS},
        "stage": p.stage.value, "normalized_domain": p.normalized_domain,
        "last_contacted_at": p.last_contacted_at, "created_at": p.created_at, "updated_at": p.updated_at,
    }


def get(db: Session, prospect_id: int) -> Prospect:
    p = db.get(Prospect, prospect_id)
    if p is None or p.deleted_at is not None:
        raise NotFoundError("Prospect not found")
    return p


def _log(db: Session, p: Prospect, kind: str, text: str, user: User | None = None,
         agent_id: int | None = None) -> None:
    db.add(ProspectActivity(prospect_id=p.id, activity_type=kind, description=text,
                            user_id=user.id if user else None, agent_id=agent_id))


# -- duplicates --------------------------------------------------------------------------------


def find_duplicates(db: Session, data: dict[str, Any], exclude_id: int | None = None) -> list[dict[str, Any]]:
    """Same website domain, email, phone, or company name in the same city."""
    domain = domain_of(data.get("website"))
    email = (data.get("email") or "").lower() or None
    phone = phone_digits(data.get("phone"))
    key = company_key(data.get("company_name"))
    city = (data.get("city") or "").strip().lower()

    candidates_q = select(Prospect).where(Prospect.deleted_at.is_(None))
    conditions = []
    if domain:
        conditions.append(Prospect.normalized_domain == domain)
    if email:
        conditions.append(func.lower(Prospect.email) == email)
    if key:
        first = key.split()[0]
        conditions.append(func.lower(Prospect.company_name).contains(first))
    if phone:
        conditions.append(Prospect.phone.is_not(None))
    if not conditions:
        return []
    candidates_q = candidates_q.where(or_(*conditions))
    if exclude_id:
        candidates_q = candidates_q.where(Prospect.id != exclude_id)

    matches = []
    for p in db.scalars(candidates_q.limit(500)):
        reasons = []
        if domain and p.normalized_domain == domain:
            reasons.append("same website")
        if email and (p.email or "").lower() == email:
            reasons.append("same email")
        if phone and phone_digits(p.phone) == phone:
            reasons.append("same phone")
        if key and company_key(p.company_name) == key and (not city or (p.city or "").lower() == city):
            reasons.append("same company name" + (" and city" if city else ""))
        if reasons:
            matches.append({"id": p.id, "company_name": p.company_name, "city": p.city, "reasons": reasons})
    return matches


# -- CRUD --------------------------------------------------------------------------------------


def create(db: Session, user: User, data: dict[str, Any], *, allow_duplicate: bool = False,
           source: str = "manual") -> Prospect:
    if not allow_duplicate:
        dupes = find_duplicates(db, data)
        if dupes:
            raise ConflictError("This prospect looks like a duplicate", details={"duplicates": dupes})
    p = Prospect(**{k: v for k, v in data.items() if k in FIELDS and v is not None})
    p.normalized_domain = domain_of(p.website)
    db.add(p)
    db.flush()
    _log(db, p, "created", f"Added ({source})", user)
    audit_service.record(db, "prospect.created", user=user, actor_type=ActorType.USER,
                         entity_type="prospect", entity_id=p.id, summary=f"Added prospect {p.company_name}")
    return p


def update(db: Session, user: User, p: Prospect, changes: dict[str, Any]) -> Prospect:
    changes = {k: v for k, v in changes.items() if k in FIELDS}
    if "company_name" in changes and changes["company_name"] is None:
        changes.pop("company_name")
    changed = [k for k, v in changes.items() if getattr(p, k) != v]
    if not changed:
        return p
    old_stage = p.stage
    for k in changed:
        setattr(p, k, changes[k])
    p.normalized_domain = domain_of(p.website)
    if "stage" in changed:
        _log(db, p, "stage_change", f"Stage {old_stage.value} → {p.stage.value}", user)
    other = [k for k in changed if k != "stage"]
    if other:
        _log(db, p, "updated", "Updated " + ", ".join(k.replace("_", " ") for k in other), user)
    audit_service.record(db, "prospect.updated", user=user, actor_type=ActorType.USER,
                         entity_type="prospect", entity_id=p.id,
                         summary=f"Updated {p.company_name}: {', '.join(changed)}")
    return p


def soft_delete(db: Session, user: User, p: Prospect) -> None:
    p.deleted_at = utcnow()
    _log(db, p, "deleted", "Deleted", user)
    audit_service.record(db, "prospect.deleted", user=user, actor_type=ActorType.USER,
                         entity_type="prospect", entity_id=p.id, summary=f"Deleted prospect {p.company_name}")


def add_activity(db: Session, user: User, p: Prospect, kind: str, text: str, mark_contacted: bool) -> ProspectActivity:
    activity = ProspectActivity(prospect_id=p.id, activity_type=kind, description=text, user_id=user.id)
    db.add(activity)
    if mark_contacted:
        p.last_contacted_at = utcnow()
    p.updated_at = utcnow()
    return activity


def activities(db: Session, p: Prospect) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ProspectActivity, User.full_name)
        .outerjoin(User, User.id == ProspectActivity.user_id)
        .where(ProspectActivity.prospect_id == p.id)
        .order_by(ProspectActivity.id.desc())
    ).all()
    from app.models.ai import Agent

    agent_names = {a.id: a.name for a in db.scalars(select(Agent))}
    return [
        {"id": a.id, "activity_type": a.activity_type, "description": a.description,
         "user": name, "agent": agent_names.get(a.agent_id), "created_at": a.created_at}
        for a, name in rows
    ]


def search(db: Session, *, q: str | None, stage: SalesStage | None, industry: str | None,
           city: str | None, min_score: int | None, follow_up_due: bool, sort: str, order: str,
           offset: int, limit: int) -> tuple[list[Prospect], int]:
    query = select(Prospect).where(Prospect.deleted_at.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(
            func.lower(Prospect.company_name).like(like), func.lower(Prospect.contact_name).like(like),
            func.lower(Prospect.email).like(like), func.lower(Prospect.city).like(like),
            func.lower(Prospect.website).like(like),
        ))
    if stage:
        query = query.where(Prospect.stage == stage)
    if industry:
        query = query.where(func.lower(Prospect.industry) == industry.strip().lower())
    if city:
        query = query.where(func.lower(Prospect.city) == city.strip().lower())
    if min_score is not None:
        query = query.where(Prospect.lead_score >= min_score)
    if follow_up_due:
        query = query.where(Prospect.next_follow_up_on.is_not(None), Prospect.next_follow_up_on <= date.today())
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    column = SORTABLE.get(sort, Prospect.created_at)
    ordering = column.asc() if order == "asc" else column.desc()
    rows = list(db.scalars(query.order_by(ordering, Prospect.id.desc()).offset(offset).limit(limit)))
    return rows, total


def facets(db: Session) -> dict[str, list[str]]:
    def distinct(col):
        return [v for v in db.scalars(select(col).where(Prospect.deleted_at.is_(None), col.is_not(None))
                                      .distinct().order_by(col)) if v]
    return {"industries": distinct(Prospect.industry), "cities": distinct(Prospect.city)}


# -- conversion --------------------------------------------------------------------------------


def convert_to_client(db: Session, user: User, p: Prospect, data: dict[str, Any]) -> Client:
    existing = db.scalar(select(Client).where(Client.prospect_id == p.id, Client.deleted_at.is_(None)))
    if existing:
        raise ConflictError("This prospect is already a client", details={"client_id": existing.id})
    client = Client(
        prospect_id=p.id, company_name=p.company_name, contact_name=p.contact_name, email=p.email,
        phone=p.phone, website=p.website, industry=p.industry,
        address=", ".join(x for x in (p.address, p.city, p.state, p.zip_code) if x) or None,
        contract_status=ContractStatus.ACTIVE, start_date=data.get("start_date") or date.today(),
        service_package=data.get("service_package"), monthly_fee=data.get("monthly_fee") or 0,
        setup_fee=data.get("setup_fee") or 0,
    )
    db.add(client)
    db.flush()
    old = p.stage
    p.stage = SalesStage.WON
    _log(db, p, "converted", f"Won and converted to client #{client.id}" + (f" (stage was {old.value})" if old != SalesStage.WON else ""), user)
    audit_service.record(db, "prospect.converted", user=user, actor_type=ActorType.USER,
                         entity_type="client", entity_id=client.id,
                         summary=f"Converted prospect {p.company_name} to a client")
    return client


# -- CSV ---------------------------------------------------------------------------------------

_HEADER_ALIASES = {
    "company": "company_name", "company name": "company_name", "business name": "company_name",
    "name": "company_name", "url": "website", "site": "website", "zip": "zip_code", "postal code": "zip_code",
    "zip code": "zip_code", "business phone": "phone", "phone number": "phone", "business email": "email",
    "contact": "contact_name", "contact person": "contact_name", "title": "contact_title",
    "source": "lead_source", "score": "lead_score", "sales stage": "stage", "follow up": "next_follow_up_on",
    "next follow-up": "next_follow_up_on", "business address": "address", "street": "address",
}


def _header(name: str) -> str:
    key = name.strip().lower().replace("_", " ")
    if key in _HEADER_ALIASES:
        return _HEADER_ALIASES[key]
    snake = key.replace(" ", "_").replace("-", "_")
    return snake


def import_csv(db: Session, user: User, raw: bytes, *, skip_duplicates: bool = True) -> dict[str, Any]:
    if len(raw) > MAX_IMPORT_BYTES:
        raise AppError(f"CSV is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError("CSV has no header row")
    mapping = {h: _header(h) for h in reader.fieldnames if h}
    if "company_name" not in mapping.values():
        raise AppError("CSV needs a company name column (e.g. 'company_name' or 'Company')")
    ignored = sorted({h for h, f in mapping.items() if f not in FIELDS})

    created, skipped, errors = [], [], []
    for index, row in enumerate(reader, start=2):  # row 1 is the header
        if index - 1 > MAX_IMPORT_ROWS:
            errors.append({"row": index, "error": f"Stopped: more than {MAX_IMPORT_ROWS} rows"})
            break
        values = {mapping[h]: (v or "").strip() for h, v in row.items() if h in mapping and mapping[h] in FIELDS}
        values = {k: v for k, v in values.items() if v != ""}
        if not values:
            continue
        if "stage" in values:
            values["stage"] = values["stage"].upper().replace(" ", "_")
        try:
            parsed = ProspectCreate.model_validate(values).model_dump(exclude={"allow_duplicate"}, exclude_none=True)
        except ValidationError as exc:
            first = exc.errors()[0]
            errors.append({"row": index, "error": f"{'.'.join(map(str, first['loc']))}: {first['msg']}"})
            continue
        dupes = find_duplicates(db, parsed)
        if dupes and skip_duplicates:
            skipped.append({"row": index, "company_name": parsed["company_name"],
                            "duplicate_of": dupes[0]["id"], "reasons": dupes[0]["reasons"]})
            continue
        p = create(db, user, parsed, allow_duplicate=True, source="CSV import")
        db.flush()
        created.append(p.id)
    audit_service.record(db, "prospect.imported", user=user, actor_type=ActorType.USER,
                         entity_type="prospect", summary=f"CSV import: {len(created)} added, "
                         f"{len(skipped)} duplicates skipped, {len(errors)} rows with errors")
    return {"created": len(created), "created_ids": created, "skipped_duplicates": skipped,
            "errors": errors, "ignored_columns": ignored}


def _safe_cell(value: Any) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @ at the start of a cell)."""
    if value is None:
        return ""
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(rows: list[Prospect]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    columns = ["id", *FIELDS, "last_contacted_at", "created_at"]
    writer.writerow(columns)
    for p in rows:
        writer.writerow([_safe_cell(p.stage.value if c == "stage" else getattr(p, c)) for c in columns])
    return out.getvalue()

"""Finance: revenue, expenses, invoices, payments and the numbers derived from them.

Rules:
* Money is Decimal, rounded to cents with ROUND_HALF_UP; floats are never used for amounts.
* Revenue is cash received. Recording a payment creates its revenue row; deleting the payment
  removes it. Payment-linked revenue cannot be edited directly.
* An invoice's paid status is derived from its payments only (never set by hand).
* Invoices can be edited only as drafts; voiding requires no payments.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ai import AgentExecution, AgentTask
from app.models.crm import Client
from app.models.delivery import Project
from app.models.enums import (
    ActorType, ContractStatus, ExpenseCategory, InvoiceStatus as IS, RevenueCategory,
)
from app.models.finance import Expense, Invoice, InvoiceItem, Payment, Revenue
from app.models.identity import User
from app.services import audit_service
from app.utils.errors import AppError, ConflictError, NotFoundError
from app.utils.time import utcnow

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def m(value: Any) -> str:
    return str(money(value))


def _audit(db: Session, user: User, action: str, entity: str, entity_id: int | None, summary: str) -> None:
    audit_service.record(db, action, user=user, actor_type=ActorType.USER, entity_type=entity,
                         entity_id=entity_id, summary=summary)


def _client(db: Session, client_id: int | None) -> Client | None:
    if client_id is None:
        return None
    c = db.get(Client, client_id)
    if c is None or c.deleted_at is not None:
        raise NotFoundError("Client not found")
    return c


def day_start(d: date) -> datetime:
    """Midnight at the start of ``d`` in the server's local time zone, as an aware datetime.

    Report dates are calendar days as the user sees them (``date.today()`` is local too); stored
    timestamps are UTC, so the boundary is converted rather than assumed to be UTC midnight.
    """
    return datetime.combine(d, time.min).astimezone()


def month_range(month: str) -> tuple[date, date]:
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, 1), date(year, mon, monthrange(year, mon)[1])


# -- revenue -----------------------------------------------------------------------------------


def revenue_out(db: Session, r: Revenue) -> dict[str, Any]:
    client = db.get(Client, r.client_id) if r.client_id else None
    return {"id": r.id, "client_id": r.client_id, "client_name": client.company_name if client else None,
            "payment_id": r.payment_id, "category": r.category.value, "description": r.description,
            "amount": m(r.amount), "received_on": r.received_on, "from_payment": r.payment_id is not None,
            "created_at": r.created_at}


def list_revenue(db: Session, start: date | None, end: date | None, client_id: int | None,
                 category: RevenueCategory | None) -> list[Revenue]:
    q = select(Revenue)
    if start:
        q = q.where(Revenue.received_on >= start)
    if end:
        q = q.where(Revenue.received_on <= end)
    if client_id:
        q = q.where(Revenue.client_id == client_id)
    if category:
        q = q.where(Revenue.category == category)
    return list(db.scalars(q.order_by(Revenue.received_on.desc(), Revenue.id.desc()).limit(1000)))


def create_revenue(db: Session, user: User, data: dict[str, Any]) -> Revenue:
    _client(db, data.get("client_id"))
    r = Revenue(client_id=data.get("client_id"), category=data["category"], description=data.get("description"),
                amount=money(data["amount"]), received_on=data["received_on"])
    db.add(r)
    db.flush()
    _audit(db, user, "finance.revenue_added", "revenue", r.id, f"Revenue {m(r.amount)} ({r.category.value.lower()})")
    return r


def _manual_revenue(db: Session, revenue_id: int) -> Revenue:
    r = db.get(Revenue, revenue_id)
    if r is None:
        raise NotFoundError("Revenue entry not found")
    if r.payment_id is not None:
        raise AppError("This revenue comes from a payment; change or delete the payment instead")
    return r


def update_revenue(db: Session, user: User, revenue_id: int, changes: dict[str, Any]) -> Revenue:
    r = _manual_revenue(db, revenue_id)
    if "client_id" in changes:
        _client(db, changes["client_id"])
    for k, v in changes.items():
        if v is not None or k in ("client_id", "description"):
            setattr(r, k, money(v) if k == "amount" else v)
    _audit(db, user, "finance.revenue_updated", "revenue", r.id, f"Revenue entry #{r.id} updated")
    return r


def delete_revenue(db: Session, user: User, revenue_id: int) -> None:
    r = _manual_revenue(db, revenue_id)
    db.delete(r)
    _audit(db, user, "finance.revenue_deleted", "revenue", revenue_id, f"Revenue entry #{revenue_id} ({m(r.amount)}) deleted")


# -- expenses ----------------------------------------------------------------------------------


def expense_out(e: Expense) -> dict[str, Any]:
    return {"id": e.id, "category": e.category.value, "vendor": e.vendor, "description": e.description,
            "amount": m(e.amount), "incurred_on": e.incurred_on, "is_recurring": e.is_recurring,
            "created_at": e.created_at}


def list_expenses(db: Session, start: date | None, end: date | None, category: ExpenseCategory | None) -> list[Expense]:
    q = select(Expense).where(Expense.deleted_at.is_(None))
    if start:
        q = q.where(Expense.incurred_on >= start)
    if end:
        q = q.where(Expense.incurred_on <= end)
    if category:
        q = q.where(Expense.category == category)
    return list(db.scalars(q.order_by(Expense.incurred_on.desc(), Expense.id.desc()).limit(1000)))


def get_expense(db: Session, expense_id: int) -> Expense:
    e = db.get(Expense, expense_id)
    if e is None or e.deleted_at is not None:
        raise NotFoundError("Expense not found")
    return e


def create_expense(db: Session, user: User, data: dict[str, Any]) -> Expense:
    e = Expense(**{**data, "amount": money(data["amount"])})
    db.add(e)
    db.flush()
    _audit(db, user, "finance.expense_added", "expense", e.id, f"Expense {m(e.amount)} ({e.category.value.lower()})")
    return e


def update_expense(db: Session, user: User, e: Expense, changes: dict[str, Any]) -> Expense:
    for k, v in changes.items():
        if v is not None or k in ("vendor", "description"):
            setattr(e, k, money(v) if k == "amount" else v)
    _audit(db, user, "finance.expense_updated", "expense", e.id, f"Expense #{e.id} updated")
    return e


def delete_expense(db: Session, user: User, e: Expense) -> None:
    e.deleted_at = utcnow()
    _audit(db, user, "finance.expense_deleted", "expense", e.id, f"Expense #{e.id} ({m(e.amount)}) deleted")


def ai_usage(db: Session, month: str) -> dict[str, Any]:
    """Estimated AI spend from recorded executions. An estimate: check the provider's invoice."""
    start, end = month_range(month)
    rows = db.execute(
        select(AgentExecution.provider, AgentExecution.model, func.count(),
               func.sum(AgentExecution.prompt_tokens), func.sum(AgentExecution.completion_tokens),
               func.sum(AgentExecution.estimated_cost))
        .where(AgentExecution.started_at >= day_start(start), AgentExecution.started_at < day_start(end + timedelta(days=1)))
        .group_by(AgentExecution.provider, AgentExecution.model)
    ).all()
    by_model = [{"provider": p, "model": mo, "calls": n, "prompt_tokens": int(pt or 0),
                 "completion_tokens": int(ct or 0), "estimated_cost": f"{Decimal(str(c or 0)):.4f}"}
                for p, mo, n, pt, ct, c in rows]
    total = sum((Decimal(r["estimated_cost"]) for r in by_model), Decimal("0"))
    unpriced = sum(r["calls"] for r in by_model if r["provider"] != "mock" and Decimal(r["estimated_cost"]) == 0)
    recorded = db.scalar(select(func.sum(Expense.amount)).where(
        Expense.deleted_at.is_(None), Expense.category == ExpenseCategory.AI_API,
        Expense.incurred_on >= start, Expense.incurred_on <= end))
    return {"month": month, "by_model": by_model, "estimated_total": m(total),
            "calls_without_price": unpriced, "recorded_ai_expenses": m(recorded)}


def record_ai_expense(db: Session, user: User, month: str, amount: Decimal, vendor: str | None) -> Expense:
    start, end = month_range(month)
    return create_expense(db, user, {"category": ExpenseCategory.AI_API, "vendor": vendor or "AI providers",
                                     "description": f"AI API usage {month}", "amount": amount,
                                     "incurred_on": min(end, date.today()), "is_recurring": False})


# -- invoices ----------------------------------------------------------------------------------


def get_invoice(db: Session, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise NotFoundError("Invoice not found")
    return inv


def paid_amount(inv: Invoice) -> Decimal:
    return money(sum((money(p.amount) for p in inv.payments), ZERO))


def is_overdue(inv: Invoice, today: date | None = None) -> bool:
    today = today or date.today()
    return inv.status in (IS.SENT, IS.PARTIALLY_PAID) and inv.due_date is not None and inv.due_date < today


def invoice_out(db: Session, inv: Invoice, *, full: bool = False) -> dict[str, Any]:
    client = db.get(Client, inv.client_id)
    paid = paid_amount(inv)
    out = {
        "id": inv.id, "number": inv.number, "client_id": inv.client_id,
        "client_name": client.company_name if client else None, "status": inv.status.value,
        "issue_date": inv.issue_date, "due_date": inv.due_date, "subtotal": m(inv.subtotal), "total": m(inv.total),
        "paid": m(paid), "balance": m(money(inv.total) - paid if inv.status != IS.VOID else 0),
        "overdue": is_overdue(inv), "notes": inv.notes, "created_at": inv.created_at, "updated_at": inv.updated_at,
    }
    if full:
        out["items"] = [{"id": i.id, "description": i.description, "quantity": m(i.quantity),
                         "unit_price": m(i.unit_price), "amount": m(i.amount)} for i in inv.items]
        out["payments"] = [payment_out(db, p) for p in sorted(inv.payments, key=lambda p: (p.paid_on, p.id))]
    return out


def _next_number(db: Session, year: int) -> str:
    prefix = f"INV-{year}-"
    last = db.scalar(select(Invoice.number).where(Invoice.number.like(f"{prefix}%")).order_by(Invoice.number.desc()))
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def _set_items(inv: Invoice, items: list[dict[str, Any]]) -> None:
    inv.items.clear()
    subtotal = ZERO
    for it in items:
        qty, price = money(it["quantity"]), money(it["unit_price"])
        amount = money(qty * price)
        inv.items.append(InvoiceItem(description=it["description"], quantity=qty, unit_price=price, amount=amount))
        subtotal += amount
    inv.subtotal = money(subtotal)
    inv.total = inv.subtotal  # no tax handling yet; documented limitation


def _check_dates(issue: date | None, due: date | None) -> None:
    if issue and due and due < issue:
        raise AppError("Due date cannot be before the issue date")


def create_invoice(db: Session, user: User, data: dict[str, Any]) -> Invoice:
    _client(db, data["client_id"])
    _check_dates(data.get("issue_date"), data.get("due_date"))
    issue = data.get("issue_date")
    inv = Invoice(client_id=data["client_id"], number=_next_number(db, (issue or date.today()).year), status=IS.DRAFT,
                  issue_date=issue, due_date=data.get("due_date"), notes=data.get("notes"))
    _set_items(inv, data["items"])
    db.add(inv)
    db.flush()
    _audit(db, user, "finance.invoice_drafted", "invoice", inv.id, f"Invoice {inv.number} drafted ({m(inv.total)})")
    return inv


def invoice_from_client(db: Session, user: User, client_id: int, data: dict[str, Any]) -> Invoice:
    """Draft an invoice from the client's agreed fees (retainer and, optionally, setup fee)."""
    client = _client(db, client_id)
    assert client is not None
    items = []
    label = data.get("period_label") or date.today().strftime("%B %Y")
    if money(client.monthly_fee) > 0:
        items.append({"description": f"{client.service_package or 'Monthly retainer'} — {label}",
                      "quantity": Decimal("1"), "unit_price": client.monthly_fee})
    if data.get("include_setup_fee") and money(client.setup_fee) > 0:
        items.append({"description": "One-time setup fee", "quantity": Decimal("1"), "unit_price": client.setup_fee})
    if not items:
        raise AppError("This client has no monthly or setup fee to invoice; add line items manually")
    today = date.today()
    return create_invoice(db, user, {"client_id": client.id, "issue_date": today,
                                     "due_date": today + timedelta(days=data.get("due_in_days", 14)), "items": items})


def update_invoice(db: Session, user: User, inv: Invoice, changes: dict[str, Any]) -> Invoice:
    if inv.status != IS.DRAFT:
        raise AppError("Only draft invoices can be edited")
    for k in ("issue_date", "due_date", "notes"):
        if k in changes:
            setattr(inv, k, changes[k])
    _check_dates(inv.issue_date, inv.due_date)
    if changes.get("items"):
        _set_items(inv, changes["items"])
    _audit(db, user, "finance.invoice_updated", "invoice", inv.id, f"Invoice {inv.number} updated")
    return inv


def mark_sent(db: Session, user: User, inv: Invoice) -> Invoice:
    """Record that the invoice was sent from outside AgencyOS (nothing is emailed from here)."""
    if inv.status != IS.DRAFT:
        raise AppError("Only draft invoices can be marked as sent")
    if money(inv.total) <= 0:
        raise AppError("An invoice must have a total above zero")
    inv.issue_date = inv.issue_date or date.today()
    if inv.due_date is None:
        inv.due_date = inv.issue_date + timedelta(days=14)
    inv.status = IS.SENT
    _refresh_status(inv)
    _audit(db, user, "finance.invoice_sent", "invoice", inv.id,
           f"Invoice {inv.number} marked as sent (sent outside AgencyOS)")
    return inv


def void_invoice(db: Session, user: User, inv: Invoice) -> Invoice:
    if inv.payments:
        raise ConflictError("An invoice with payments cannot be voided; delete the payments first")
    if inv.status == IS.VOID:
        raise AppError("Invoice is already void")
    inv.status = IS.VOID
    _audit(db, user, "finance.invoice_voided", "invoice", inv.id, f"Invoice {inv.number} voided")
    return inv


def delete_invoice(db: Session, user: User, inv: Invoice) -> None:
    if inv.status != IS.DRAFT:
        raise AppError("Only draft invoices can be deleted; void sent invoices instead")
    _audit(db, user, "finance.invoice_deleted", "invoice", inv.id, f"Draft invoice {inv.number} deleted")
    db.delete(inv)


def _refresh_status(inv: Invoice) -> None:
    """Paid status follows the payment records, never a manual flag."""
    if inv.status in (IS.DRAFT, IS.VOID):
        return
    paid = paid_amount(inv)
    total = money(inv.total)
    if paid <= 0:
        inv.status = IS.SENT
    elif paid < total:
        inv.status = IS.PARTIALLY_PAID
    else:
        inv.status = IS.PAID


def search_invoices(db: Session, status: IS | None, client_id: int | None, overdue: bool) -> list[Invoice]:
    q = select(Invoice)
    if status:
        q = q.where(Invoice.status == status)
    if client_id:
        q = q.where(Invoice.client_id == client_id)
    rows = list(db.scalars(q.order_by(Invoice.id.desc()).limit(500)))
    return [i for i in rows if is_overdue(i)] if overdue else rows


# -- payments ----------------------------------------------------------------------------------


def payment_out(db: Session, p: Payment) -> dict[str, Any]:
    inv = db.get(Invoice, p.invoice_id) if p.invoice_id else None
    client = db.get(Client, p.client_id) if p.client_id else None
    return {"id": p.id, "invoice_id": p.invoice_id, "invoice_number": inv.number if inv else None,
            "client_id": p.client_id, "client_name": client.company_name if client else None,
            "amount": m(p.amount), "paid_on": p.paid_on, "method": p.method, "reference": p.reference,
            "created_at": p.created_at}


def record_payment(db: Session, user: User, data: dict[str, Any]) -> Payment:
    amount = money(data["amount"])
    inv = None
    client_id = data.get("client_id")
    if data.get("invoice_id") is not None:
        inv = get_invoice(db, data["invoice_id"])
        if inv.status == IS.DRAFT:
            raise AppError("Mark the invoice as sent before recording a payment against it")
        if inv.status == IS.VOID:
            raise AppError("Payments cannot be recorded against a void invoice")
        balance = money(inv.total) - paid_amount(inv)
        if amount > balance:
            raise AppError(f"Payment exceeds the invoice balance of {m(balance)}")
        if client_id is not None and client_id != inv.client_id:
            raise AppError("The payment's client does not match the invoice")
        client_id = inv.client_id
    else:
        if client_id is None:
            raise AppError("Link the payment to an invoice or a client")
        _client(db, client_id)
    if data["paid_on"] > date.today():
        raise AppError("A payment date cannot be in the future")
    p = Payment(invoice_id=inv.id if inv else None, client_id=client_id, amount=amount, paid_on=data["paid_on"],
                method=data.get("method"), reference=data.get("reference"))
    db.add(p)
    db.flush()
    description = f"Payment for {inv.number}" if inv else (data.get("reference") or "Payment received")
    db.add(Revenue(client_id=client_id, payment_id=p.id, category=data.get("category") or RevenueCategory.RETAINER,
                   description=description[:300], amount=amount, received_on=p.paid_on))
    if inv:
        db.refresh(inv)
        _refresh_status(inv)
    _audit(db, user, "finance.payment_recorded", "payment", p.id,
           f"Payment {m(amount)} recorded" + (f" for {inv.number}; invoice now {inv.status.value.lower()}" if inv else ""))
    return p


def delete_payment(db: Session, user: User, payment_id: int) -> None:
    p = db.get(Payment, payment_id)
    if p is None:
        raise NotFoundError("Payment not found")
    inv = db.get(Invoice, p.invoice_id) if p.invoice_id else None
    for r in db.scalars(select(Revenue).where(Revenue.payment_id == p.id)):
        db.delete(r)
    db.delete(p)
    db.flush()
    if inv:
        db.refresh(inv)
        _refresh_status(inv)
    _audit(db, user, "finance.payment_deleted", "payment", payment_id, f"Payment #{payment_id} ({m(p.amount)}) deleted")


def list_payments(db: Session, start: date | None, end: date | None, client_id: int | None) -> list[Payment]:
    q = select(Payment)
    if start:
        q = q.where(Payment.paid_on >= start)
    if end:
        q = q.where(Payment.paid_on <= end)
    if client_id:
        q = q.where(Payment.client_id == client_id)
    return list(db.scalars(q.order_by(Payment.paid_on.desc(), Payment.id.desc()).limit(1000)))


# -- summary & profitability -------------------------------------------------------------------


def _sum(db: Session, col, *where) -> Decimal:
    return money(db.scalar(select(func.sum(col)).where(*where)))


def summary(db: Session, start: date, end: date) -> dict[str, Any]:
    revenue = _sum(db, Revenue.amount, Revenue.received_on >= start, Revenue.received_on <= end)
    expenses = _sum(db, Expense.amount, Expense.deleted_at.is_(None), Expense.incurred_on >= start, Expense.incurred_on <= end)
    profit = revenue - expenses
    mrr = _sum(db, Client.monthly_fee, Client.deleted_at.is_(None), Client.contract_status == ContractStatus.ACTIVE)
    open_invoices = list(db.scalars(select(Invoice).where(Invoice.status.in_([IS.SENT, IS.PARTIALLY_PAID]))))
    outstanding = money(sum((money(i.total) - paid_amount(i) for i in open_invoices), ZERO))
    overdue = [i for i in open_invoices if is_overdue(i)]
    overdue_amount = money(sum((money(i.total) - paid_amount(i) for i in overdue), ZERO))
    rev_by_cat = dict(db.execute(select(Revenue.category, func.sum(Revenue.amount)).where(
        Revenue.received_on >= start, Revenue.received_on <= end).group_by(Revenue.category)).all())
    exp_by_cat = dict(db.execute(select(Expense.category, func.sum(Expense.amount)).where(
        Expense.deleted_at.is_(None), Expense.incurred_on >= start, Expense.incurred_on <= end).group_by(Expense.category)).all())
    return {
        "start": start, "end": end,
        "total_revenue": m(revenue), "total_expenses": m(expenses), "gross_profit": m(profit),
        "margin_percent": float(round(profit * 100 / revenue, 1)) if revenue > 0 else None,
        "monthly_recurring_revenue": m(mrr), "outstanding": m(outstanding),
        "overdue": m(overdue_amount), "overdue_invoices": len(overdue), "open_invoices": len(open_invoices),
        "all_time_revenue": m(_sum(db, Revenue.amount)),
        "revenue_by_category": {c.value: m(rev_by_cat.get(c, 0)) for c in RevenueCategory},
        "expenses_by_category": {c.value: m(exp_by_cat.get(c, 0)) for c in ExpenseCategory},
    }


def monthly_series(db: Session, months: int = 12, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    keys = []
    y, mo = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{mo:02d}")
        y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
    keys.reverse()
    start = month_range(keys[0])[0]
    rev = {k: ZERO for k in keys}
    exp = {k: ZERO for k in keys}
    for d, a in db.execute(select(Revenue.received_on, Revenue.amount).where(Revenue.received_on >= start)):
        k = f"{d.year:04d}-{d.month:02d}"
        if k in rev:
            rev[k] += money(a)
    for d, a in db.execute(select(Expense.incurred_on, Expense.amount).where(Expense.deleted_at.is_(None), Expense.incurred_on >= start)):
        k = f"{d.year:04d}-{d.month:02d}"
        if k in exp:
            exp[k] += money(a)
    return {"labels": keys, "revenue": [m(rev[k]) for k in keys], "expenses": [m(exp[k]) for k in keys],
            "profit": [m(rev[k] - exp[k]) for k in keys]}


def client_profitability(db: Session, start: date, end: date) -> dict[str, Any]:
    """Estimate per client: revenue − direct AI cost − share of other expenses (allocated by revenue).

    Expenses are not recorded per client, so shared costs are allocated in proportion to each
    client's share of revenue. This is an estimate and is labelled as one.
    """
    revenue_rows = dict(db.execute(select(Revenue.client_id, func.sum(Revenue.amount)).where(
        Revenue.received_on >= start, Revenue.received_on <= end, Revenue.client_id.is_not(None))
        .group_by(Revenue.client_id)).all())
    ai_rows = dict(db.execute(
        select(Project.client_id, func.sum(AgentExecution.estimated_cost))
        .join(AgentTask, AgentTask.id == AgentExecution.task_id)
        .join(Project, Project.id == AgentTask.project_id)
        .where(AgentExecution.started_at >= day_start(start), AgentExecution.started_at < day_start(end + timedelta(days=1)))
        .group_by(Project.client_id)).all())
    total_revenue = _sum(db, Revenue.amount, Revenue.received_on >= start, Revenue.received_on <= end)
    total_expenses = _sum(db, Expense.amount, Expense.deleted_at.is_(None), Expense.incurred_on >= start, Expense.incurred_on <= end)
    client_ids = set(revenue_rows) | set(ai_rows)
    clients = {c.id: c for c in db.scalars(select(Client).where(Client.id.in_(client_ids)))} if client_ids else {}
    rows = []
    for cid in client_ids:
        rev = money(revenue_rows.get(cid))
        ai = money(ai_rows.get(cid))
        share = money(total_expenses * rev / total_revenue) if total_revenue > 0 else ZERO
        profit = rev - ai - share
        c = clients.get(cid)
        rows.append({"client_id": cid, "client_name": c.company_name if c else f"Client #{cid}",
                     "revenue": m(rev), "direct_ai_cost": m(ai), "allocated_expenses": m(share),
                     "estimated_profit": m(profit),
                     "margin_percent": float(round(profit * 100 / rev, 1)) if rev > 0 else None})
    rows.sort(key=lambda r: Decimal(r["estimated_profit"]), reverse=True)
    return {"start": start, "end": end, "method": "Shared expenses allocated by revenue share; AI cost from "
            "agent work linked to the client's projects.", "rows": rows}

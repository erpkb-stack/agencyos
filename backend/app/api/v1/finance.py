from datetime import date
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.session import get_db
from app.models.enums import ExpenseCategory, InvoiceStatus, RevenueCategory
from app.models.identity import User
from app.schemas.finance import (
    AIUsageExpenseIn, ExpenseIn, ExpenseUpdate, InvoiceFromClientIn, InvoiceIn, InvoiceUpdate, PaymentIn, RevenueIn,
    RevenueUpdate,
)
from app.security.deps import get_current_user, require_admin, require_writer
from app.services import finance_service as fs
from app.utils.errors import AppError

router = APIRouter(prefix="/finance", tags=["finance"])

PRINT_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def _period(start: date | None, end: date | None) -> tuple[date, date]:
    today = date.today()
    start = start or today.replace(day=1)
    end = end or today
    if end < start:
        raise AppError("The end date is before the start date")
    return start, end


@router.get("/summary")
def summary(start: date | None = None, end: date | None = None, db: Session = Depends(get_db),
            _: User = Depends(get_current_user)) -> dict[str, Any]:
    s, e = _period(start, end)
    return {**fs.summary(db, s, e), "monthly": fs.monthly_series(db), "profitability": fs.client_profitability(db, s, e)}


# -- revenue -----------------------------------------------------------------------------------


@router.get("/revenue")
def list_revenue(start: date | None = None, end: date | None = None, client_id: int | None = None,
                 category: RevenueCategory | None = None, db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [fs.revenue_out(db, r) for r in fs.list_revenue(db, start, end, client_id, category)]}


@router.post("/revenue", status_code=status.HTTP_201_CREATED)
def add_revenue(body: RevenueIn, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> dict[str, Any]:
    r = fs.create_revenue(db, user, body.model_dump())
    db.commit()
    return fs.revenue_out(db, r)


@router.patch("/revenue/{revenue_id}")
def update_revenue(revenue_id: int, body: RevenueUpdate, db: Session = Depends(get_db),
                   user: User = Depends(require_admin)) -> dict[str, Any]:
    r = fs.update_revenue(db, user, revenue_id, body.model_dump(exclude_unset=True))
    db.commit()
    return fs.revenue_out(db, r)


@router.delete("/revenue/{revenue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_revenue(revenue_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> Response:
    fs.delete_revenue(db, user, revenue_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- expenses ----------------------------------------------------------------------------------


@router.get("/expenses")
def list_expenses(start: date | None = None, end: date | None = None, category: ExpenseCategory | None = None,
                  db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [fs.expense_out(e) for e in fs.list_expenses(db, start, end, category)]}


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def add_expense(body: ExpenseIn, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    e = fs.create_expense(db, user, body.model_dump())
    db.commit()
    return fs.expense_out(e)


@router.patch("/expenses/{expense_id}")
def update_expense(expense_id: int, body: ExpenseUpdate, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> dict[str, Any]:
    e = fs.update_expense(db, user, fs.get_expense(db, expense_id), body.model_dump(exclude_unset=True))
    db.commit()
    return fs.expense_out(e)


@router.delete("/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(expense_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> Response:
    fs.delete_expense(db, user, fs.get_expense(db, expense_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/ai-usage")
def ai_usage(month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), db: Session = Depends(get_db),
             _: User = Depends(get_current_user)) -> dict[str, Any]:
    return fs.ai_usage(db, month)


@router.post("/ai-usage/expense", status_code=status.HTTP_201_CREATED)
def ai_usage_expense(body: AIUsageExpenseIn, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    e = fs.record_ai_expense(db, user, body.month, body.amount, body.vendor)
    db.commit()
    return fs.expense_out(e)


# -- invoices ----------------------------------------------------------------------------------


@router.get("/invoices")
def list_invoices(status_filter: InvoiceStatus | None = Query(None, alias="status"), client_id: int | None = None,
                  overdue: bool = False, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [fs.invoice_out(db, i) for i in fs.search_invoices(db, status_filter, client_id, overdue)]}


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceIn, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    inv = fs.create_invoice(db, user, body.model_dump())
    db.commit()
    return fs.invoice_out(db, inv, full=True)


@router.post("/invoices/from-client/{client_id}", status_code=status.HTTP_201_CREATED)
def invoice_from_client(client_id: int, body: InvoiceFromClientIn, db: Session = Depends(get_db),
                        user: User = Depends(require_writer)) -> dict[str, Any]:
    inv = fs.invoice_from_client(db, user, client_id, body.model_dump())
    db.commit()
    return fs.invoice_out(db, inv, full=True)


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return fs.invoice_out(db, fs.get_invoice(db, invoice_id), full=True)


@router.patch("/invoices/{invoice_id}")
def update_invoice(invoice_id: int, body: InvoiceUpdate, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> dict[str, Any]:
    inv = fs.update_invoice(db, user, fs.get_invoice(db, invoice_id), body.model_dump(exclude_unset=True))
    db.commit()
    return fs.invoice_out(db, inv, full=True)


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> Response:
    fs.delete_invoice(db, user, fs.get_invoice(db, invoice_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invoices/{invoice_id}/mark-sent")
def mark_sent(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    inv = fs.mark_sent(db, user, fs.get_invoice(db, invoice_id))
    db.commit()
    return fs.invoice_out(db, inv, full=True)


@router.post("/invoices/{invoice_id}/void")
def void(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> dict[str, Any]:
    inv = fs.void_invoice(db, user, fs.get_invoice(db, invoice_id))
    db.commit()
    return fs.invoice_out(db, inv, full=True)


@router.get("/invoices/{invoice_id}/print", include_in_schema=False)
def print_invoice(invoice_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                  _: User = Depends(get_current_user)) -> Response:
    """Printable invoice (browser 'Save as PDF'). Every value is HTML-escaped."""
    d = fs.invoice_out(db, fs.get_invoice(db, invoice_id), full=True)
    rows = "".join(f"<tr><td>{escape(i['description'])}</td><td class=n>{i['quantity']}</td><td class=n>{i['unit_price']}</td>"
                   f"<td class=n>{i['amount']}</td></tr>" for i in d["items"])
    agency = escape(settings.agency_name or "[Agency name not set]")
    address = escape(settings.agency_address or "[Agency address not set]")
    draft = "<p class=draft>DRAFT — not sent</p>" if d["status"] == "DRAFT" else ""
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Invoice {escape(d['number'])}</title>
<style>body{{font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#111;max-width:760px;margin:40px auto;padding:0 20px}}
table{{width:100%;border-collapse:collapse;margin:24px 0}}th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}
.n{{text-align:right}}.tot td{{font-weight:700;border-bottom:0}}.draft{{color:#b45309;font-weight:700}}
header{{display:flex;justify-content:space-between;gap:20px}}h1{{margin:0 0 8px}}</style></head><body>
<header><div><h1>Invoice {escape(d['number'])}</h1>{draft}<p>Issued: {d['issue_date'] or '—'}<br>Due: {d['due_date'] or '—'}</p></div>
<div><strong>{agency}</strong><br>{address}</div></header>
<p><strong>Bill to:</strong> {escape(d['client_name'] or '')}</p>
<table><thead><tr><th>Description</th><th class=n>Qty</th><th class=n>Unit price</th><th class=n>Amount</th></tr></thead>
<tbody>{rows}</tbody><tfoot><tr class=tot><td colspan=3 class=n>Total (USD)</td><td class=n>{d['total']}</td></tr>
<tr><td colspan=3 class=n>Paid</td><td class=n>{d['paid']}</td></tr>
<tr class=tot><td colspan=3 class=n>Balance due</td><td class=n>{d['balance']}</td></tr></tfoot></table>
<p>{escape(d['notes'] or '')}</p></body></html>"""
    return Response(html, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": PRINT_CSP})


# -- payments ----------------------------------------------------------------------------------


@router.get("/payments")
def list_payments(start: date | None = None, end: date | None = None, client_id: int | None = None,
                  db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [fs.payment_out(db, p) for p in fs.list_payments(db, start, end, client_id)]}


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def record_payment(body: PaymentIn, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    p = fs.record_payment(db, user, body.model_dump())
    db.commit()
    return fs.payment_out(db, p)


@router.delete("/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payment(payment_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> Response:
    fs.delete_payment(db, user, payment_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

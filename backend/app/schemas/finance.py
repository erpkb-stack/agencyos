"""Request schemas for finance. Money is Decimal with two places; never float."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ExpenseCategory, RevenueCategory

Money = Field(gt=0, max_digits=12, decimal_places=2)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RevenueIn(_In):
    client_id: int | None = None
    category: RevenueCategory
    description: str | None = Field(default=None, max_length=300)
    amount: Decimal = Money
    received_on: date


class RevenueUpdate(_In):
    client_id: int | None = None
    category: RevenueCategory | None = None
    description: str | None = Field(default=None, max_length=300)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    received_on: date | None = None


class ExpenseIn(_In):
    category: ExpenseCategory
    vendor: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=300)
    amount: Decimal = Money
    incurred_on: date
    is_recurring: bool = False


class ExpenseUpdate(_In):
    category: ExpenseCategory | None = None
    vendor: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=300)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    incurred_on: date | None = None
    is_recurring: bool | None = None


class InvoiceItemIn(_In):
    description: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=10, decimal_places=2)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class InvoiceIn(_In):
    client_id: int
    issue_date: date | None = None
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    items: list[InvoiceItemIn] = Field(min_length=1, max_length=50)


class InvoiceUpdate(_In):
    issue_date: date | None = None
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    items: list[InvoiceItemIn] | None = Field(default=None, min_length=1, max_length=50)


class InvoiceFromClientIn(_In):
    include_setup_fee: bool = False
    period_label: str | None = Field(default=None, max_length=60)
    due_in_days: int = Field(default=14, ge=0, le=120)


class PaymentIn(_In):
    invoice_id: int | None = None
    client_id: int | None = None
    amount: Decimal = Money
    paid_on: date
    method: str | None = Field(default=None, max_length=50)
    reference: str | None = Field(default=None, max_length=100)
    category: RevenueCategory = RevenueCategory.RETAINER


class AIUsageExpenseIn(_In):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    amount: Decimal = Money
    vendor: str | None = Field(default=None, max_length=200)

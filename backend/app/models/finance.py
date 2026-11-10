from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin, str_enum
from app.models.enums import ExpenseCategory, InvoiceStatus, RevenueCategory
from app.utils.time import utcnow


class Invoice(IdMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[InvoiceStatus] = mapped_column(
        str_enum(InvoiceStatus, "invoice_status"), default=InvoiceStatus.DRAFT, index=True
    )
    issue_date: Mapped[date | None] = mapped_column(default=None)
    due_date: Mapped[date | None] = mapped_column(default=None)
    subtotal: Mapped[Decimal] = mapped_column(default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(default=Decimal("0.00"))
    notes: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list[InvoiceItem]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.id"
    )
    payments: Mapped[list[Payment]] = relationship(back_populates="invoice")


class InvoiceItem(IdMixin, Base):
    __tablename__ = "invoice_items"

    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("1.00"))
    unit_price: Mapped[Decimal] = mapped_column()
    amount: Mapped[Decimal] = mapped_column()

    invoice: Mapped[Invoice] = relationship(back_populates="items")


class Payment(IdMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"), index=True
    )
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    amount: Mapped[Decimal] = mapped_column()
    paid_on: Mapped[date] = mapped_column()
    method: Mapped[str | None] = mapped_column(String(50))
    reference: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    invoice: Mapped[Invoice | None] = relationship(back_populates="payments")


class Revenue(IdMixin, TimestampMixin, Base):
    __tablename__ = "revenue"
    __table_args__ = (CheckConstraint("amount >= 0", name="amount_nonneg"),)

    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True
    )
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), unique=True
    )
    category: Mapped[RevenueCategory] = mapped_column(str_enum(RevenueCategory, "revenue_category"))
    description: Mapped[str | None] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column()
    received_on: Mapped[date] = mapped_column(index=True)


class Expense(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (CheckConstraint("amount >= 0", name="amount_nonneg"),)

    category: Mapped[ExpenseCategory] = mapped_column(str_enum(ExpenseCategory, "expense_category"))
    vendor: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column()
    incurred_on: Mapped[date] = mapped_column(index=True)
    is_recurring: Mapped[bool] = mapped_column(default=False)

"""
GPA-ERP V5.0 — SQLAlchemy ORM Models
All monetary amounts stored as Numeric(18, 2) mapped to Python Decimal.
"""
import enum
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum as SAEnum, ForeignKey,
    Index, Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, relationship, mapped_column


# ─── Base & Mixin ────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


# ─── Enumerations ────────────────────────────────────────────────────────────

class RoleName(str, enum.Enum):
    SUPER_ADMIN  = "SUPER_ADMIN"
    MD           = "MD"
    PM           = "PM"
    COST_CONTROL = "COST_CONTROL"
    FINANCE      = "FINANCE"
    GA           = "GA"
    STAFF        = "STAFF"


class ProjectStatus(str, enum.Enum):
    ACTIVE    = "active"
    COMPLETED = "completed"
    ON_HOLD   = "on_hold"
    CANCELLED = "cancelled"


class CostCodeCategory(str, enum.Enum):
    DIRECT    = "Direct"
    SITE      = "Site"
    PERSONNEL = "Personnel"
    OVERHEAD  = "Overhead"
    OTHER     = "Other"


class ARStatus(str, enum.Enum):
    DRAFT     = "draft"
    CONFIRMED = "confirmed"


class DocType(str, enum.Enum):
    PROPOSAL     = "proposal"       # Surat Penawaran
    BERITA_ACARA = "berita_acara"   # Completion certificate
    SURAT_JALAN  = "surat_jalan"    # Delivery order
    OTHER        = "other"          # General letter


class DocStatus(str, enum.Enum):
    DRAFT     = "draft"
    SUBMITTED = "submitted"   # awaiting MD / PM signature
    SIGNED    = "signed"      # approved & signed
    REJECTED  = "rejected"


class ExpenseStatus(str, enum.Enum):
    DRAFT       = "draft"
    SUBMITTED   = "submitted"
    VERIFIED    = "verified"     # cost-control stamp
    APPROVED    = "approved"     # all approvers signed off
    PAID        = "paid"         # finance disbursed
    HARD_LOCKED = "hard_locked"  # immutable — closed period
    REJECTED    = "rejected"


class PettyCashReportStatus(str, enum.Enum):
    DRAFT  = "draft"
    POSTED = "posted"
    VOID   = "void"


# ─── Role ────────────────────────────────────────────────────────────────────

class Role(Base):
    __tablename__ = "roles"

    id:   Mapped[int]      = mapped_column(Integer, primary_key=True)
    name: Mapped[RoleName] = mapped_column(SAEnum(RoleName), unique=True, nullable=False)

    users: Mapped[list["User"]] = relationship("User", back_populates="role")

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


# ─── User ────────────────────────────────────────────────────────────────────

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id:             Mapped[int]  = mapped_column(Integer, primary_key=True)
    email:          Mapped[str]  = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password:Mapped[str]  = mapped_column(String(255), nullable=False)
    full_name:      Mapped[str]  = mapped_column(String(255), nullable=False)
    role_id:        Mapped[int]  = mapped_column(ForeignKey("roles.id"), nullable=False)
    is_active:      Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    role: Mapped["Role"] = relationship("Role", back_populates="users")

    # audit trail relations
    submitted_expenses:  Mapped[list["Expense"]] = relationship(
        "Expense", foreign_keys="Expense.submitted_by", back_populates="submitter"
    )
    verified_expenses:   Mapped[list["Expense"]] = relationship(
        "Expense", foreign_keys="Expense.verified_by", back_populates="verifier"
    )
    approved_expenses:   Mapped[list["Expense"]] = relationship(
        "Expense", foreign_keys="Expense.approved_by", back_populates="approver"
    )
    paid_expenses:       Mapped[list["Expense"]] = relationship(
        "Expense", foreign_keys="Expense.paid_by", back_populates="payer"
    )
    confirmed_ars:       Mapped[list["AccountReceivable"]] = relationship(
        "AccountReceivable", foreign_keys="AccountReceivable.confirmed_by",
        back_populates="confirmer"
    )
    petty_cash_reports:   Mapped[list["PettyCashReport"]] = relationship(
        "PettyCashReport", foreign_keys="PettyCashReport.created_by",
        back_populates="creator"
    )
    menu_permissions: Mapped[list["UserMenuPermission"]] = relationship(
        "UserMenuPermission", back_populates="user", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="user", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class AppMenu(Base, TimestampMixin):
    __tablename__ = "app_menus"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True)
    key:         Mapped[str]      = mapped_column(String(80), unique=True, nullable=False, index=True)
    label:       Mapped[str]      = mapped_column(String(120), nullable=False)
    section:     Mapped[str]      = mapped_column(String(80), nullable=False, default="Workspace")
    path:        Mapped[str|None] = mapped_column(String(255), nullable=True)
    description: Mapped[str|None] = mapped_column(Text, nullable=True)
    sort_order:  Mapped[int]      = mapped_column(Integer, nullable=False, default=100)
    is_active:   Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)

    user_permissions: Mapped[list["UserMenuPermission"]] = relationship(
        "UserMenuPermission", back_populates="menu", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_app_menus_section_sort", "section", "sort_order"),
    )


class UserMenuPermission(Base, TimestampMixin):
    __tablename__ = "user_menu_permissions"

    id:         Mapped[int]  = mapped_column(Integer, primary_key=True)
    user_id:    Mapped[int]  = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    menu_id:    Mapped[int]  = mapped_column(ForeignKey("app_menus.id"), nullable=False, index=True)
    can_access: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="menu_permissions")
    menu: Mapped["AppMenu"] = relationship("AppMenu", back_populates="user_permissions")

    __table_args__ = (
        UniqueConstraint("user_id", "menu_id", name="uq_user_menu_permission"),
        Index("ix_user_menu_permission_lookup", "user_id", "menu_id"),
    )


# ─── Project ─────────────────────────────────────────────────────────────────

class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id:             Mapped[int]           = mapped_column(Integer, primary_key=True)
    code:           Mapped[str]           = mapped_column(String(50), unique=True, nullable=False, index=True)
    name:           Mapped[str]           = mapped_column(String(255), nullable=False)
    contract_value: Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    currency:       Mapped[str]           = mapped_column(String(3), nullable=False, default="IDR")
    is_archived:    Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)
    start_date:     Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date:       Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    status:         Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus), nullable=False, default=ProjectStatus.ACTIVE
    )
    imported_at:    Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)

    expenses:    Mapped[list["Expense"]]           = relationship("Expense",           back_populates="project")
    receivables: Mapped[list["AccountReceivable"]] = relationship("AccountReceivable", back_populates="project")
    documents:   Mapped[list["ProjectDocument"]]   = relationship("ProjectDocument",   back_populates="project")
    petty_cash_reports: Mapped[list["PettyCashReport"]] = relationship("PettyCashReport", back_populates="project")

    # ── Revenue-driven budget hybrid properties ───────────────────────────────

    @hybrid_property
    def total_revenue(self) -> Decimal:
        """Sum of all confirmed Account Receivables — the recognised revenue ceiling."""
        return sum(
            (
                ar.actual_payment if ar.actual_payment is not None else ar.amount
                for ar in self.receivables
                if ar.status == ARStatus.CONFIRMED
            ),
            Decimal("0")
        )

    @total_revenue.expression  # type: ignore[no-redef]
    @classmethod
    def total_revenue(cls):
        from sqlalchemy import select
        return (
            select(func.coalesce(func.sum(func.coalesce(AccountReceivable.actual_payment, AccountReceivable.amount)), 0))
            .where(
                AccountReceivable.project_id == cls.id,
                AccountReceivable.status == ARStatus.CONFIRMED,
            )
            .correlate(cls)
            .scalar_subquery()
        )

    @hybrid_property
    def total_committed(self) -> Decimal:
        """Sum of expenses at verified/approved/paid/hard_locked status."""
        committed_statuses = {
            ExpenseStatus.VERIFIED,
            ExpenseStatus.APPROVED,
            ExpenseStatus.PAID,
            ExpenseStatus.HARD_LOCKED,
        }
        return sum(
            (e.amount for e in self.expenses if e.status in committed_statuses),
            Decimal("0")
        )

    @total_committed.expression  # type: ignore[no-redef]
    @classmethod
    def total_committed(cls):
        from sqlalchemy import select
        committed = [
            ExpenseStatus.VERIFIED.value,
            ExpenseStatus.APPROVED.value,
            ExpenseStatus.PAID.value,
            ExpenseStatus.HARD_LOCKED.value,
        ]
        return (
            select(func.coalesce(func.sum(Expense.amount), 0))
            .where(
                Expense.project_id == cls.id,
                Expense.status.in_(committed),
            )
            .correlate(cls)
            .scalar_subquery()
        )

    @hybrid_property
    def budget(self) -> Decimal:
        """Revenue-driven budget: available = total_revenue - total_committed."""
        return self.total_revenue - self.total_committed

    @budget.expression  # type: ignore[no-redef]
    @classmethod
    def budget(cls):
        return cls.total_revenue - cls.total_committed

    def __repr__(self) -> str:
        return f"<Project {self.code} — {self.name}>"


# ─── CostCode ────────────────────────────────────────────────────────────────

class CostCode(Base, TimestampMixin):
    __tablename__ = "cost_codes"

    id:        Mapped[int]              = mapped_column(Integer, primary_key=True)
    code:      Mapped[str]              = mapped_column(String(50), unique=True, nullable=False, index=True)
    name:      Mapped[str]              = mapped_column(String(255), nullable=False)
    parent_id: Mapped[int|None]         = mapped_column(ForeignKey("cost_codes.id"), nullable=True)
    category:  Mapped[CostCodeCategory] = mapped_column(SAEnum(CostCodeCategory), nullable=False)
    is_active: Mapped[bool]             = mapped_column(Boolean, default=True, nullable=False)

    parent:   Mapped["CostCode|None"]  = relationship("CostCode", remote_side="CostCode.id", back_populates="children")
    children: Mapped[list["CostCode"]] = relationship("CostCode", back_populates="parent")
    expenses: Mapped[list["Expense"]]  = relationship("Expense", back_populates="cost_code")
    petty_cash_reports: Mapped[list["PettyCashReport"]] = relationship("PettyCashReport", back_populates="cost_code")

    def __repr__(self) -> str:
        return f"<CostCode {self.code} — {self.name}>"


# ─── ApprovalRule ────────────────────────────────────────────────────────────

class CostCentre(Base, TimestampMixin):
    __tablename__ = "cost_centres"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True)
    code:        Mapped[str]      = mapped_column(String(50), unique=True, nullable=False, index=True)
    name:        Mapped[str]      = mapped_column(String(255), nullable=False)
    description: Mapped[str|None] = mapped_column(Text, nullable=True)
    is_active:   Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)

    expenses: Mapped[list["Expense"]] = relationship("Expense", back_populates="cost_centre")
    petty_cash_reports: Mapped[list["PettyCashReport"]] = relationship("PettyCashReport", back_populates="cost_centre")


class ProjectDocument(Base, TimestampMixin):
    __tablename__ = "project_documents"

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True)
    project_id:   Mapped[int]      = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    doc_type:     Mapped[str]      = mapped_column(String(50), nullable=False, default="contract")
    title:        Mapped[str]      = mapped_column(String(255), nullable=False)
    file_path:    Mapped[str]      = mapped_column(String(2048), nullable=False)
    reference_no: Mapped[str|None] = mapped_column(String(100), nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="documents")

    __table_args__ = (
        Index("ix_project_docs_project_type", "project_id", "doc_type"),
    )


class ApprovalRule(Base, TimestampMixin):
    """
    Defines one step in the multi-level approval chain.
    Multiple rules can match a single expense; they are applied in `priority` order.
    Null cost_code_category → matches any category.
    Null max_amount → no upper bound.
    """
    __tablename__ = "approval_rules"

    id:                   Mapped[int]                  = mapped_column(Integer, primary_key=True)
    min_amount:           Mapped[Decimal]              = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    max_amount:           Mapped[Decimal|None]         = mapped_column(Numeric(18, 2), nullable=True)
    cost_code_category:   Mapped[CostCodeCategory|None]= mapped_column(SAEnum(CostCodeCategory), nullable=True)
    required_role:        Mapped[RoleName]             = mapped_column(SAEnum(RoleName), nullable=False)
    priority:             Mapped[int]                  = mapped_column(Integer, nullable=False, default=1)
    is_active:            Mapped[bool]                 = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("ix_approval_rules_priority", "priority"),
        Index("ix_approval_rules_active",   "is_active"),
    )

    def __repr__(self) -> str:
        return (
            f"<ApprovalRule {self.required_role.value} "
            f"[{self.min_amount}–{self.max_amount or '∞'}] p={self.priority}>"
        )


# ─── AccountReceivable ───────────────────────────────────────────────────────

class AccountReceivable(Base, TimestampMixin):
    __tablename__ = "account_receivables"

    id:           Mapped[int]        = mapped_column(Integer, primary_key=True)
    project_id:   Mapped[int]        = mapped_column(ForeignKey("projects.id"), nullable=False)
    amount:       Mapped[Decimal]    = mapped_column(Numeric(18, 2), nullable=False)
    description:  Mapped[str]        = mapped_column(Text, nullable=False)
    invoice_no:   Mapped[str|None]   = mapped_column(String(100), nullable=True, index=True)
    customer_name: Mapped[str|None]  = mapped_column(String(255), nullable=True)
    invoice_date: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_date:     Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_payment: Mapped[Decimal|None] = mapped_column(Numeric(18, 2), nullable=True)
    actual_payment:   Mapped[Decimal|None] = mapped_column(Numeric(18, 2), nullable=True)
    remaining_amount: Mapped[Decimal|None] = mapped_column(Numeric(18, 2), nullable=True)
    paid_at:      Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    status:       Mapped[ARStatus]   = mapped_column(
        SAEnum(ARStatus), nullable=False, default=ARStatus.DRAFT
    )
    confirmed_by: Mapped[int|None]   = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)

    project:   Mapped["Project"] = relationship("Project", back_populates="receivables")
    confirmer: Mapped["User|None"] = relationship(
        "User", foreign_keys=[confirmed_by], back_populates="confirmed_ars"
    )

    __table_args__ = (
        Index("ix_ar_project_status", "project_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<AR id={self.id} project={self.project_id} {self.status}>"


# ─── Expense ─────────────────────────────────────────────────────────────────

class Expense(Base, TimestampMixin):
    """
    Full approval workflow:
      draft → submitted → verified → approved → paid → hard_locked
    approval_chain  : ordered list of RoleName values e.g. ["COST_CONTROL","PM","MD"]
    approval_step   : index into approval_chain pointing to the *current* required approver
    approval_history: list of {role, user_id, action, timestamp, note} records
    """
    __tablename__ = "expenses"

    id:              Mapped[int]           = mapped_column(Integer, primary_key=True)
    project_id:      Mapped[int]           = mapped_column(ForeignKey("projects.id"), nullable=False)
    cost_code_id:    Mapped[int]           = mapped_column(ForeignKey("cost_codes.id"), nullable=False)
    cost_centre_id:  Mapped[int|None]      = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    petty_cash_line_id: Mapped[int|None]   = mapped_column(ForeignKey("petty_cash_report_lines.id"), nullable=True)
    amount:          Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False)
    description:     Mapped[str]           = mapped_column(Text, nullable=False)
    vendor_name:     Mapped[str|None]      = mapped_column(String(255), nullable=True)
    reference_no:    Mapped[str|None]      = mapped_column(String(100), nullable=True)
    receipt_url:     Mapped[str|None]      = mapped_column(String(2048), nullable=True)
    status:          Mapped[ExpenseStatus] = mapped_column(
        SAEnum(ExpenseStatus), nullable=False, default=ExpenseStatus.DRAFT
    )

    # Actor FKs
    submitted_by:    Mapped[int|None]      = mapped_column(ForeignKey("users.id"), nullable=True)
    verified_by:     Mapped[int|None]      = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by:     Mapped[int|None]      = mapped_column(ForeignKey("users.id"), nullable=True)
    paid_by:         Mapped[int|None]      = mapped_column(ForeignKey("users.id"), nullable=True)

    # Approval chain tracking
    current_approver_role: Mapped[str|None] = mapped_column(String(50), nullable=True)
    approval_chain:        Mapped[list|None]= mapped_column(JSONB, nullable=True, default=list)
    approval_step:         Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    approval_history:      Mapped[list|None]= mapped_column(JSONB, nullable=True, default=list)
    rejection_reason:      Mapped[str|None] = mapped_column(Text, nullable=True)

    # Relationships
    project:   Mapped["Project"]   = relationship("Project",   back_populates="expenses")
    cost_code: Mapped["CostCode"]  = relationship("CostCode",  back_populates="expenses")
    cost_centre: Mapped["CostCentre|None"] = relationship("CostCentre", back_populates="expenses")
    petty_cash_line: Mapped["PettyCashReportLine|None"] = relationship(
        "PettyCashReportLine", back_populates="expense", foreign_keys=[petty_cash_line_id]
    )
    submitter: Mapped["User|None"] = relationship("User", foreign_keys=[submitted_by], back_populates="submitted_expenses")
    verifier:  Mapped["User|None"] = relationship("User", foreign_keys=[verified_by],  back_populates="verified_expenses")
    approver:  Mapped["User|None"] = relationship("User", foreign_keys=[approved_by],  back_populates="approved_expenses")
    payer:     Mapped["User|None"] = relationship("User", foreign_keys=[paid_by],       back_populates="paid_expenses")

    __table_args__ = (
        Index("ix_expenses_project_status",  "project_id", "status"),
        Index("ix_expenses_current_approver","current_approver_role"),
        Index("ix_expenses_submitted_by",    "submitted_by"),
        Index("ix_expenses_petty_cash_line", "petty_cash_line_id"),
    )

    def __repr__(self) -> str:
        return f"<Expense id={self.id} ₱{self.amount} {self.status}>"


class PettyCashReport(Base, TimestampMixin):
    __tablename__ = "petty_cash_reports"

    id:             Mapped[int]                   = mapped_column(Integer, primary_key=True)
    report_no:      Mapped[str]                   = mapped_column(String(100), unique=True, nullable=False, index=True)
    month:          Mapped[str]                   = mapped_column(String(7), nullable=False, index=True)
    project_id:     Mapped[int]                   = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    cost_code_id:   Mapped[int]                   = mapped_column(ForeignKey("cost_codes.id"), nullable=False)
    cost_centre_id: Mapped[int|None]              = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    title:          Mapped[str|None]              = mapped_column(String(255), nullable=True)
    notes:          Mapped[str|None]              = mapped_column(Text, nullable=True)
    status:         Mapped[PettyCashReportStatus] = mapped_column(
        SAEnum(PettyCashReportStatus), nullable=False, default=PettyCashReportStatus.DRAFT
    )
    total_amount:   Mapped[Decimal]               = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    created_by:     Mapped[int]                   = mapped_column(ForeignKey("users.id"), nullable=False)
    posted_at:      Mapped[datetime|None]         = mapped_column(DateTime(timezone=True), nullable=True)

    project:     Mapped["Project"]         = relationship("Project", back_populates="petty_cash_reports")
    cost_code:   Mapped["CostCode"]        = relationship("CostCode", back_populates="petty_cash_reports")
    cost_centre: Mapped["CostCentre|None"] = relationship("CostCentre", back_populates="petty_cash_reports")
    creator:     Mapped["User"]            = relationship("User", foreign_keys=[created_by], back_populates="petty_cash_reports")
    lines:       Mapped[list["PettyCashReportLine"]] = relationship(
        "PettyCashReportLine", back_populates="report", cascade="all, delete-orphan",
        order_by="PettyCashReportLine.line_no"
    )

    __table_args__ = (
        Index("ix_petty_cash_reports_project_month", "project_id", "month"),
        Index("ix_petty_cash_reports_status", "status"),
    )


class PettyCashReportLine(Base, TimestampMixin):
    __tablename__ = "petty_cash_report_lines"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True)
    report_id:   Mapped[int]           = mapped_column(ForeignKey("petty_cash_reports.id"), nullable=False, index=True)
    line_no:     Mapped[int]           = mapped_column(Integer, nullable=False)
    spent_on:    Mapped[date|None]     = mapped_column(Date, nullable=True)
    description: Mapped[str]           = mapped_column(Text, nullable=False)
    amount:      Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False)
    receipt_url: Mapped[str|None]      = mapped_column(String(2048), nullable=True)
    source:      Mapped[str|None]      = mapped_column(String(50), nullable=True)
    ocr_text:    Mapped[str|None]      = mapped_column(Text, nullable=True)

    report:  Mapped["PettyCashReport"] = relationship("PettyCashReport", back_populates="lines")
    expense: Mapped["Expense|None"] = relationship(
        "Expense", back_populates="petty_cash_line",
        foreign_keys="Expense.petty_cash_line_id", uselist=False
    )

    @property
    def expense_id(self) -> int | None:
        return self.expense.id if self.expense else None

    __table_args__ = (
        Index("ix_petty_cash_lines_report_line", "report_id", "line_no"),
    )


# ─── LegalDocument ───────────────────────────────────────────────────────────

class LegalDocument(Base, TimestampMixin):
    """
    Proposal / offering letters generated on the company KOP SURAT.
    Workflow: draft → submitted → signed | rejected
    Only MD or PM can sign. Staff can only create & submit.
    """
    __tablename__ = "legal_documents"

    id:                 Mapped[int]           = mapped_column(Integer, primary_key=True)
    doc_number:         Mapped[str|None]      = mapped_column(String(100), unique=True, nullable=True, index=True)
    reference_number:   Mapped[str|None]      = mapped_column(String(100), nullable=True, index=True)
    doc_type:           Mapped[DocType]       = mapped_column(SAEnum(DocType), nullable=False)
    status:             Mapped[DocStatus]     = mapped_column(SAEnum(DocStatus), nullable=False, default=DocStatus.DRAFT)

    # Content fields
    title:              Mapped[str]           = mapped_column(String(500), nullable=False)
    recipient_name:     Mapped[str|None]      = mapped_column(String(255), nullable=True)
    recipient_company:  Mapped[str|None]      = mapped_column(String(255), nullable=True)
    recipient_address:  Mapped[str|None]      = mapped_column(Text, nullable=True)
    subject:            Mapped[str]           = mapped_column(String(500), nullable=False)
    body:               Mapped[str]           = mapped_column(Text, nullable=False)
    closing:            Mapped[str|None]      = mapped_column(Text, nullable=True)
    quoted_amount:      Mapped[Decimal|None]  = mapped_column(Numeric(18, 2), nullable=True)

    # Project reference (optional)
    project_id:         Mapped[int|None]      = mapped_column(ForeignKey("projects.id"), nullable=True)

    # Approval
    rejection_note:     Mapped[str|None]      = mapped_column(Text, nullable=True)
    signed_by:          Mapped[int|None]      = mapped_column(ForeignKey("users.id"), nullable=True)
    signed_at:          Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Author
    created_by:         Mapped[int]           = mapped_column(ForeignKey("users.id"), nullable=False)

    # Relationships
    project:  Mapped["Project|None"] = relationship("Project",  foreign_keys=[project_id])
    creator:  Mapped["User"]         = relationship("User",     foreign_keys=[created_by])
    signer:   Mapped["User|None"]    = relationship("User",     foreign_keys=[signed_by])

    __table_args__ = (
        Index("ix_legal_docs_status",     "status"),
        Index("ix_legal_docs_created_by", "created_by"),
        Index("ix_legal_docs_type",       "doc_type"),
    )

    def __repr__(self) -> str:
        return f"<LegalDocument {self.doc_number} {self.doc_type} {self.status}>"


# ─── Inventory ───────────────────────────────────────────────────────────────

class ItemCategory(str, enum.Enum):
    MATERIALS    = "materials"
    TOOLS        = "tools"
    CONSUMABLES  = "consumables"


class TxnType(str, enum.Enum):
    IN         = "in"
    OUT        = "out"
    ADJUSTMENT = "adjustment"


class InventoryItem(Base, TimestampMixin):
    __tablename__ = "inventory_items"

    id:            Mapped[int]           = mapped_column(Integer, primary_key=True)
    code:          Mapped[str]           = mapped_column(String(50), unique=True, nullable=False, index=True)
    name:          Mapped[str]           = mapped_column(String(255), nullable=False)
    category:      Mapped[ItemCategory]  = mapped_column(SAEnum(ItemCategory, values_callable=lambda x: [e.value for e in x]), nullable=False)
    unit:          Mapped[str]           = mapped_column(String(50), nullable=False, default="pcs")
    qty_on_hand:   Mapped[Decimal]       = mapped_column(Numeric(18, 3), nullable=False, default=Decimal("0"))
    min_stock:     Mapped[Decimal]       = mapped_column(Numeric(18, 3), nullable=False, default=Decimal("0"))
    unit_cost:     Mapped[Decimal|None]  = mapped_column(Numeric(18, 2), nullable=True)
    location:      Mapped[str|None]      = mapped_column(String(255), nullable=True)
    notes:         Mapped[str|None]      = mapped_column(Text, nullable=True)
    is_active:     Mapped[bool]          = mapped_column(Boolean, default=True, nullable=False)

    transactions:  Mapped[list["InventoryTxn"]] = relationship("InventoryTxn", back_populates="item")

    __table_args__ = (
        Index("ix_inv_items_category", "category"),
        Index("ix_inv_items_active",   "is_active"),
    )

    def __repr__(self) -> str:
        return f"<InventoryItem {self.code} qty={self.qty_on_hand}>"


class InventoryTxn(Base):
    __tablename__ = "inventory_txns"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True)
    item_id:     Mapped[int]           = mapped_column(ForeignKey("inventory_items.id"), nullable=False, index=True)
    txn_type:    Mapped[TxnType]       = mapped_column(SAEnum(TxnType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    quantity:    Mapped[Decimal]       = mapped_column(Numeric(18, 3), nullable=False)
    reference:   Mapped[str|None]      = mapped_column(String(255), nullable=True)
    notes:       Mapped[str|None]      = mapped_column(Text, nullable=True)
    project_id:  Mapped[int|None]      = mapped_column(ForeignKey("projects.id"), nullable=True)
    created_by:  Mapped[int]           = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    item:    Mapped["InventoryItem"] = relationship("InventoryItem", back_populates="transactions")
    project: Mapped["Project|None"] = relationship("Project",       foreign_keys=[project_id])
    creator: Mapped["User"]         = relationship("User",          foreign_keys=[created_by])

    __table_args__ = (
        Index("ix_inv_txns_item",    "item_id"),
        Index("ix_inv_txns_project", "project_id"),
    )


# ─── AuditLog ────────────────────────────────────────────────────────────────

class AuditLog(Base):
    """Immutable append-only audit trail. Never update or delete rows."""
    __tablename__ = "audit_logs"

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True)
    entity_type:  Mapped[str]      = mapped_column(String(100), nullable=False)
    entity_id:    Mapped[int]      = mapped_column(Integer, nullable=False)
    action:       Mapped[str]      = mapped_column(String(100), nullable=False)
    before_state: Mapped[dict|None]= mapped_column(JSONB, nullable=True)
    after_state:  Mapped[dict|None]= mapped_column(JSONB, nullable=True)
    changed_by:   Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ip_address:   Mapped[str|None] = mapped_column(String(45), nullable=True)
    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_entity",    "entity_type", "entity_id"),
        Index("ix_audit_changed_by","changed_by"),
        Index("ix_audit_created_at","created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.entity_type}#{self.entity_id} {self.action}>"


# ─── Notification ─────────────────────────────────────────────────────────────

class Notification(Base):
    __tablename__ = "notifications"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True)
    user_id:    Mapped[int]      = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title:      Mapped[str]      = mapped_column(String(200), nullable=False)
    body:       Mapped[str]      = mapped_column(String(500), nullable=False)
    link:       Mapped[str|None] = mapped_column(String(500), nullable=True)
    is_read:    Mapped[bool]     = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship("User", back_populates="notifications")

    def __repr__(self) -> str:
        return f"<Notification {self.id} user={self.user_id} read={self.is_read}>"


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS — Human Resource Information System
# ═══════════════════════════════════════════════════════════════════════════════

# ─── HRIS Enumerations ────────────────────────────────────────────────────────

class EmploymentType(str, enum.Enum):
    TETAP     = "Tetap"       # Permanent (PKWTT)
    PKWT      = "PKWT"        # Fixed-term contract
    OUTSOURCE = "Outsource"   # Third-party outsourced


class EmployeeStatus(str, enum.Enum):
    ACTIVE      = "active"
    PROBATION   = "probation"
    LEAVE       = "leave"         # extended leave
    TERMINATED  = "terminated"


class EmpDocType(str, enum.Enum):
    KTP      = "KTP"
    NPWP     = "NPWP"
    BPJS_TK  = "BPJS_TK"
    BPJS_KES = "BPJS_KES"
    IJAZAH   = "IJAZAH"
    SKCK     = "SKCK"
    OTHER    = "OTHER"


# ─── Department ───────────────────────────────────────────────────────────────

class Department(Base, TimestampMixin):
    __tablename__ = "hris_departments"

    id:        Mapped[int]      = mapped_column(Integer, primary_key=True)
    code:      Mapped[str]      = mapped_column(String(50), unique=True, nullable=False, index=True)
    name:      Mapped[str]      = mapped_column(String(255), nullable=False)
    parent_id: Mapped[int|None] = mapped_column(ForeignKey("hris_departments.id"), nullable=True)
    is_active: Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)

    parent:   Mapped["Department|None"]  = relationship("Department", remote_side="Department.id", back_populates="children")
    children: Mapped[list["Department"]] = relationship("Department", back_populates="parent")
    employees: Mapped[list["Employee"]]  = relationship("Employee", back_populates="department")

    def __repr__(self) -> str:
        return f"<Department {self.code}>"


# ─── JobGrade ─────────────────────────────────────────────────────────────────

class JobGrade(Base, TimestampMixin):
    __tablename__ = "hris_job_grades"

    id:        Mapped[int]  = mapped_column(Integer, primary_key=True)
    code:      Mapped[str]  = mapped_column(String(50), unique=True, nullable=False, index=True)
    name:      Mapped[str]  = mapped_column(String(255), nullable=False)
    level:     Mapped[int]  = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    employees: Mapped[list["Employee"]] = relationship("Employee", back_populates="grade")

    def __repr__(self) -> str:
        return f"<JobGrade {self.code} L{self.level}>"


# ─── Employee ─────────────────────────────────────────────────────────────────

class Employee(Base, TimestampMixin):
    __tablename__ = "hris_employees"

    id:             Mapped[int]            = mapped_column(Integer, primary_key=True)
    employee_no:    Mapped[str]            = mapped_column(String(50), unique=True, nullable=False, index=True)
    full_name:      Mapped[str]            = mapped_column(String(255), nullable=False)
    nik:            Mapped[str|None]       = mapped_column(String(16), unique=True, nullable=True)
    npwp:           Mapped[str|None]       = mapped_column(String(20), nullable=True)
    email:          Mapped[str|None]       = mapped_column(String(320), nullable=True)
    phone:          Mapped[str|None]       = mapped_column(String(20), nullable=True)
    tipe:           Mapped[EmploymentType] = mapped_column(
        SAEnum(EmploymentType, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    status:         Mapped[EmployeeStatus] = mapped_column(
        SAEnum(EmployeeStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=EmployeeStatus.ACTIVE
    )
    dept_id:        Mapped[int|None]       = mapped_column(ForeignKey("hris_departments.id"), nullable=True)
    grade_id:       Mapped[int|None]       = mapped_column(ForeignKey("hris_job_grades.id"), nullable=True)
    site:           Mapped[str|None]       = mapped_column(String(255), nullable=True)
    join_date:      Mapped[date|None]      = mapped_column(Date, nullable=True)
    end_date:       Mapped[date|None]      = mapped_column(Date, nullable=True)
    bank_name:      Mapped[str|None]       = mapped_column(String(100), nullable=True)
    bank_account:   Mapped[str|None]       = mapped_column(String(50), nullable=True)
    bpjs_tk_no:     Mapped[str|None]       = mapped_column(String(30), nullable=True)
    bpjs_kes_no:    Mapped[str|None]       = mapped_column(String(30), nullable=True)
    user_id:        Mapped[int|None]       = mapped_column(ForeignKey("users.id"), unique=True, nullable=True)
    photo_url:      Mapped[str|None]       = mapped_column(String(500), nullable=True)
    # Face recognition embedding (JSONB list of floats)
    face_embedding: Mapped[dict|None]      = mapped_column(JSONB, nullable=True)

    department: Mapped["Department|None"]       = relationship("Department", back_populates="employees")
    grade:      Mapped["JobGrade|None"]         = relationship("JobGrade", back_populates="employees")
    user:       Mapped["User|None"]             = relationship("User", foreign_keys=[user_id])
    documents:  Mapped[list["EmployeeDocument"]] = relationship(
        "EmployeeDocument", back_populates="employee", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_hris_employees_dept",   "dept_id"),
        Index("ix_hris_employees_status", "status"),
        Index("ix_hris_employees_tipe",   "tipe"),
    )

    def __repr__(self) -> str:
        return f"<Employee {self.employee_no} {self.full_name}>"


# ─── EmployeeDocument ─────────────────────────────────────────────────────────

class EmployeeDocument(Base, TimestampMixin):
    __tablename__ = "hris_employee_documents"

    id:          Mapped[int]        = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int]        = mapped_column(ForeignKey("hris_employees.id"), nullable=False, index=True)
    doc_type:    Mapped[EmpDocType] = mapped_column(
        SAEnum(EmpDocType, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    file_url:    Mapped[str]        = mapped_column(String(500), nullable=False)
    uploaded_at: Mapped[datetime]   = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    employee: Mapped["Employee"] = relationship("Employee", back_populates="documents")

    def __repr__(self) -> str:
        return f"<EmployeeDocument {self.doc_type} emp={self.employee_id}>"


# ─── HRIS H2 Enumerations ─────────────────────────────────────────────────────

class AttendanceSource(str, enum.Enum):
    MANUAL       = "manual"
    MOBILE       = "mobile"        # geolocation + selfie clock-in
    FINGERPRINT  = "fingerprint"
    IMPORT       = "import"


class LeaveRequestStatus(str, enum.Enum):
    DRAFT     = "draft"
    SUBMITTED = "submitted"
    APPROVED  = "approved"
    REJECTED  = "rejected"


# ─── AttendanceRecord ─────────────────────────────────────────────────────────

class AttendanceRecord(Base, TimestampMixin):
    __tablename__ = "hris_attendance_records"

    id:                     Mapped[int]              = mapped_column(Integer, primary_key=True)
    employee_id:            Mapped[int]              = mapped_column(ForeignKey("hris_employees.id"), nullable=False, index=True)
    date:                   Mapped[date]             = mapped_column(Date, nullable=False)
    clock_in:               Mapped[datetime|None]    = mapped_column(DateTime(timezone=True), nullable=True)
    clock_out:              Mapped[datetime|None]    = mapped_column(DateTime(timezone=True), nullable=True)
    hours_regular:          Mapped[Decimal|None]     = mapped_column(Numeric(5, 2), nullable=True)
    hours_overtime_weekday: Mapped[Decimal|None]     = mapped_column(Numeric(5, 2), nullable=True)
    hours_overtime_weekend: Mapped[Decimal|None]     = mapped_column(Numeric(5, 2), nullable=True)
    hours_overtime_holiday: Mapped[Decimal|None]     = mapped_column(Numeric(5, 2), nullable=True)
    source:                 Mapped[AttendanceSource] = mapped_column(
        SAEnum(AttendanceSource, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=AttendanceSource.MANUAL
    )
    # Geolocation (clock-in)
    latitude:               Mapped[Decimal|None]     = mapped_column(Numeric(9, 6), nullable=True)
    longitude:              Mapped[Decimal|None]     = mapped_column(Numeric(9, 6), nullable=True)
    accuracy:               Mapped[Decimal|None]     = mapped_column(Numeric(10, 2), nullable=True)  # metres
    # Selfie / face verification
    selfie_url:             Mapped[str|None]         = mapped_column(String(500), nullable=True)
    face_verified:          Mapped[bool]             = mapped_column(Boolean, nullable=False, default=False)
    face_confidence:        Mapped[Decimal|None]     = mapped_column(Numeric(4, 3), nullable=True)  # 0.000–1.000
    note:                   Mapped[str|None]         = mapped_column(Text, nullable=True)

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        UniqueConstraint("employee_id", "date", name="uq_attendance_emp_date"),
        Index("ix_attendance_date",     "date"),
        Index("ix_attendance_employee", "employee_id"),
    )

    def __repr__(self) -> str:
        return f"<AttendanceRecord emp={self.employee_id} date={self.date}>"


# ─── LeaveType ────────────────────────────────────────────────────────────────

class LeaveType(Base, TimestampMixin):
    __tablename__ = "hris_leave_types"

    id:                  Mapped[int]      = mapped_column(Integer, primary_key=True)
    code:                Mapped[str]      = mapped_column(String(50), unique=True, nullable=False, index=True)
    name:                Mapped[str]      = mapped_column(String(255), nullable=False)
    max_days_per_year:   Mapped[int|None] = mapped_column(Integer, nullable=True)   # null = unlimited
    is_paid:             Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)
    requires_approval:   Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)
    is_active:           Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)

    balances:  Mapped[list["LeaveBalance"]]  = relationship("LeaveBalance",  back_populates="leave_type")
    requests:  Mapped[list["LeaveRequest"]]  = relationship("LeaveRequest",  back_populates="leave_type")

    def __repr__(self) -> str:
        return f"<LeaveType {self.code}>"


# ─── LeaveBalance ─────────────────────────────────────────────────────────────

class LeaveBalance(Base, TimestampMixin):
    __tablename__ = "hris_leave_balances"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id:   Mapped[int] = mapped_column(ForeignKey("hris_employees.id"), nullable=False, index=True)
    leave_type_id: Mapped[int] = mapped_column(ForeignKey("hris_leave_types.id"), nullable=False, index=True)
    year:          Mapped[int] = mapped_column(Integer, nullable=False)
    accrued:       Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # days granted
    used:          Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # days taken

    employee:   Mapped["Employee"]  = relationship("Employee",  foreign_keys=[employee_id])
    leave_type: Mapped["LeaveType"] = relationship("LeaveType", back_populates="balances")

    __table_args__ = (
        UniqueConstraint("employee_id", "leave_type_id", "year", name="uq_leave_balance"),
        Index("ix_leave_balance_emp_year", "employee_id", "year"),
    )

    @property
    def remaining(self) -> int:
        return max(0, self.accrued - self.used)

    def __repr__(self) -> str:
        return f"<LeaveBalance emp={self.employee_id} type={self.leave_type_id} year={self.year}>"


# ─── LeaveRequest ─────────────────────────────────────────────────────────────

class LeaveRequest(Base, TimestampMixin):
    __tablename__ = "hris_leave_requests"

    id:                    Mapped[int]               = mapped_column(Integer, primary_key=True)
    employee_id:           Mapped[int]               = mapped_column(ForeignKey("hris_employees.id"), nullable=False, index=True)
    leave_type_id:         Mapped[int]               = mapped_column(ForeignKey("hris_leave_types.id"), nullable=False)
    start_date:            Mapped[date]              = mapped_column(Date, nullable=False)
    end_date:              Mapped[date]              = mapped_column(Date, nullable=False)
    days:                  Mapped[int]               = mapped_column(Integer, nullable=False)
    reason:                Mapped[str|None]          = mapped_column(Text, nullable=True)
    status:                Mapped[LeaveRequestStatus] = mapped_column(
        SAEnum(LeaveRequestStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=LeaveRequestStatus.DRAFT
    )
    approval_chain:        Mapped[list|None]         = mapped_column(JSONB, nullable=True, default=list)
    approval_step:         Mapped[int]               = mapped_column(Integer, nullable=False, default=0)
    current_approver_role: Mapped[str|None]          = mapped_column(String(50), nullable=True)
    approval_history:      Mapped[list|None]         = mapped_column(JSONB, nullable=True, default=list)
    submitted_by:          Mapped[int]               = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by:           Mapped[int|None]          = mapped_column(ForeignKey("users.id"), nullable=True)

    employee:   Mapped["Employee"]   = relationship("Employee",  foreign_keys=[employee_id])
    leave_type: Mapped["LeaveType"]  = relationship("LeaveType", back_populates="requests")
    submitter:  Mapped["User"]       = relationship("User", foreign_keys=[submitted_by])
    approver:   Mapped["User|None"]  = relationship("User", foreign_keys=[approved_by])

    __table_args__ = (
        Index("ix_leave_requests_emp",    "employee_id"),
        Index("ix_leave_requests_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<LeaveRequest emp={self.employee_id} {self.start_date}–{self.end_date}>"

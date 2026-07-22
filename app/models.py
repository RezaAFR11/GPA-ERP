"""
GPA-ERP V5.0 — SQLAlchemy ORM Models
All monetary amounts stored as Numeric(18, 2) mapped to Python Decimal.
"""
import enum
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum as SAEnum, ForeignKey,
    Index, Integer, Numeric, String, Text, UniqueConstraint, func, text,
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
    SUPER_ADMIN     = "SUPER_ADMIN"
    MD              = "MD"
    PM              = "PM"
    PROJECT_CONTROL = "PROJECT_CONTROL"  # mirrors PM access
    COST_CONTROL    = "COST_CONTROL"
    FINANCE         = "FINANCE"
    GA              = "GA"
    HR              = "HR"                # mirrors GA access
    STAFF           = "STAFF"
    WORKER          = "WORKER"   # Site/field worker — HRIS self-service only


# ── Role aliases ──────────────────────────────────────────────────────────────
# A role that inherits ALL access from another role. Authorization checks treat
# the alias role as its target (e.g. HR is allowed wherever GA is allowed).
ROLE_ALIASES: "dict[RoleName, RoleName]" = {
    RoleName.HR: RoleName.GA,
    RoleName.PROJECT_CONTROL: RoleName.PM,
}


def effective_roles(role: "RoleName") -> "tuple[RoleName, ...]":
    """The role itself plus the role it inherits access from (if any)."""
    alias = ROLE_ALIASES.get(role)
    return (role, alias) if alias else (role,)


def roles_inheriting(role: "RoleName") -> "tuple[RoleName, ...]":
    """The role itself plus any role that aliases to it (notification fan-out)."""
    return (role, *[r for r, target in ROLE_ALIASES.items() if target == role])


class ProjectStatus(str, enum.Enum):
    ACTIVE    = "active"
    COMPLETED = "completed"
    ON_HOLD   = "on_hold"
    CANCELLED = "cancelled"


class CostCodeCategory(str, enum.Enum):
    DIRECT        = "Direct"
    SITE          = "Site"
    PERSONNEL     = "Personnel"
    OVERHEAD      = "Overhead"
    OTHER         = "Other"
    REIMBURSEMENT = "Reimbursement"


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


class ExpenseType(str, enum.Enum):
    REGULAR       = "regular"        # standard project-linked expense
    REIMBURSEMENT = "reimbursement"  # personal out-of-pocket claim (staff/worker)


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
    email:          Mapped[str]  = mapped_column(String(320), nullable=False)
    hashed_password:Mapped[str]  = mapped_column(String(255), nullable=False)
    full_name:      Mapped[str]  = mapped_column(String(255), nullable=False)
    role_id:        Mapped[int]  = mapped_column(ForeignKey("roles.id"), nullable=False)
    is_active:      Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version:  Mapped[int]  = mapped_column(Integer, default=0, nullable=False)

    role: Mapped["Role"] = relationship("Role", back_populates="users")
    # Linked HRIS employee (pegawai) record, if any.
    employee: Mapped["Employee|None"] = relationship(
        "Employee", back_populates="user", uselist=False, foreign_keys="Employee.user_id"
    )

    @property
    def employee_id(self) -> int | None:
        return self.employee.id if self.employee else None

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

    __table_args__ = (
        UniqueConstraint("email", name="users_email_key"),
        Index("ix_users_email", "email"),
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class AppMenu(Base, TimestampMixin):
    __tablename__ = "app_menus"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True)
    key:         Mapped[str]      = mapped_column(String(80), nullable=False)
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
        UniqueConstraint("key", name="app_menus_key_key"),
        Index("ix_app_menus_key", "key", unique=True),
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


class WorkspaceBranding(Base, TimestampMixin):
    __tablename__ = "workspace_branding"

    id:       Mapped[int] = mapped_column(Integer, primary_key=True)
    logo:     Mapped[str] = mapped_column(String(12), nullable=False, default="GP")
    title:    Mapped[str] = mapped_column(String(80), nullable=False, default="GPA")
    subtitle: Mapped[str] = mapped_column(String(120), nullable=False, default="Cost Control")


# ─── Project ─────────────────────────────────────────────────────────────────

class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id:             Mapped[int]           = mapped_column(Integer, primary_key=True)
    code:           Mapped[str]           = mapped_column(String(50), nullable=False)
    name:           Mapped[str]           = mapped_column(String(255), nullable=False)
    contract_value: Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    currency:       Mapped[str]           = mapped_column(String(3), nullable=False, default="IDR")
    is_archived:    Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)
    start_date:     Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date:       Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    status:         Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=ProjectStatus.ACTIVE
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
                ar.amount
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
            select(func.coalesce(func.sum(AccountReceivable.amount), 0))
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

    __table_args__ = (
        UniqueConstraint("code", name="projects_code_key"),
        Index("ix_projects_code", "code"),
        Index("ix_projects_archived_code", "is_archived", "code"),
    )

    def __repr__(self) -> str:
        return f"<Project {self.code} — {self.name}>"


# ─── CostCode ────────────────────────────────────────────────────────────────

class CostCode(Base, TimestampMixin):
    __tablename__ = "cost_codes"

    id:        Mapped[int]              = mapped_column(Integer, primary_key=True)
    code:      Mapped[str]              = mapped_column(String(50), nullable=False)
    name:      Mapped[str]              = mapped_column(String(255), nullable=False)
    parent_id: Mapped[int|None]         = mapped_column(ForeignKey("cost_codes.id"), nullable=True)
    category:  Mapped[CostCodeCategory] = mapped_column(SAEnum(CostCodeCategory, values_callable=lambda x: [e.value for e in x]), nullable=False)
    is_active: Mapped[bool]             = mapped_column(Boolean, default=True, nullable=False)

    parent:   Mapped["CostCode|None"]  = relationship("CostCode", remote_side="CostCode.id", back_populates="children")
    children: Mapped[list["CostCode"]] = relationship("CostCode", back_populates="parent")
    expenses: Mapped[list["Expense"]]  = relationship("Expense", back_populates="cost_code")
    petty_cash_reports: Mapped[list["PettyCashReport"]] = relationship("PettyCashReport", back_populates="cost_code")

    __table_args__ = (
        UniqueConstraint("code", name="cost_codes_code_key"),
        Index("ix_cost_codes_code", "code"),
    )

    def __repr__(self) -> str:
        return f"<CostCode {self.code} — {self.name}>"


# ─── ApprovalRule ────────────────────────────────────────────────────────────

class CostCentre(Base, TimestampMixin):
    __tablename__ = "cost_centres"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True)
    code:        Mapped[str]      = mapped_column(String(50), nullable=False)
    name:        Mapped[str]      = mapped_column(String(255), nullable=False)
    description: Mapped[str|None] = mapped_column(Text, nullable=True)
    is_active:   Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)

    expenses: Mapped[list["Expense"]] = relationship("Expense", back_populates="cost_centre")
    petty_cash_reports: Mapped[list["PettyCashReport"]] = relationship("PettyCashReport", back_populates="cost_centre")

    __table_args__ = (
        UniqueConstraint("code", name="cost_centres_code_key"),
        Index("ix_cost_centres_code", "code", unique=True),
    )


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
    cost_code_category:   Mapped[CostCodeCategory|None]= mapped_column(SAEnum(CostCodeCategory, values_callable=lambda x: [e.value for e in x]), nullable=True)
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
        SAEnum(ARStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=ARStatus.DRAFT
    )
    confirmed_by: Mapped[int|None]   = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)

    project:   Mapped["Project"] = relationship("Project", back_populates="receivables")
    confirmer: Mapped["User|None"] = relationship(
        "User", foreign_keys=[confirmed_by], back_populates="confirmed_ars"
    )

    __table_args__ = (
        Index("ix_ar_project_status", "project_id", "status"),
        Index("ix_ar_status", "status"),
        Index("ix_ar_actual_payment", "actual_payment"),
        Index("ix_ar_amount", "amount"),
        Index("ix_ar_due_date", "due_date"),
        Index("ix_ar_customer_name", "customer_name"),
        Index(
            "ix_ar_invoice_no_trgm",
            "invoice_no",
            postgresql_using="gin",
            postgresql_ops={"invoice_no": "gin_trgm_ops"},
        ),
        Index(
            "ix_ar_customer_name_trgm",
            "customer_name",
            postgresql_using="gin",
            postgresql_ops={"customer_name": "gin_trgm_ops"},
        ),
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

    id:                 Mapped[int]           = mapped_column(Integer, primary_key=True)
    expense_type:       Mapped[ExpenseType]   = mapped_column(
        SAEnum(ExpenseType, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=ExpenseType.REGULAR
    )
    project_id:         Mapped[int|None]      = mapped_column(ForeignKey("projects.id"), nullable=True)
    cost_code_id:       Mapped[int]           = mapped_column(ForeignKey("cost_codes.id"), nullable=False)
    cost_centre_id:     Mapped[int|None]      = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    petty_cash_line_id: Mapped[int|None]      = mapped_column(ForeignKey("petty_cash_report_lines.id"), nullable=True)
    amount:             Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False)
    description:        Mapped[str]           = mapped_column(Text, nullable=False)
    vendor_name:        Mapped[str|None]      = mapped_column(String(255), nullable=True)
    reference_no:       Mapped[str|None]      = mapped_column(String(100), nullable=True)
    receipt_url:        Mapped[str|None]      = mapped_column(String(2048), nullable=True)
    status:             Mapped[ExpenseStatus] = mapped_column(
        SAEnum(ExpenseStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=ExpenseStatus.DRAFT
    )

    # Actor FKs
    submitted_by:         Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)
    receipt_reviewed_by:  Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)  # GA receipt check
    verified_by:          Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by:          Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)
    paid_by:              Mapped[int|None] = mapped_column(ForeignKey("users.id"), nullable=True)

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
    submitter:         Mapped["User|None"] = relationship("User", foreign_keys=[submitted_by],        back_populates="submitted_expenses")
    receipt_reviewer:  Mapped["User|None"] = relationship("User", foreign_keys=[receipt_reviewed_by], viewonly=True)
    verifier:          Mapped["User|None"] = relationship("User", foreign_keys=[verified_by],          back_populates="verified_expenses")
    approver:          Mapped["User|None"] = relationship("User", foreign_keys=[approved_by],          back_populates="approved_expenses")
    payer:             Mapped["User|None"] = relationship("User", foreign_keys=[paid_by],              back_populates="paid_expenses")

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
    report_no:      Mapped[str]                   = mapped_column(String(100), nullable=False)
    month:          Mapped[str]                   = mapped_column(String(7), nullable=False, index=True)
    project_id:     Mapped[int]                   = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    cost_code_id:   Mapped[int]                   = mapped_column(ForeignKey("cost_codes.id"), nullable=False)
    cost_centre_id: Mapped[int|None]              = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    title:          Mapped[str|None]              = mapped_column(String(255), nullable=True)
    notes:          Mapped[str|None]              = mapped_column(Text, nullable=True)
    status:         Mapped[PettyCashReportStatus] = mapped_column(
        SAEnum(PettyCashReportStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=PettyCashReportStatus.DRAFT
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
        UniqueConstraint("report_no", name="petty_cash_reports_report_no_key"),
        Index("ix_petty_cash_reports_report_no", "report_no", unique=True),
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
    doc_type:           Mapped[DocType]       = mapped_column(SAEnum(DocType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    status:             Mapped[DocStatus]     = mapped_column(SAEnum(DocStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=DocStatus.DRAFT)

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
    code:          Mapped[str]           = mapped_column(String(50), nullable=False)
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
        UniqueConstraint("code", name="inventory_items_code_key"),
        Index("ix_inv_items_category", "category"),
        Index("ix_inv_items_active",   "is_active"),
    )

    def __repr__(self) -> str:
        return f"<InventoryItem {self.code} qty={self.qty_on_hand}>"


class InventoryTxn(Base):
    __tablename__ = "inventory_txns"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True)
    item_id:     Mapped[int]           = mapped_column(ForeignKey("inventory_items.id"), nullable=False)
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


# --- Cross-module operational records ---------------------------------------

class OperationalRecord(Base, TimestampMixin):
    """Shared workflow record for operational modules with common controls.

    Module-specific attributes live in ``details`` while reference, ownership,
    value, due date, progress, approval, and audit fields remain queryable.
    """
    __tablename__ = "operational_records"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True)
    module:           Mapped[str]           = mapped_column(String(50), nullable=False)
    record_type:      Mapped[str]           = mapped_column(String(60), nullable=False)
    reference_no:     Mapped[str]           = mapped_column(String(100), nullable=False)
    title:            Mapped[str]           = mapped_column(String(500), nullable=False)
    description:      Mapped[str|None]      = mapped_column(Text, nullable=True)
    status:           Mapped[str]           = mapped_column(String(30), nullable=False, default="draft")
    priority:         Mapped[str]           = mapped_column(String(20), nullable=False, default="normal")
    project_id:       Mapped[int|None]      = mapped_column(ForeignKey("projects.id"), nullable=True)
    partner_name:     Mapped[str|None]      = mapped_column(String(255), nullable=True)
    amount:           Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    currency:         Mapped[str]           = mapped_column(String(3), nullable=False, default="IDR")
    progress:         Mapped[Decimal]       = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    due_date:         Mapped[date|None]     = mapped_column(Date, nullable=True)
    owner_id:         Mapped[int|None]      = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by:       Mapped[int]           = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by:      Mapped[int|None]      = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at:      Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at:        Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    details:          Mapped[dict]          = mapped_column(JSONB, nullable=False, default=dict)
    workflow_history: Mapped[list]          = mapped_column(JSONB, nullable=False, default=list)

    project:  Mapped["Project|None"] = relationship("Project", foreign_keys=[project_id])
    owner:    Mapped["User|None"]    = relationship("User", foreign_keys=[owner_id])
    creator:  Mapped["User"]         = relationship("User", foreign_keys=[created_by])
    approver: Mapped["User|None"]    = relationship("User", foreign_keys=[approved_by])

    __table_args__ = (
        UniqueConstraint("module", "reference_no", name="uq_operational_record_reference"),
        Index("ix_operational_records_module_status", "module", "status"),
        Index("ix_operational_records_project", "project_id"),
        Index("ix_operational_records_due_date", "due_date"),
        Index("ix_operational_records_owner", "owner_id"),
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

    __table_args__ = (
        Index("ix_notifications_user_is_read", "user_id", "is_read"),
        Index("ix_notifications_created_at", text("created_at DESC")),
    )

    def __repr__(self) -> str:
        return f"<Notification {self.id} user={self.user_id} read={self.is_read}>"


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id:          Mapped[int]           = mapped_column(Integer, primary_key=True)
    to_email:    Mapped[str]           = mapped_column(String(320), nullable=False, index=True)
    subject:     Mapped[str]           = mapped_column(String(200), nullable=False)
    body_html:   Mapped[str]           = mapped_column(Text, nullable=False)
    body_text:   Mapped[str|None]      = mapped_column(Text, nullable=True)
    status:      Mapped[str]           = mapped_column(String(20), nullable=False, default="pending", index=True)
    attempts:    Mapped[int]           = mapped_column(Integer, nullable=False, default=0)
    last_error:  Mapped[str|None]      = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime]      = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    sent_at:     Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_email_outbox_pending", "status", "created_at"),
    )


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
    code:      Mapped[str]      = mapped_column(String(50), nullable=False)
    name:      Mapped[str]      = mapped_column(String(255), nullable=False)
    parent_id: Mapped[int|None] = mapped_column(ForeignKey("hris_departments.id"), nullable=True)
    is_active: Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)

    parent:   Mapped["Department|None"]  = relationship("Department", remote_side="Department.id", back_populates="children")
    children: Mapped[list["Department"]] = relationship("Department", back_populates="parent")
    employees: Mapped[list["Employee"]]  = relationship("Employee", back_populates="department")

    __table_args__ = (
        UniqueConstraint("code", name="uq_hris_dept_code"),
        Index("ix_hris_departments_code", "code"),
    )

    def __repr__(self) -> str:
        return f"<Department {self.code}>"


# ─── JobGrade ─────────────────────────────────────────────────────────────────

class JobGrade(Base, TimestampMixin):
    __tablename__ = "hris_job_grades"

    id:        Mapped[int]  = mapped_column(Integer, primary_key=True)
    code:      Mapped[str]  = mapped_column(String(50), nullable=False)
    name:      Mapped[str]  = mapped_column(String(255), nullable=False)
    level:     Mapped[int]  = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    employees: Mapped[list["Employee"]] = relationship("Employee", back_populates="grade")

    __table_args__ = (
        UniqueConstraint("code", name="uq_hris_grade_code"),
        Index("ix_hris_job_grades_code", "code"),
    )

    def __repr__(self) -> str:
        return f"<JobGrade {self.code} L{self.level}>"


# ─── WorkLocation ─────────────────────────────────────────────────────────────

class WorkLocationType(str, enum.Enum):
    HOME_OFFICE = "home_office"
    SITE        = "site"
    OTHER       = "other"


class WorkLocation(Base, TimestampMixin):
    """Named work locations with GPS centre + allowed radius for clock-in validation."""
    __tablename__ = "hris_work_locations"

    id:            Mapped[int]           = mapped_column(Integer, primary_key=True)
    name:          Mapped[str]           = mapped_column(String(255), nullable=False)
    location_type: Mapped[WorkLocationType] = mapped_column(
        SAEnum(WorkLocationType, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=WorkLocationType.OTHER
    )
    latitude:      Mapped[Decimal]       = mapped_column(Numeric(9, 6), nullable=False)
    longitude:     Mapped[Decimal]       = mapped_column(Numeric(9, 6), nullable=False)
    radius_meters: Mapped[int]           = mapped_column(Integer, nullable=False, default=100)
    timezone_name: Mapped[str]           = mapped_column(String(64), nullable=False, default="Asia/Jakarta")
    is_active:     Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)

    employees: Mapped[list["Employee"]] = relationship("Employee", back_populates="work_location")

    def __repr__(self) -> str:
        return f"<WorkLocation {self.name} r={self.radius_meters}m>"


# ─── WorkGroup ────────────────────────────────────────────────────────────────

class WorkGroup(Base, TimestampMixin):
    """Sub-groups within STAFF or WORKER roles (e.g. Tim Admin, Tim Site A)."""
    __tablename__ = "hris_work_groups"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True)
    name:        Mapped[str]      = mapped_column(String(255), unique=True, nullable=False)
    role:        Mapped[RoleName] = mapped_column(
        SAEnum(RoleName, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    description: Mapped[str|None] = mapped_column(Text, nullable=True)
    is_active:   Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)

    members: Mapped[list["Employee"]] = relationship("Employee", back_populates="work_group")

    def __repr__(self) -> str:
        return f"<WorkGroup {self.name}>"


# ─── Employee ─────────────────────────────────────────────────────────────────

class Employee(Base, TimestampMixin):
    __tablename__ = "hris_employees"

    id:               Mapped[int]            = mapped_column(Integer, primary_key=True)
    employee_no:      Mapped[str]            = mapped_column(String(50), nullable=False)
    full_name:        Mapped[str]            = mapped_column(String(255), nullable=False)
    nik:              Mapped[str|None]       = mapped_column(String(16), nullable=True)
    npwp:             Mapped[str|None]       = mapped_column(String(20), nullable=True)
    email:            Mapped[str|None]       = mapped_column(String(320), nullable=True)
    phone:            Mapped[str|None]       = mapped_column(String(20), nullable=True)
    tipe:             Mapped[EmploymentType] = mapped_column(
        SAEnum(EmploymentType, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    status:           Mapped[EmployeeStatus] = mapped_column(
        SAEnum(EmployeeStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=EmployeeStatus.ACTIVE
    )
    dept_id:          Mapped[int|None]       = mapped_column(ForeignKey("hris_departments.id"), nullable=True)
    grade_id:         Mapped[int|None]       = mapped_column(ForeignKey("hris_job_grades.id"), nullable=True)
    work_location_id: Mapped[int|None]       = mapped_column(ForeignKey("hris_work_locations.id"), nullable=True)
    work_group_id:    Mapped[int|None]       = mapped_column(ForeignKey("hris_work_groups.id"), nullable=True)
    site:             Mapped[str|None]       = mapped_column(String(255), nullable=True)
    join_date:        Mapped[date|None]      = mapped_column(Date, nullable=True)
    end_date:         Mapped[date|None]      = mapped_column(Date, nullable=True)
    bank_name:        Mapped[str|None]       = mapped_column(String(100), nullable=True)
    bank_account:     Mapped[str|None]       = mapped_column(String(50), nullable=True)
    bpjs_tk_no:       Mapped[str|None]       = mapped_column(String(30), nullable=True)
    bpjs_kes_no:      Mapped[str|None]       = mapped_column(String(30), nullable=True)
    user_id:          Mapped[int|None]       = mapped_column(ForeignKey("users.id"), nullable=True)
    photo_url:        Mapped[str|None]       = mapped_column(String(500), nullable=True)
    # PPh 21 PTKP status — e.g. "TK/0", "K/1". Defaults to TK/0 (single, no dependants).
    ptkp_status:      Mapped[str|None]       = mapped_column(String(10), nullable=True, default="TK/0")
    # Face recognition embedding (JSONB list of floats)
    face_embedding:   Mapped[dict|None]      = mapped_column(JSONB, nullable=True)

    department:    Mapped["Department|None"]         = relationship("Department", back_populates="employees")
    grade:         Mapped["JobGrade|None"]           = relationship("JobGrade", back_populates="employees")
    work_location: Mapped["WorkLocation|None"]       = relationship("WorkLocation", back_populates="employees")
    work_group:    Mapped["WorkGroup|None"]           = relationship("WorkGroup", back_populates="members")
    user:          Mapped["User|None"]               = relationship("User", foreign_keys=[user_id], back_populates="employee")
    documents:     Mapped[list["EmployeeDocument"]]  = relationship(
        "EmployeeDocument", back_populates="employee", cascade="all, delete-orphan"
    )
    salary_assignments: Mapped[list["SalaryAssignment"]] = relationship(
        "SalaryAssignment", back_populates="employee"
    )

    __table_args__ = (
        UniqueConstraint("employee_no", name="uq_hris_emp_no"),
        UniqueConstraint("nik", name="uq_hris_emp_nik"),
        UniqueConstraint("user_id", name="uq_hris_emp_user"),
        Index("ix_hris_employees_employee_no", "employee_no"),
        Index("ix_hris_employees_dept",          "dept_id"),
        Index("ix_hris_employees_status",        "status"),
        Index("ix_hris_employees_tipe",          "tipe"),
        Index("ix_hris_employees_work_location", "work_location_id"),
        Index("ix_hris_employees_work_group",  "work_group_id"),
    )

    def __repr__(self) -> str:
        return f"<Employee {self.employee_no} {self.full_name}>"


# ─── EmployeeDocument ─────────────────────────────────────────────────────────

class EmployeeDocument(Base, TimestampMixin):
    __tablename__ = "hris_employee_documents"

    id:          Mapped[int]        = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int]        = mapped_column(
        ForeignKey("hris_employees.id", ondelete="CASCADE"), nullable=False
    )
    doc_type:    Mapped[EmpDocType] = mapped_column(
        SAEnum(EmpDocType, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    file_url:    Mapped[str]        = mapped_column(String(500), nullable=False)
    uploaded_at: Mapped[datetime]   = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    employee: Mapped["Employee"] = relationship("Employee", back_populates="documents")

    __table_args__ = (
        Index("ix_hris_employee_documents_emp", "employee_id"),
    )

    def __repr__(self) -> str:
        return f"<EmployeeDocument {self.doc_type} emp={self.employee_id}>"


# ─── HRIS H2 Enumerations ─────────────────────────────────────────────────────

class AttendanceSource(str, enum.Enum):
    MANUAL       = "manual"
    MOBILE       = "mobile"        # geolocation + selfie clock-in
    FINGERPRINT  = "fingerprint"
    IMPORT       = "import"


class LeaveCategory(str, enum.Enum):
    ANNUAL    = "annual"
    SICK      = "sick"
    MATERNITY = "maternity"
    PATERNITY = "paternity"
    UNPAID    = "unpaid"
    OTHER     = "other"


class LeaveRequestStatus(str, enum.Enum):
    DRAFT     = "draft"
    SUBMITTED = "submitted"
    APPROVED  = "approved"
    REJECTED  = "rejected"


# ─── AttendanceRecord ─────────────────────────────────────────────────────────

class AttendanceRecord(Base, TimestampMixin):
    __tablename__ = "hris_attendance_records"

    id:                     Mapped[int]              = mapped_column(Integer, primary_key=True)
    employee_id:            Mapped[int]              = mapped_column(
        ForeignKey("hris_employees.id", ondelete="CASCADE"), nullable=False
    )
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
    location_ok:            Mapped[bool|None]        = mapped_column(Boolean, nullable=True)         # within radius?
    location_distance_m:    Mapped[Decimal|None]     = mapped_column(Numeric(10, 1), nullable=True)  # distance to work location centre
    # Selfie / face verification
    selfie_url:             Mapped[str|None]         = mapped_column(String(500), nullable=True)
    face_verified:          Mapped[bool]             = mapped_column(Boolean, nullable=False, default=False)
    face_confidence:        Mapped[Decimal|None]     = mapped_column(Numeric(4, 3), nullable=True)  # 0.000–1.000
    note:                   Mapped[str|None]         = mapped_column(Text, nullable=True)
    matched_work_location_id: Mapped[int|None] = mapped_column(ForeignKey("hris_work_locations.id"), nullable=True)

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id])
    matched_work_location: Mapped["WorkLocation|None"] = relationship("WorkLocation", foreign_keys=[matched_work_location_id])

    __table_args__ = (
        UniqueConstraint("employee_id", "date", name="uq_attendance_employee_date"),
        Index("ix_hris_attendance_date", "date"),
        Index("ix_hris_attendance_employee_id", "employee_id"),
    )

    def __repr__(self) -> str:
        return f"<AttendanceRecord emp={self.employee_id} date={self.date}>"


# ─── LeaveType ────────────────────────────────────────────────────────────────

class LeaveType(Base, TimestampMixin):
    __tablename__ = "hris_leave_types"

    id:                  Mapped[int]           = mapped_column(Integer, primary_key=True)
    code:                Mapped[str]           = mapped_column(String(50), nullable=False)
    name:                Mapped[str]           = mapped_column(String(255), nullable=False)
    max_days_per_year:   Mapped[int|None]      = mapped_column(Integer, nullable=True)   # null = unlimited
    is_paid:             Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    requires_approval:   Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    is_active:           Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    category:            Mapped[LeaveCategory] = mapped_column(
        SAEnum(
            LeaveCategory,
            values_callable=lambda x: [e.value for e in x],
            native_enum=False,
            length=20,
        ),
        nullable=False, default=LeaveCategory.ANNUAL
    )
    requires_doctor_cert: Mapped[bool]         = mapped_column(Boolean, nullable=False, default=False)

    balances:  Mapped[list["LeaveBalance"]]  = relationship("LeaveBalance",  back_populates="leave_type")
    requests:  Mapped[list["LeaveRequest"]]  = relationship("LeaveRequest",  back_populates="leave_type")

    __table_args__ = (
        UniqueConstraint("code", name="uq_leave_type_code"),
    )

    def __repr__(self) -> str:
        return f"<LeaveType {self.code}>"


# ─── LeaveBalance ─────────────────────────────────────────────────────────────

class LeaveBalance(Base, TimestampMixin):
    __tablename__ = "hris_leave_balances"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id:   Mapped[int] = mapped_column(
        ForeignKey("hris_employees.id", ondelete="CASCADE"), nullable=False
    )
    leave_type_id: Mapped[int] = mapped_column(
        ForeignKey("hris_leave_types.id", ondelete="CASCADE"), nullable=False
    )
    year:          Mapped[int] = mapped_column(Integer, nullable=False)
    accrued:       Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # days granted
    used:          Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # days taken

    employee:   Mapped["Employee"]  = relationship("Employee",  foreign_keys=[employee_id])
    leave_type: Mapped["LeaveType"] = relationship("LeaveType", back_populates="balances")

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "leave_type_id", "year",
            name="uq_leave_balance_emp_type_year",
        ),
        Index("ix_hris_leave_balances_employee_id", "employee_id"),
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
    employee_id:           Mapped[int]               = mapped_column(
        ForeignKey("hris_employees.id", ondelete="CASCADE"), nullable=False
    )
    leave_type_id:         Mapped[int]               = mapped_column(ForeignKey("hris_leave_types.id"), nullable=False)
    start_date:            Mapped[date]              = mapped_column(Date, nullable=False)
    end_date:              Mapped[date]              = mapped_column(Date, nullable=False)
    days:                  Mapped[int]               = mapped_column(Integer, nullable=False)
    reason:                Mapped[str|None]          = mapped_column(Text, nullable=True)
    doctor_cert_url:       Mapped[str|None]          = mapped_column(String(500), nullable=True)
    status:                Mapped[LeaveRequestStatus] = mapped_column(
        SAEnum(LeaveRequestStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=LeaveRequestStatus.DRAFT
    )
    approval_chain:        Mapped[list|None]         = mapped_column(JSONB, nullable=True, default=list)
    approval_step:         Mapped[int]               = mapped_column(Integer, nullable=False, default=0)
    current_approver_role: Mapped[str|None]          = mapped_column(String(50), nullable=True)
    approval_history:      Mapped[list|None]         = mapped_column(JSONB, nullable=True, default=list)
    submitted_by:          Mapped[int|None]          = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by:           Mapped[int|None]          = mapped_column(ForeignKey("users.id"), nullable=True)

    employee:   Mapped["Employee"]   = relationship("Employee",  foreign_keys=[employee_id])
    leave_type: Mapped["LeaveType"]  = relationship("LeaveType", back_populates="requests")
    submitter:  Mapped["User|None"]  = relationship("User", foreign_keys=[submitted_by])
    approver:   Mapped["User|None"]  = relationship("User", foreign_keys=[approved_by])

    __table_args__ = (
        Index("ix_hris_leave_requests_employee_id", "employee_id"),
        Index("ix_hris_leave_requests_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<LeaveRequest emp={self.employee_id} {self.start_date}–{self.end_date}>"


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS — Phase H3: Payroll
# ═══════════════════════════════════════════════════════════════════════════════

class SalaryComponentType(str, enum.Enum):
    BASIC      = "BASIC"
    ALLOWANCE  = "ALLOWANCE"
    DEDUCTION  = "DEDUCTION"
    BPJS       = "BPJS"
    TAX        = "TAX"


class PayrollStatus(str, enum.Enum):
    OPEN   = "OPEN"
    LOCKED = "LOCKED"
    POSTED = "POSTED"


class PPh21Method(str, enum.Enum):
    GROSS_UP = "GROSS_UP"
    NETTO    = "NETTO"


class SalaryComponent(Base, TimestampMixin):
    """Reusable salary line (basic, allowance, deduction, BPJS, tax)."""
    __tablename__ = "hris_salary_components"

    id:             Mapped[int]                = mapped_column(Integer, primary_key=True)
    code:           Mapped[str]                = mapped_column(String(20),  unique=True, nullable=False)
    name:           Mapped[str]                = mapped_column(String(100), nullable=False)
    component_type: Mapped[SalaryComponentType] = mapped_column(
        SAEnum(SalaryComponentType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    is_taxable:     Mapped[bool]               = mapped_column(Boolean, nullable=False, default=True)
    is_active:      Mapped[bool]               = mapped_column(Boolean, nullable=False, default=True)

    assignments: Mapped[list["SalaryAssignment"]] = relationship("SalaryAssignment", back_populates="component")

    def __repr__(self) -> str:
        return f"<SalaryComponent {self.code}>"


class SalaryAssignment(Base, TimestampMixin):
    """Per-employee salary structure line."""
    __tablename__ = "hris_salary_assignments"

    id:             Mapped[int]             = mapped_column(Integer, primary_key=True)
    employee_id:    Mapped[int]             = mapped_column(
        ForeignKey("hris_employees.id", ondelete="CASCADE"), nullable=False
    )
    component_id:   Mapped[int]             = mapped_column(ForeignKey("hris_salary_components.id"), nullable=False)
    amount:         Mapped[Decimal]         = mapped_column(Numeric(18, 2), nullable=False)
    effective_from: Mapped[date]            = mapped_column(Date, nullable=False)
    effective_to:   Mapped[date|None]       = mapped_column(Date, nullable=True)

    employee:  Mapped["Employee"]        = relationship("Employee", back_populates="salary_assignments")
    component: Mapped["SalaryComponent"] = relationship("SalaryComponent", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "component_id", "effective_from",
            name="uq_salary_assignment_effective_start",
        ),
        Index("ix_salary_assignments_emp", "employee_id"),
    )

    def __repr__(self) -> str:
        return f"<SalaryAssignment emp={self.employee_id} comp={self.component_id}>"


class PayrollPeriod(Base, TimestampMixin):
    """Monthly payroll period (OPEN → LOCKED → POSTED)."""
    __tablename__ = "hris_payroll_periods"

    id:        Mapped[int]            = mapped_column(Integer, primary_key=True)
    year:      Mapped[int]            = mapped_column(Integer, nullable=False)
    month:     Mapped[int]            = mapped_column(Integer, nullable=False)
    status:    Mapped[PayrollStatus]  = mapped_column(
        SAEnum(PayrollStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PayrollStatus.OPEN,
    )
    locked_at: Mapped[datetime|None]  = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[int|None]       = mapped_column(ForeignKey("users.id"), nullable=True)

    runs:    Mapped[list["PayrollRun"]] = relationship("PayrollRun", back_populates="period")
    locker:  Mapped["User|None"]        = relationship("User")

    __table_args__ = (UniqueConstraint("year", "month", name="uq_payroll_period_ym"),)

    def __repr__(self) -> str:
        return f"<PayrollPeriod {self.year}-{self.month:02d} {self.status}>"


class PayrollRun(Base, TimestampMixin):
    """Per-employee calculation result for a payroll period."""
    __tablename__ = "hris_payroll_runs"

    id:                  Mapped[int]           = mapped_column(Integer, primary_key=True)
    period_id:           Mapped[int]           = mapped_column(ForeignKey("hris_payroll_periods.id"), nullable=False)
    employee_id:         Mapped[int]           = mapped_column(ForeignKey("hris_employees.id"),       nullable=False)
    gross_salary:        Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=0)
    bpjs_tk_employee:    Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=0)
    bpjs_tk_employer:    Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=0)
    bpjs_kes_employee:   Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=0)
    bpjs_kes_employer:   Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=0)
    pph21_amount:        Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=0)
    pph21_method:        Mapped[PPh21Method]   = mapped_column(
        SAEnum(PPh21Method, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PPh21Method.NETTO,
    )
    net_salary:          Mapped[Decimal]       = mapped_column(Numeric(18, 2), nullable=False, default=0)
    thr_amount:          Mapped[Decimal|None]  = mapped_column(Numeric(18, 2), nullable=True)
    components_snapshot: Mapped[dict|None]     = mapped_column(JSONB, nullable=True)
    cost_centre_id:      Mapped[int|None]      = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    expense_id:          Mapped[int|None]      = mapped_column(ForeignKey("expenses.id"), nullable=True)

    period:    Mapped["PayrollPeriod"] = relationship("PayrollPeriod", back_populates="runs")
    employee:  Mapped["Employee"]      = relationship("Employee")
    payslip:   Mapped["PaySlip|None"]  = relationship("PaySlip", back_populates="run", uselist=False)

    __table_args__ = (
        UniqueConstraint("period_id", "employee_id", name="uq_payroll_run_period_emp"),
        Index("ix_payroll_runs_period_id", "period_id"),
        Index("ix_payroll_runs_employee_id", "employee_id"),
    )

    def __repr__(self) -> str:
        return f"<PayrollRun period={self.period_id} emp={self.employee_id}>"


class PaySlip(Base, TimestampMixin):
    """Generated PDF pay slip for a payroll run."""
    __tablename__ = "hris_payslips"

    id:           Mapped[int]       = mapped_column(Integer, primary_key=True)
    run_id:       Mapped[int]       = mapped_column(ForeignKey("hris_payroll_runs.id"), unique=True, nullable=False)
    pdf_url:      Mapped[str]       = mapped_column(String(500), nullable=False)
    generated_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped["PayrollRun"] = relationship("PayrollRun", back_populates="payslip")

    def __repr__(self) -> str:
        return f"<PaySlip run={self.run_id}>"


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS — Phase H4: Rekrutmen & Onboarding
# ═══════════════════════════════════════════════════════════════════════════════

class PostingStatus(str, enum.Enum):
    OPEN     = "OPEN"
    CLOSED   = "CLOSED"
    ON_HOLD  = "ON_HOLD"


class ApplicantStage(str, enum.Enum):
    RECEIVED    = "RECEIVED"
    SCREENING   = "SCREENING"
    INTERVIEW   = "INTERVIEW"
    OFFER       = "OFFER"
    HIRED       = "HIRED"
    REJECTED    = "REJECTED"


class ApplicantSource(str, enum.Enum):
    JOBSTREET = "JOBSTREET"
    LINKEDIN  = "LINKEDIN"
    REFERRAL  = "REFERRAL"
    WALK_IN   = "WALK_IN"
    OTHER     = "OTHER"


class InterviewResult(str, enum.Enum):
    PENDING = "PENDING"
    PASS    = "PASS"
    FAIL    = "FAIL"
    HOLD    = "HOLD"


class JobPosting(Base, TimestampMixin):
    """Open job requisition."""
    __tablename__ = "hris_job_postings"

    id:            Mapped[int]           = mapped_column(Integer, primary_key=True)
    title:         Mapped[str]           = mapped_column(String(200), nullable=False)
    department_id: Mapped[int|None]      = mapped_column(ForeignKey("hris_departments.id"), nullable=True)
    grade_id:      Mapped[int|None]      = mapped_column(ForeignKey("hris_job_grades.id"),  nullable=True)
    description:   Mapped[str|None]      = mapped_column(Text, nullable=True)
    requirements:  Mapped[str|None]      = mapped_column(Text, nullable=True)
    status:        Mapped[PostingStatus] = mapped_column(
        SAEnum(PostingStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PostingStatus.OPEN,
    )
    opened_at:     Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at:     Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by:    Mapped[int]           = mapped_column(ForeignKey("users.id"), nullable=False)

    department:  Mapped["Department|None"] = relationship("Department")
    grade:       Mapped["JobGrade|None"]   = relationship("JobGrade")
    creator:     Mapped["User"]            = relationship("User")
    applicants:  Mapped[list["Applicant"]] = relationship("Applicant", back_populates="posting")

    def __repr__(self) -> str:
        return f"<JobPosting '{self.title}' {self.status}>"


class Applicant(Base, TimestampMixin):
    """Candidate in the recruitment pipeline."""
    __tablename__ = "hris_applicants"

    id:         Mapped[int]             = mapped_column(Integer, primary_key=True)
    posting_id: Mapped[int]             = mapped_column(
        ForeignKey("hris_job_postings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name:  Mapped[str]             = mapped_column(String(200), nullable=False)
    email:      Mapped[str|None]        = mapped_column(String(200), nullable=True)
    phone:      Mapped[str|None]        = mapped_column(String(30),  nullable=True)
    source:     Mapped[ApplicantSource] = mapped_column(
        SAEnum(ApplicantSource, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=ApplicantSource.OTHER,
    )
    stage:      Mapped[ApplicantStage]  = mapped_column(
        SAEnum(ApplicantStage, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=ApplicantStage.RECEIVED,
    )
    cv_url:     Mapped[str|None]        = mapped_column(String(500), nullable=True)
    note:       Mapped[str|None]        = mapped_column(Text, nullable=True)
    employee_id: Mapped[int|None]       = mapped_column(
        ForeignKey("hris_employees.id"), unique=True, nullable=True,
    )

    posting:    Mapped["JobPosting"]        = relationship("JobPosting", back_populates="applicants")
    interviews: Mapped[list["Interview"]]   = relationship("Interview", back_populates="applicant")
    onboarding: Mapped[list["OnboardingTask"]] = relationship("OnboardingTask", back_populates="applicant")
    employee:   Mapped["Employee|None"] = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hris_applicants_stage", "stage"),
    )

    def __repr__(self) -> str:
        return f"<Applicant '{self.full_name}' {self.stage}>"


class Interview(Base, TimestampMixin):
    """Scheduled interview session."""
    __tablename__ = "hris_interviews"

    id:             Mapped[int]             = mapped_column(Integer, primary_key=True)
    applicant_id:   Mapped[int]             = mapped_column(
        ForeignKey("hris_applicants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_at:   Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)
    interviewer_id: Mapped[int|None]        = mapped_column(ForeignKey("users.id"), nullable=True)
    result:         Mapped[InterviewResult] = mapped_column(
        SAEnum(InterviewResult, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=InterviewResult.PENDING,
    )
    notes:          Mapped[str|None]        = mapped_column(Text, nullable=True)

    applicant:   Mapped["Applicant"]  = relationship("Applicant", back_populates="interviews")
    interviewer: Mapped["User|None"]  = relationship("User")

    def __repr__(self) -> str:
        return f"<Interview app={self.applicant_id} {self.result}>"


class OnboardingTask(Base, TimestampMixin):
    """Checklist item for a hired applicant's onboarding."""
    __tablename__ = "hris_onboarding_tasks"

    id:           Mapped[int]       = mapped_column(Integer, primary_key=True)
    applicant_id: Mapped[int]       = mapped_column(
        ForeignKey("hris_applicants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task:         Mapped[str]       = mapped_column(String(300), nullable=False)
    is_completed: Mapped[bool]      = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_to:  Mapped[int|None]  = mapped_column(ForeignKey("users.id"), nullable=True)
    sort_order:   Mapped[int]       = mapped_column(Integer, nullable=False, default=0)

    applicant:   Mapped["Applicant"]  = relationship("Applicant", back_populates="onboarding")
    assignee:    Mapped["User|None"]  = relationship("User")

    def __repr__(self) -> str:
        return f"<OnboardingTask '{self.task[:40]}' done={self.is_completed}>"


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS — Enhancement Pack: Config, Self-Service, Analytics
# ═══════════════════════════════════════════════════════════════════════════════

# ─── HolidayCalendar ──────────────────────────────────────────────────────────

class HolidayCalendar(Base, TimestampMixin):
    """National and company holidays used by overtime calculation engine."""
    __tablename__ = "hris_holiday_calendar"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True)
    date:        Mapped[date]     = mapped_column(Date, unique=True, nullable=False, index=True)
    name:        Mapped[str]      = mapped_column(String(255), nullable=False)
    is_national: Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)
    year:        Mapped[int]      = mapped_column(Integer, nullable=False, index=True)

    __table_args__ = (
        Index("ix_holiday_date", "date"),
        Index("ix_holiday_year", "year"),
    )

    def __repr__(self) -> str:
        return f"<HolidayCalendar {self.date} {self.name}>"


# ─── OvertimeRequest ──────────────────────────────────────────────────────────

class OvertimeRequestStatus(str, enum.Enum):
    DRAFT     = "draft"
    SUBMITTED = "submitted"
    APPROVED  = "approved"
    REJECTED  = "rejected"


class OvertimeRequest(Base, TimestampMixin):
    """Employee overtime request — submitted before working OT, approved by HR/MD."""
    __tablename__ = "hris_overtime_requests"

    id:               Mapped[int]                  = mapped_column(Integer, primary_key=True)
    employee_id:      Mapped[int]                  = mapped_column(ForeignKey("hris_employees.id"), nullable=False, index=True)
    date:             Mapped[date]                 = mapped_column(Date, nullable=False)
    planned_hours:    Mapped[Decimal]              = mapped_column(Numeric(4, 1), nullable=False)
    reason:           Mapped[str]                  = mapped_column(Text, nullable=False)
    status:           Mapped[OvertimeRequestStatus]= mapped_column(
        SAEnum(OvertimeRequestStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=OvertimeRequestStatus.SUBMITTED,
    )
    approved_by:      Mapped[int|None]             = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at:      Mapped[datetime|None]        = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str|None]             = mapped_column(Text, nullable=True)
    attendance_id:    Mapped[int|None]             = mapped_column(ForeignKey("hris_attendance_records.id"), nullable=True)

    employee:    Mapped["Employee"]              = relationship("Employee", foreign_keys=[employee_id])
    approver:    Mapped["User|None"]             = relationship("User", foreign_keys=[approved_by])
    attendance:  Mapped["AttendanceRecord|None"] = relationship("AttendanceRecord", foreign_keys=[attendance_id])

    __table_args__ = (
        Index("ix_ot_requests_employee", "employee_id"),
        Index("ix_ot_requests_status",   "status"),
        Index("ix_ot_requests_date",     "date"),
    )

    def __repr__(self) -> str:
        return f"<OvertimeRequest emp={self.employee_id} date={self.date} {self.status}>"


# ─── EmployeeDataChangeRequest ────────────────────────────────────────────────

class DataChangeStatus(str, enum.Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EmployeeDataChangeRequest(Base, TimestampMixin):
    """Employee requests a change to their own master data (bank account, phone, etc.)."""
    __tablename__ = "hris_data_change_requests"

    id:           Mapped[int]            = mapped_column(Integer, primary_key=True)
    employee_id:  Mapped[int]            = mapped_column(ForeignKey("hris_employees.id"), nullable=False, index=True)
    field_name:   Mapped[str]            = mapped_column(String(100), nullable=False)   # "bank_account", "phone", etc.
    old_value:    Mapped[str|None]       = mapped_column(Text, nullable=True)
    new_value:    Mapped[str]            = mapped_column(Text, nullable=False)
    reason:       Mapped[str|None]       = mapped_column(Text, nullable=True)
    status:       Mapped[DataChangeStatus]= mapped_column(
        SAEnum(DataChangeStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=DataChangeStatus.PENDING,
    )
    reviewed_by:  Mapped[int|None]       = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at:  Mapped[datetime|None]  = mapped_column(DateTime(timezone=True), nullable=True)
    review_note:  Mapped[str|None]       = mapped_column(Text, nullable=True)

    employee:   Mapped["Employee"]  = relationship("Employee", foreign_keys=[employee_id])
    reviewer:   Mapped["User|None"] = relationship("User", foreign_keys=[reviewed_by])

    __table_args__ = (
        Index("ix_data_change_employee", "employee_id"),
        Index("ix_data_change_status",   "status"),
    )

    def __repr__(self) -> str:
        return f"<DataChangeRequest emp={self.employee_id} field={self.field_name} {self.status}>"

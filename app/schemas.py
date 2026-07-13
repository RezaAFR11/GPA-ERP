"""
GPA-ERP V5.0 — Pydantic v2 schemas
All money fields use Decimal with 2 decimal places.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

T = TypeVar("T")

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import (
    ARStatus, CostCodeCategory, DocStatus, DocType,
    ExpenseStatus, ExpenseType, ItemCategory, PettyCashReportStatus, ProjectStatus, RoleName, TxnType,
    EmpDocType, EmployeeStatus, EmploymentType,
    AttendanceSource, LeaveCategory, LeaveRequestStatus,
    SalaryComponentType, PayrollStatus, PPh21Method,
    PostingStatus, ApplicantStage, ApplicantSource, InterviewResult,
    WorkLocationType,
    OvertimeRequestStatus, DataChangeStatus,
)


# ─── Shared config ───────────────────────────────────────────────────────────

class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Token / Auth ─────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


# ─── Role ────────────────────────────────────────────────────────────────────

class RoleResponse(ORMBase):
    id:   int
    name: RoleName


# ─── User ────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email:     EmailStr
    password:  str  = Field(min_length=8, description="Min 8 chars")
    full_name: str  = Field(min_length=2, max_length=255)
    role_id:   int

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserUpdate(BaseModel):
    full_name: str | None  = Field(None, min_length=2, max_length=255)
    role_id:   int | None  = None
    is_active: bool | None = None


class UserSelfUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)


class PasswordChange(BaseModel):
    current_password: str
    new_password:     str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserResponse(ORMBase):
    id:         int
    email:      str
    full_name:  str
    is_active:  bool
    role:       RoleResponse
    created_at: datetime
    must_change_password: bool = False
    employee_id: int | None = None


class PasswordResetResponse(BaseModel):
    message:       str
    temp_password: str


class UserSummary(ORMBase):
    id:        int
    full_name: str
    email:     str
    role:      RoleResponse


# ─── Project ─────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    code:           str     = Field(min_length=2, max_length=50, pattern=r"^[A-Z0-9\-]+$")
    name:           str     = Field(min_length=2, max_length=255)
    contract_value: Decimal = Field(ge=Decimal("0"), decimal_places=2)
    currency:       str     = Field(default="IDR", min_length=3, max_length=3)
    is_archived:    bool    = False
    start_date:     datetime | None = None
    end_date:       datetime | None = None
    status:         ProjectStatus   = ProjectStatus.ACTIVE

    @model_validator(mode="after")
    def dates_consistent(self) -> "ProjectCreate":
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class ProjectUpdate(BaseModel):
    name:           str     | None  = Field(None, min_length=2, max_length=255)
    contract_value: Decimal | None  = Field(None, ge=Decimal("0"), decimal_places=2)
    currency:       str     | None  = Field(None, min_length=3, max_length=3)
    is_archived:    bool    | None  = None
    start_date:     datetime | None = None
    end_date:       datetime | None = None
    status:         ProjectStatus   | None = None


class ProjectResponse(ORMBase):
    id:             int
    code:           str
    name:           str
    contract_value: Decimal
    currency:       str
    is_archived:    bool
    status:         ProjectStatus
    start_date:     datetime | None
    end_date:       datetime | None
    imported_at:    datetime | None
    created_at:     datetime
    # computed
    total_revenue:  Decimal
    total_committed:Decimal
    budget:         Decimal


class ProjectImportRow(BaseModel):
    """One row from the Excel/CSV import template."""
    code:           str
    name:           str
    contract_value: Decimal
    start_date:     str | None = None
    end_date:       str | None = None
    status:         str | None = None


class ProjectImportResult(BaseModel):
    imported: int
    skipped:  int
    errors:   list[dict[str, Any]]


# ─── CostCode ────────────────────────────────────────────────────────────────

class CostCodeCreate(BaseModel):
    code:      str              = Field(min_length=2, max_length=50)
    name:      str              = Field(min_length=2, max_length=255)
    parent_id: int | None       = None
    category:  CostCodeCategory
    is_active: bool             = True


class CostCodeUpdate(BaseModel):
    name:      str              | None = None
    parent_id: int              | None = None
    category:  CostCodeCategory | None = None
    is_active: bool             | None = None


class CostCodeResponse(ORMBase):
    id:        int
    code:      str
    name:      str
    parent_id: int | None
    category:  CostCodeCategory
    is_active: bool
    created_at:datetime


class CostCentreCreate(BaseModel):
    code:        str        = Field(min_length=1, max_length=50)
    name:        str        = Field(min_length=2, max_length=255)
    description: str | None = None
    is_active:   bool       = True


class CostCentreUpdate(BaseModel):
    name:        str | None = Field(None, min_length=2, max_length=255)
    description: str | None = None
    is_active:   bool | None = None


class CostCentreResponse(ORMBase):
    id:          int
    code:        str
    name:        str
    description: str | None
    is_active:   bool
    created_at:  datetime


class ProjectDocumentResponse(ORMBase):
    id:           int
    project_id:   int
    doc_type:     str
    title:        str
    file_path:    str
    reference_no: str | None
    created_at:   datetime


# ─── ApprovalRule ────────────────────────────────────────────────────────────

class ApprovalRuleCreate(BaseModel):
    min_amount:         Decimal             = Field(ge=Decimal("0"), decimal_places=2, default=Decimal("0"))
    max_amount:         Decimal | None      = Field(None, ge=Decimal("0"), decimal_places=2)
    cost_code_category: CostCodeCategory | None = None
    required_role:      RoleName
    priority:           int                 = Field(ge=1, default=1)
    is_active:          bool                = True

    @model_validator(mode="after")
    def amount_range_valid(self) -> "ApprovalRuleCreate":
        if self.max_amount is not None and self.max_amount <= self.min_amount:
            raise ValueError("max_amount must be greater than min_amount")
        return self


class ApprovalRuleUpdate(BaseModel):
    min_amount:         Decimal             | None = Field(None, ge=Decimal("0"), decimal_places=2)
    max_amount:         Decimal             | None = Field(None, ge=Decimal("0"), decimal_places=2)
    cost_code_category: CostCodeCategory    | None = None
    required_role:      RoleName            | None = None
    priority:           int                 | None = Field(None, ge=1)
    is_active:          bool                | None = None


class ApprovalRuleResponse(ORMBase):
    id:                 int
    min_amount:         Decimal
    max_amount:         Decimal | None
    cost_code_category: CostCodeCategory | None
    required_role:      RoleName
    priority:           int
    is_active:          bool
    created_at:         datetime


# ─── AccountReceivable ───────────────────────────────────────────────────────

class ARCreate(BaseModel):
    project_id:  int
    amount:      Decimal = Field(gt=Decimal("0"), decimal_places=2)
    description: str     = Field(min_length=3, max_length=2000)
    invoice_no:  str | None = Field(None, max_length=100)
    customer_name: str | None = Field(None, max_length=255)
    invoice_date: datetime | None = None
    due_date:    datetime | None = None
    expected_payment: Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    actual_payment:   Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    remaining_amount: Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    paid_at:     datetime | None = None


class ARUpdate(BaseModel):
    project_id:  int     | None = None
    amount:      Decimal | None = Field(None, gt=Decimal("0"), decimal_places=2)
    description: str     | None = Field(None, min_length=3, max_length=2000)
    invoice_no:  str     | None = Field(None, max_length=100)
    customer_name: str   | None = Field(None, max_length=255)
    invoice_date: datetime | None = None
    due_date:    datetime | None = None
    expected_payment: Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    actual_payment:   Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    remaining_amount: Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    paid_at:     datetime | None = None


class ARConfirm(BaseModel):
    note: str | None = None


class ARResponse(ORMBase):
    id:           int
    project_id:   int
    amount:       Decimal
    description:  str
    invoice_no:   str | None
    customer_name:str | None
    invoice_date: datetime | None
    due_date:     datetime | None
    expected_payment: Decimal | None
    actual_payment:   Decimal | None
    remaining_amount: Decimal | None
    paid_at:      datetime | None
    status:       ARStatus
    confirmed_by: int | None
    confirmed_at: datetime | None
    created_at:   datetime
    confirmer:    UserSummary | None = None


class ARSummary(BaseModel):
    total_invoiced:    Decimal
    total_paid:        Decimal
    total_outstanding: Decimal
    collection_rate:   float
    count:             int


# ─── Expense ─────────────────────────────────────────────────────────────────

class ExpenseCreate(BaseModel):
    expense_type:   ExpenseType = ExpenseType.REGULAR
    project_id:     int | None = None   # required for regular, optional for reimbursement
    cost_code_id:   int | None = None   # optional — auto-assigned for STAFF/WORKER reimbursements
    cost_centre_id: int | None = None
    amount:         Decimal = Field(gt=Decimal("0"), decimal_places=2)
    description:    str     = Field(min_length=3, max_length=2000)
    vendor_name:    str | None = Field(None, max_length=255)
    reference_no:   str | None = Field(None, max_length=100)
    receipt_url:    str | None = None

    @model_validator(mode="after")
    def project_required_for_regular(self) -> "ExpenseCreate":
        if self.expense_type == ExpenseType.REGULAR and self.project_id is None:
            raise ValueError("project_id is required for regular expenses")
        if self.expense_type == ExpenseType.REGULAR and self.cost_code_id is None:
            raise ValueError("cost_code_id is required for regular expenses")
        return self


class ExpenseUpdate(BaseModel):
    """Only allowed while status == draft."""
    cost_code_id:   int     | None = None
    cost_centre_id: int     | None = None
    amount:         Decimal | None = Field(None, gt=Decimal("0"), decimal_places=2)
    description:    str     | None = Field(None, min_length=3, max_length=2000)
    vendor_name:    str     | None = Field(None, max_length=255)
    reference_no:   str     | None = Field(None, max_length=100)
    receipt_url:    str     | None = None


class ExpenseActionRequest(BaseModel):
    note: str | None = Field(None, max_length=1000)


class ExpenseRejectRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


class ExpenseStats(BaseModel):
    total_logged:    Decimal
    total_approved:  Decimal
    total_paid:      Decimal
    count_by_status: dict[str, int]


class ExpenseResponse(ORMBase):
    id:                    int
    expense_type:          ExpenseType = ExpenseType.REGULAR
    project_id:            int | None
    cost_code_id:          int
    cost_centre_id:        int | None
    petty_cash_line_id:    int | None = None
    amount:                Decimal
    description:           str
    vendor_name:           str | None = None
    reference_no:          str | None = None
    receipt_url:           str | None
    status:                ExpenseStatus
    over_budget:           bool | None = None
    budget_remaining:      Decimal | None = None
    submitted_by:          int | None
    receipt_reviewed_by:   int | None = None
    verified_by:           int | None
    approved_by:           int | None
    paid_by:               int | None
    current_approver_role: str | None
    approval_chain:        list | None
    approval_step:         int
    approval_history:      list | None
    rejection_reason:      str | None
    created_at:            datetime
    updated_at:            datetime
    cost_code:             CostCodeResponse | None = None
    cost_centre:           CostCentreResponse | None = None
    submitter:             UserSummary      | None = None


class PettyCashReportLineCreate(BaseModel):
    spent_on:    date | None = None
    description: str = Field(min_length=3, max_length=2000)
    amount:      Decimal = Field(gt=Decimal("0"), decimal_places=2)
    receipt_url: str | None = None
    source:      str | None = Field(None, max_length=50)
    ocr_text:    str | None = None


class PettyCashReportLineUpdate(BaseModel):
    id:          int | None = None
    spent_on:    date | None = None
    description: str | None = Field(None, min_length=3, max_length=2000)
    amount:      Decimal | None = Field(None, gt=Decimal("0"), decimal_places=2)
    receipt_url: str | None = None
    source:      str | None = Field(None, max_length=50)
    ocr_text:    str | None = None


class PettyCashReportCreate(BaseModel):
    month:          str = Field(pattern=r"^\d{4}-\d{2}$")
    project_id:     int
    cost_code_id:   int
    cost_centre_id: int | None = None
    title:          str | None = Field(None, max_length=255)
    notes:          str | None = None
    lines:          list[PettyCashReportLineCreate] = Field(min_length=1)


class PettyCashReportUpdate(BaseModel):
    month:          str | None = Field(None, pattern=r"^\d{4}-\d{2}$")
    project_id:     int | None = None
    cost_code_id:   int | None = None
    cost_centre_id: int | None = None
    title:          str | None = Field(None, max_length=255)
    notes:          str | None = None
    lines:          list[PettyCashReportLineUpdate] | None = None


class PettyCashReportLineResponse(ORMBase):
    id:          int
    report_id:   int
    expense_id:  int | None
    line_no:     int
    spent_on:    date | None
    description: str
    amount:      Decimal
    receipt_url: str | None
    source:      str | None
    ocr_text:    str | None
    created_at:  datetime
    updated_at:  datetime


class PettyCashReportResponse(ORMBase):
    id:             int
    report_no:      str
    month:          str
    project_id:     int
    cost_code_id:   int
    cost_centre_id: int | None
    title:          str | None
    notes:          str | None
    status:         PettyCashReportStatus
    total_amount:   Decimal
    created_by:     int
    posted_at:      datetime | None
    created_at:     datetime
    updated_at:     datetime
    project:        ProjectResponse | None = None
    cost_code:      CostCodeResponse | None = None
    cost_centre:    CostCentreResponse | None = None
    creator:        UserSummary | None = None
    lines:          list[PettyCashReportLineResponse] = []


# ─── AuditLog ────────────────────────────────────────────────────────────────

class AuditLogResponse(ORMBase):
    id:           int
    entity_type:  str
    entity_id:    int
    action:       str
    before_state: dict | None
    after_state:  dict | None
    changed_by:   int | None
    ip_address:   str | None
    created_at:   datetime


# ─── Legal Documents ─────────────────────────────────────────────────────────

class LegalDocCreate(BaseModel):
    doc_number:         str | None = Field(None, max_length=100)
    reference_number:   str | None = Field(None, max_length=100)
    doc_type:           DocType
    title:              str     = Field(min_length=3, max_length=500)
    subject:            str     = Field(min_length=3, max_length=500)
    body:               str     = Field(min_length=10)
    recipient_name:     str | None = Field(None, max_length=255)
    recipient_company:  str | None = Field(None, max_length=255)
    recipient_address:  str | None = None
    closing:            str | None = None
    quoted_amount:      Decimal | None = Field(None, gt=Decimal("0"), decimal_places=2)
    project_id:         int | None = None


class LegalDocUpdate(BaseModel):
    doc_number:        str     | None = Field(None, max_length=100)
    reference_number:  str     | None = Field(None, max_length=100)
    doc_type:          DocType | None = None
    title:             str     | None = Field(None, min_length=3, max_length=500)
    subject:           str     | None = Field(None, min_length=3, max_length=500)
    body:              str     | None = Field(None, min_length=10)
    recipient_name:    str     | None = None
    recipient_company: str     | None = None
    recipient_address: str     | None = None
    closing:           str     | None = None
    quoted_amount:     Decimal | None = Field(None, gt=Decimal("0"), decimal_places=2)
    project_id:        int     | None = None


class LegalDocRejectRequest(BaseModel):
    note: str = Field(min_length=5, max_length=1000)


class LegalDocResponse(ORMBase):
    id:                int
    doc_number:        str | None
    reference_number:  str | None
    doc_type:          DocType
    status:            DocStatus
    title:             str
    subject:           str
    body:              str
    recipient_name:    str | None
    recipient_company: str | None
    recipient_address: str | None
    closing:           str | None
    quoted_amount:     Decimal | None
    project_id:        int | None
    rejection_note:    str | None
    signed_by:         int | None
    signed_at:         datetime | None
    created_by:        int
    created_at:        datetime
    updated_at:        datetime
    creator:           UserSummary | None = None
    signer:            UserSummary | None = None
    project:           "ProjectResponse | None" = None


# ─── Inventory ───────────────────────────────────────────────────────────────

class InventoryItemCreate(BaseModel):
    code:      str     = Field(min_length=1, max_length=50)
    name:      str     = Field(min_length=2, max_length=255)
    category:  ItemCategory
    unit:      str     = Field(default="pcs", max_length=50)
    qty_on_hand: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), decimal_places=3)
    min_stock:   Decimal = Field(default=Decimal("0"), ge=Decimal("0"), decimal_places=3)
    unit_cost:   Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    location:    str | None = Field(None, max_length=255)
    notes:       str | None = None


class InventoryItemUpdate(BaseModel):
    name:       str     | None = Field(None, min_length=2, max_length=255)
    category:   ItemCategory | None = None
    unit:       str     | None = Field(None, max_length=50)
    min_stock:  Decimal | None = Field(None, ge=Decimal("0"), decimal_places=3)
    unit_cost:  Decimal | None = Field(None, ge=Decimal("0"), decimal_places=2)
    location:   str     | None = Field(None, max_length=255)
    notes:      str     | None = None
    is_active:  bool    | None = None


class InventorySummary(BaseModel):
    total_items:     int
    low_stock_count: int
    total_value:     Decimal


class InventoryTxnCreate(BaseModel):
    txn_type:   TxnType
    quantity:   Decimal = Field(gt=Decimal("0"), decimal_places=3)
    reference:  str | None = Field(None, max_length=255)
    notes:      str | None = None
    project_id: int | None = None


class InventoryTxnResponse(ORMBase):
    id:         int
    item_id:    int
    txn_type:   TxnType
    quantity:   Decimal
    reference:  str | None
    notes:      str | None
    project_id: int | None
    created_by: int
    created_at: datetime
    creator:    UserSummary | None = None


class InventoryItemResponse(ORMBase):
    id:           int
    code:         str
    name:         str
    category:     ItemCategory
    unit:         str
    qty_on_hand:  Decimal
    min_stock:    Decimal
    unit_cost:    Decimal | None
    location:     str | None
    notes:        str | None
    is_active:    bool
    created_at:   datetime
    updated_at:   datetime


# ─── Notifications ───────────────────────────────────────────────────────────

class NotificationOut(BaseModel):
    id:         int
    title:      str
    body:       str
    link:       str | None
    is_read:    bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ─── Generic Responses ───────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS Schemas — Phase H1: Data Karyawan & Organisasi
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Department ──────────────────────────────────────────────────────────────

class DepartmentCreate(BaseModel):
    code:      str  = Field(min_length=1, max_length=50)
    name:      str  = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    code:      str | None  = Field(None, min_length=1, max_length=50)
    name:      str | None  = Field(None, min_length=1, max_length=255)
    parent_id: int | None  = None
    is_active: bool | None = None


class DepartmentResponse(ORMBase):
    id:        int
    code:      str
    name:      str
    parent_id: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ─── JobGrade ────────────────────────────────────────────────────────────────

class JobGradeCreate(BaseModel):
    code:      str = Field(min_length=1, max_length=50)
    name:      str = Field(min_length=1, max_length=255)
    level:     int = Field(ge=1, le=20)
    is_active: bool = True


class JobGradeUpdate(BaseModel):
    code:      str | None  = Field(None, min_length=1, max_length=50)
    name:      str | None  = Field(None, min_length=1, max_length=255)
    level:     int | None  = Field(None, ge=1, le=20)
    is_active: bool | None = None


class JobGradeResponse(ORMBase):
    id:        int
    code:      str
    name:      str
    level:     int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ─── Employee ────────────────────────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    employee_no:  str              = Field(min_length=1, max_length=50)
    full_name:    str              = Field(min_length=2, max_length=255)
    nik:          str | None       = Field(None, max_length=16)
    npwp:         str | None       = Field(None, max_length=20)
    email:        str | None       = Field(None, max_length=320)
    phone:        str | None       = Field(None, max_length=20)
    tipe:         EmploymentType
    status:       EmployeeStatus   = EmployeeStatus.ACTIVE
    dept_id:      int | None       = None
    grade_id:     int | None       = None
    site:         str | None       = Field(None, max_length=255)
    join_date:    date | None      = None
    end_date:     date | None      = None
    bank_name:    str | None       = Field(None, max_length=100)
    bank_account: str | None       = Field(None, max_length=50)
    bpjs_tk_no:   str | None       = Field(None, max_length=30)
    bpjs_kes_no:  str | None       = Field(None, max_length=30)
    user_id:      int | None       = None


class EmployeeUpdate(BaseModel):
    full_name:    str | None            = Field(None, min_length=2, max_length=255)
    nik:          str | None            = Field(None, max_length=16)
    npwp:         str | None            = Field(None, max_length=20)
    email:        str | None            = Field(None, max_length=320)
    phone:        str | None            = Field(None, max_length=20)
    tipe:         EmploymentType | None = None
    status:       EmployeeStatus | None = None
    dept_id:      int | None            = None
    grade_id:     int | None            = None
    site:         str | None            = Field(None, max_length=255)
    join_date:    date | None           = None
    end_date:     date | None           = None
    bank_name:    str | None            = Field(None, max_length=100)
    bank_account: str | None            = Field(None, max_length=50)
    bpjs_tk_no:   str | None            = Field(None, max_length=30)
    bpjs_kes_no:  str | None            = Field(None, max_length=30)
    user_id:          int | None            = None
    work_location_id: int | None            = None
    ptkp_status:      str | None            = Field(None, max_length=10)


class EmployeeDocumentResponse(ORMBase):
    id:          int
    employee_id: int
    doc_type:    EmpDocType
    file_url:    str
    uploaded_at: datetime
    created_at:  datetime


class EmployeeResponse(ORMBase):
    id:           int
    employee_no:  str
    full_name:    str
    nik:          str | None
    npwp:         str | None
    email:        str | None
    phone:        str | None
    tipe:         EmploymentType
    status:       EmployeeStatus
    dept_id:      int | None
    grade_id:     int | None
    site:         str | None
    join_date:    date | None
    end_date:     date | None
    bank_name:    str | None
    bank_account: str | None
    bpjs_tk_no:   str | None
    bpjs_kes_no:  str | None
    user_id:      int | None
    photo_url:    str | None
    department:   DepartmentResponse | None = None
    grade:        JobGradeResponse | None   = None
    user:         UserSummary | None        = None
    documents:    list[EmployeeDocumentResponse] = []
    created_at:   datetime
    updated_at:   datetime


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS Schemas — Work Locations
# ═══════════════════════════════════════════════════════════════════════════════

class WorkLocationCreate(BaseModel):
    name:          str             = Field(min_length=1, max_length=255)
    location_type: WorkLocationType = WorkLocationType.OTHER
    latitude:      Decimal
    longitude:     Decimal
    radius_meters: int             = Field(default=100, ge=10, le=50000)
    is_active:     bool            = True


class WorkLocationUpdate(BaseModel):
    name:          str | None             = None
    location_type: WorkLocationType | None = None
    latitude:      Decimal | None         = None
    longitude:     Decimal | None         = None
    radius_meters: int | None             = Field(None, ge=10, le=50000)
    is_active:     bool | None            = None


class WorkLocationResponse(ORMBase):
    id:            int
    name:          str
    location_type: WorkLocationType
    latitude:      Decimal
    longitude:     Decimal
    radius_meters: int
    is_active:     bool
    created_at:    datetime
    updated_at:    datetime


# ─── WorkGroup schemas ────────────────────────────────────────────────────────

class WorkGroupCreate(ORMBase):
    name:        str
    role:        RoleName
    description: str | None = None
    is_active:   bool = True

class WorkGroupUpdate(ORMBase):
    name:        str | None = None
    description: str | None = None
    is_active:   bool | None = None

class EmployeeSummary(ORMBase):
    id:          int
    employee_no: str
    full_name:   str

class WorkGroupResponse(ORMBase):
    id:          int
    name:        str
    role:        RoleName
    description: str | None
    is_active:   bool
    members:     list[EmployeeSummary] = []
    created_at:  datetime
    updated_at:  datetime


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS Schemas — Phase H2: Absensi & Cuti
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Attendance ──────────────────────────────────────────────────────────────

class AttendanceRecordResponse(ORMBase):
    id:                     int
    employee_id:            int
    date:                   date
    clock_in:               datetime | None
    clock_out:              datetime | None
    hours_regular:          Decimal | None
    hours_overtime_weekday: Decimal | None
    hours_overtime_weekend: Decimal | None
    hours_overtime_holiday: Decimal | None
    source:                 AttendanceSource
    latitude:               Decimal | None
    longitude:              Decimal | None
    accuracy:               Decimal | None
    location_ok:            bool | None
    location_distance_m:    Decimal | None
    selfie_url:             str | None
    face_verified:          bool
    face_confidence:        Decimal | None
    note:                   str | None
    matched_location_name:  str | None = None
    matched_location_type:  str | None = None
    created_at:             datetime
    updated_at:             datetime

    @model_validator(mode="before")
    @classmethod
    def _populate_matched_location(cls, data):
        if hasattr(data, "matched_work_location") and data.matched_work_location is not None:
            wl = data.matched_work_location
            data.__dict__["matched_location_name"] = wl.name
            data.__dict__["matched_location_type"] = wl.location_type.value if wl.location_type else None
        return data


class AttendanceManualCreate(BaseModel):
    employee_id:            int
    date:                   date
    clock_in:               datetime | None = None
    clock_out:              datetime | None = None
    hours_regular:          Decimal | None  = None
    hours_overtime_weekday: Decimal | None  = None
    hours_overtime_weekend: Decimal | None  = None
    hours_overtime_holiday: Decimal | None  = None
    note:                   str | None      = None


class AttendanceSummaryItem(ORMBase):
    employee_id:   int
    employee_no:   str
    full_name:     str
    days_present:  int
    hours_regular: Decimal
    hours_ot_total: Decimal


# ─── Leave Types ─────────────────────────────────────────────────────────────

class LeaveTypeCreate(BaseModel):
    code:                str  = Field(min_length=1, max_length=50)
    name:                str  = Field(min_length=1, max_length=255)
    max_days_per_year:   int | None = Field(None, ge=1)
    is_paid:             bool = True
    requires_approval:   bool = True
    is_active:           bool = True
    category:            LeaveCategory = LeaveCategory.ANNUAL
    requires_doctor_cert: bool = False


class LeaveTypeResponse(ORMBase):
    id:                  int
    code:                str
    name:                str
    max_days_per_year:   int | None
    is_paid:             bool
    requires_approval:   bool
    is_active:           bool
    category:            LeaveCategory
    requires_doctor_cert: bool
    created_at:          datetime


# ─── Leave Balance ───────────────────────────────────────────────────────────

class LeaveBalanceResponse(ORMBase):
    id:            int
    employee_id:   int
    leave_type_id: int
    year:          int
    accrued:       int
    used:          int
    remaining:     int
    leave_type:    LeaveTypeResponse


# ─── Leave Request ───────────────────────────────────────────────────────────

class LeaveRequestCreate(BaseModel):
    employee_id:   int | None = None   # optional: resolved from current_user if omitted
    leave_type_id: int
    start_date:    date
    end_date:      date
    reason:        str | None = None

    @model_validator(mode="after")
    def check_dates(self) -> "LeaveRequestCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self


class LeaveActionRequest(BaseModel):
    note: str | None = None


class LeaveRequestResponse(ORMBase):
    id:                    int
    employee_id:           int
    leave_type_id:         int
    start_date:            date
    end_date:              date
    days:                  int
    reason:                str | None
    status:                LeaveRequestStatus
    approval_chain:        list | None
    approval_step:         int
    current_approver_role: str | None
    approval_history:      list | None
    submitted_by:          int
    approved_by:           int | None
    leave_type:            LeaveTypeResponse
    employee:              EmployeeResponse | None = None
    created_at:            datetime
    updated_at:            datetime


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS H3 — Payroll schemas
# ═══════════════════════════════════════════════════════════════════════════════

class SalaryComponentCreate(BaseModel):
    code:           str
    name:           str
    component_type: SalaryComponentType
    is_taxable:     bool = True
    is_active:      bool = True


class SalaryComponentResponse(ORMBase):
    id:             int
    code:           str
    name:           str
    component_type: SalaryComponentType
    is_taxable:     bool
    is_active:      bool


class SalaryAssignmentCreate(BaseModel):
    employee_id:    int
    component_id:   int
    amount:         Decimal
    effective_from: date
    effective_to:   date | None = None


class SalaryAssignmentResponse(ORMBase):
    id:             int
    employee_id:    int
    component_id:   int
    component:      SalaryComponentResponse
    amount:         Decimal
    effective_from: date
    effective_to:   date | None


class PayrollPeriodCreate(BaseModel):
    year:  int
    month: int


class PayrollPeriodResponse(ORMBase):
    id:        int
    year:      int
    month:     int
    status:    PayrollStatus
    locked_at: datetime | None
    locked_by: int | None
    created_at: datetime


class BPJSBreakdown(BaseModel):
    """BPJS contribution summary (employee + employer)."""
    jht_employee:  Decimal
    jht_employer:  Decimal
    jp_employee:   Decimal
    jp_employer:   Decimal
    jkk_employer:  Decimal
    jkm_employer:  Decimal
    kes_employee:  Decimal
    kes_employer:  Decimal
    total_employee: Decimal
    total_employer: Decimal


class PayrollRunResponse(ORMBase):
    id:                int
    period_id:         int
    employee_id:       int
    employee:          EmployeeResponse | None = None
    gross_salary:      Decimal
    bpjs_tk_employee:  Decimal
    bpjs_tk_employer:  Decimal
    bpjs_kes_employee: Decimal
    bpjs_kes_employer: Decimal
    pph21_amount:      Decimal
    pph21_method:      PPh21Method
    net_salary:        Decimal
    thr_amount:        Decimal | None
    components_snapshot: dict | None
    cost_centre_id:    int | None
    expense_id:        int | None
    created_at:        datetime
    updated_at:        datetime


class PayrollRunAdjust(BaseModel):
    gross_salary:   Decimal | None = None
    thr_amount:     Decimal | None = None
    pph21_method:   PPh21Method | None = None
    cost_centre_id: int | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS H4 — Rekrutmen schemas
# ═══════════════════════════════════════════════════════════════════════════════

class JobPostingCreate(BaseModel):
    title:         str
    department_id: int | None = None
    grade_id:      int | None = None
    description:   str | None = None
    requirements:  str | None = None


class JobPostingResponse(ORMBase):
    id:            int
    title:         str
    department_id: int | None
    grade_id:      int | None
    description:   str | None
    requirements:  str | None
    status:        PostingStatus
    opened_at:     datetime | None
    closed_at:     datetime | None
    created_by:    int
    created_at:    datetime


class ApplicantCreate(BaseModel):
    posting_id: int
    full_name:  str
    email:      str | None = None
    phone:      str | None = None
    source:     ApplicantSource = ApplicantSource.OTHER
    note:       str | None = None


class ApplicantResponse(ORMBase):
    id:         int
    posting_id: int
    full_name:  str
    email:      str | None
    phone:      str | None
    source:     ApplicantSource
    stage:      ApplicantStage
    cv_url:     str | None
    note:       str | None
    created_at: datetime
    updated_at: datetime


class InterviewCreate(BaseModel):
    applicant_id:   int
    scheduled_at:   datetime
    interviewer_id: int | None = None
    notes:          str | None = None


class InterviewResponse(ORMBase):
    id:             int
    applicant_id:   int
    scheduled_at:   datetime
    interviewer_id: int | None
    result:         InterviewResult
    notes:          str | None
    created_at:     datetime


class OnboardingTaskResponse(ORMBase):
    id:           int
    applicant_id: int
    task:         str
    is_completed: bool
    completed_at: datetime | None
    assigned_to:  int | None
    sort_order:   int


class HireRequest(BaseModel):
    """Body for POST /hris/applicants/{id}/hire."""
    department_id:  int | None = None
    grade_id:       int | None = None
    join_date:      date | None = None
    create_user:    bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS Schemas — Enhancement Pack (Config, Self-Service, Analytics)
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Org Chart ───────────────────────────────────────────────────────────────

class DepartmentNode(ORMBase):
    """Recursive department tree node with headcount and open positions."""
    id:             int
    code:           str
    name:           str
    parent_id:      int | None
    is_active:      bool
    headcount:      int = 0
    open_positions: int = 0
    children:       list["DepartmentNode"] = []

DepartmentNode.model_rebuild()


# ─── HRIS Dashboard Stats ─────────────────────────────────────────────────────

class HeadcountTrendItem(BaseModel):
    month: str     # "2025-01"
    count: int

class DeptAttendanceItem(BaseModel):
    dept:     str
    rate_pct: float

class PkwtAlertItem(BaseModel):
    id:           int
    employee_no:  str
    full_name:    str
    dept:         str | None
    end_date:     date | None
    days_left:    int

class HrisDashboardStats(BaseModel):
    # Headcount
    total_employees:     int
    active:              int
    probation:           int
    terminated_ytd:      int
    hired_ytd:           int
    # Trend
    headcount_trend:     list[HeadcountTrendItem]
    # PKWT expiry
    pkwt_expiring_30d:   int
    pkwt_expiring_60d:   int
    pkwt_expiring_90d:   int
    pkwt_expiring_list:  list[PkwtAlertItem]
    # Leave
    leave_liability_days: int
    # Attendance
    attendance_rate_pct:  float
    dept_attendance:      list[DeptAttendanceItem]


# ─── Holiday Calendar ────────────────────────────────────────────────────────

class HolidayCalendarCreate(BaseModel):
    date:        date
    name:        str = Field(min_length=1, max_length=255)
    is_national: bool = True

class HolidayCalendarResponse(ORMBase):
    id:          int
    date:        date
    name:        str
    is_national: bool
    year:        int
    created_at:  datetime


# ─── Overtime Request ────────────────────────────────────────────────────────

class OvertimeRequestCreate(BaseModel):
    date:          date
    planned_hours: float = Field(gt=0, le=12)
    reason:        str = Field(min_length=1)

class OvertimeRequestResponse(ORMBase):
    id:               int
    employee_id:      int
    date:             date
    planned_hours:    Decimal
    reason:           str
    status:           OvertimeRequestStatus
    approved_by:      int | None
    approved_at:      datetime | None
    rejection_reason: str | None
    attendance_id:    int | None
    created_at:       datetime
    employee_name:    str | None = None

    @classmethod
    def from_orm_with_name(cls, obj: Any) -> "OvertimeRequestResponse":
        data = cls.model_validate(obj)
        data.employee_name = obj.employee.full_name if obj.employee else None
        return data

class OvertimeActionRequest(BaseModel):
    note: str | None = None


# ─── Employee Data Change Request ────────────────────────────────────────────

# Fields employees are allowed to request changes for
CHANGEABLE_FIELDS = {
    "phone", "email", "bank_name", "bank_account", "npwp", "bpjs_tk_no", "bpjs_kes_no",
}

class DataChangeRequestCreate(BaseModel):
    field_name: str = Field(min_length=1, max_length=100)
    new_value:  str = Field(min_length=1)
    reason:     str | None = None

class DataChangeRequestResponse(ORMBase):
    id:           int
    employee_id:  int
    field_name:   str
    old_value:    str | None
    new_value:    str
    reason:       str | None
    status:       DataChangeStatus
    reviewed_by:  int | None
    reviewed_at:  datetime | None
    review_note:  str | None
    created_at:   datetime

class DataChangeActionRequest(BaseModel):
    note: str | None = None


# ─── Team Leave Calendar ─────────────────────────────────────────────────────

class LeaveCalendarItem(BaseModel):
    employee_id:   int
    employee_name: str
    dept:          str | None
    leave_type:    str
    start_date:    date
    end_date:      date
    days:          int
    status:        str


# ─── Employee Documents Hub ──────────────────────────────────────────────────

class MyDocumentItem(BaseModel):
    doc_type:     str          # "payslip", "KTP", "BPJS_TK", etc.
    name:         str          # display name
    date:         date | None  # issued/uploaded date
    file_url:     str
    period_label: str | None = None  # "Januari 2025" for payslips

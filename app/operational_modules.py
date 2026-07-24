"""Configuration shared by the proposed EPC operational workspaces.

The modules use one workflow engine because reference control, ownership,
approval, due dates, and audit requirements are identical across domains.
Domain-specific fields remain available through each record's ``details``.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import RoleName


@dataclass(frozen=True)
class ModuleDefinition:
    key: str
    label: str
    description: str
    path: str
    prefix: str
    record_types: dict[str, str]
    approver_roles: tuple[RoleName, ...]


MODULE_DEFINITIONS: dict[str, ModuleDefinition] = {
    "procurement": ModuleDefinition(
        key="procurement",
        label="Procurement",
        description="Vendor sourcing, requisitions, quotations, purchase orders, and receipts",
        path="/procurement",
        prefix="PRC",
        record_types={
            "vendor": "Vendor Master",
            "purchase_requisition": "Purchase Requisition",
            "rfq": "Request for Quotation",
            "quotation": "Quotation Comparison",
            "purchase_order": "Purchase Order",
            "goods_receipt": "Goods Receipt",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.COST_CONTROL),
    ),
    "accounts_payable": ModuleDefinition(
        key="accounts_payable",
        label="Accounts Payable",
        description="Vendor invoices, payment vouchers, and payable ageing",
        path="/accounts-payable",
        prefix="AP",
        record_types={
            "vendor_invoice": "Vendor Invoice",
            "payment_voucher": "Payment Voucher",
            "credit_note": "Credit Note",
            "payable_adjustment": "Payable Adjustment",
        },
        approver_roles=(RoleName.MD, RoleName.FINANCE),
    ),
    "accounting_tax": ModuleDefinition(
        key="accounting_tax",
        label="Accounting & Tax",
        description="Journals, tax documents, bank reconciliation, and period controls",
        path="/accounting-tax",
        prefix="ACC",
        record_types={
            "journal_entry": "Journal Entry",
            "tax_document": "Tax Document",
            "bank_reconciliation": "Bank Reconciliation",
            "period_close": "Period Close",
        },
        approver_roles=(RoleName.MD, RoleName.FINANCE),
    ),
    "project_execution": ModuleDefinition(
        key="project_execution",
        label="Project Execution",
        description="WBS, milestones, progress, change orders, claims, and forecasts",
        path="/project-execution",
        prefix="PEX",
        record_types={
            "wbs_item": "WBS Item",
            "milestone": "Milestone",
            "progress_update": "Progress Update",
            "change_order": "Change Order",
            "progress_claim": "Progress Claim",
            "forecast_eac": "Forecast / EAC",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL),
    ),
    "engineering_documents": ModuleDefinition(
        key="engineering_documents",
        label="Engineering Documents",
        description="Controlled drawings, RFIs, submittals, transmittals, and as-built records",
        path="/engineering-documents",
        prefix="ENG",
        record_types={
            "drawing": "Drawing Register",
            "rfi": "Request for Information",
            "material_submittal": "Material Submittal",
            "method_statement": "Method Statement",
            "transmittal": "Transmittal",
            "as_built": "As-Built Document",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL),
    ),
    "quality_control": ModuleDefinition(
        key="quality_control",
        label="QA / QC",
        description="Inspection plans, requests, punch lists, tests, and corrective actions",
        path="/quality-control",
        prefix="QAC",
        record_types={
            "itp": "Inspection & Test Plan",
            "inspection_request": "Inspection Request",
            "test_record": "Test Record",
            "ncr": "Non-Conformance Report",
            "corrective_action": "Corrective Action",
            "punch_list": "Punch List",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.GA),
    ),
    "hse": ModuleDefinition(
        key="hse",
        label="HSE",
        description="Safety incidents, permits, inspections, toolbox meetings, and PPE controls",
        path="/hse",
        prefix="HSE",
        record_types={
            "incident": "Incident",
            "near_miss": "Near Miss",
            "toolbox_meeting": "Toolbox Meeting",
            "permit_to_work": "Permit to Work",
            "safety_inspection": "Safety Inspection",
            "jsa_jha": "JSA / JHA",
            "ppe_issuance": "PPE Issuance",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.GA),
    ),
    "warehouse_logistics": ModuleDefinition(
        key="warehouse_logistics",
        label="Warehouse & Logistics",
        description="Warehouses, transfers, reservations, material issues, returns, and stock counts",
        path="/warehouse-logistics",
        prefix="WHL",
        record_types={
            "warehouse": "Warehouse / Site Store",
            "stock_transfer": "Stock Transfer",
            "material_reservation": "Material Reservation",
            "material_issue": "Material Issue",
            "material_return": "Material Return",
            "stock_count": "Stock Count",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.GA),
    ),
    "equipment_assets": ModuleDefinition(
        key="equipment_assets",
        label="Equipment & Assets",
        description="Asset register, assignment, preventive maintenance, calibration, and downtime",
        path="/equipment-assets",
        prefix="AST",
        record_types={
            "asset": "Asset Register",
            "assignment": "Asset Assignment",
            "maintenance": "Maintenance Work Order",
            "calibration": "Calibration",
            "utilisation": "Utilisation / Downtime",
            "depreciation": "Depreciation Record",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.GA, RoleName.FINANCE),
    ),
    "contract_management": ModuleDefinition(
        key="contract_management",
        label="Contract Management",
        description="Client, vendor, and subcontract agreements, bonds, retention, and claims",
        path="/contracts",
        prefix="CTR",
        record_types={
            "client_contract": "Client Contract",
            "client_purchase_order": "Client Purchase Order",
            "vendor_contract": "Vendor Contract",
            "subcontract": "Subcontract",
            "bond_insurance": "Bond / Insurance",
            "retention": "Retention",
            "claim": "Contract Claim",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL),
    ),
    "crm_tender": ModuleDefinition(
        key="crm_tender",
        label="CRM & Tenders",
        description="Customers, opportunities, bids, estimates, quotations, and tender decisions",
        path="/crm-tenders",
        prefix="CRM",
        record_types={
            "customer": "Customer Master",
            "lead": "Lead",
            "opportunity": "Opportunity",
            "tender": "Tender",
            "estimate": "Estimate",
            "quotation": "Quotation",
            "bid_decision": "Bid / No-Bid Decision",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL),
    ),
    "manpower_operations": ModuleDefinition(
        key="manpower_operations",
        label="Manpower Operations",
        description="Mobilisation, project assignments, rosters, timesheets, competencies, and certificates",
        path="/hris/manpower",
        prefix="MPO",
        record_types={
            "mobilisation": "Mobilisation",
            "demobilisation": "Demobilisation",
            "project_assignment": "Project Assignment",
            "roster": "Roster",
            "timesheet": "Project Timesheet",
            "competency": "Competency Matrix",
            "certificate": "Certificate / Medical",
        },
        approver_roles=(RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.GA, RoleName.HR),
    ),
    "budget_bi": ModuleDefinition(
        key="budget_bi",
        label="Budget & BI",
        description="Annual budgets, project forecasts, cash flow, profitability, and management plans",
        path="/budget-bi",
        prefix="BUD",
        record_types={
            "annual_budget": "Annual Budget",
            "project_forecast": "Project Forecast",
            "cash_flow_plan": "Cash Flow Plan",
            "profitability_review": "Profitability Review",
            "management_kpi": "Management KPI",
        },
        approver_roles=(RoleName.MD, RoleName.FINANCE, RoleName.COST_CONTROL, RoleName.PROJECT_CONTROL),
    ),
}


# One explicit state machine prevents pages from inventing incompatible status flows.
STATUS_TRANSITIONS: dict[str, dict[str, str]] = {
    "draft": {"submit": "submitted", "cancel": "cancelled"},
    "submitted": {"review": "in_review", "approve": "approved", "reject": "rejected", "cancel": "cancelled"},
    "in_review": {"approve": "approved", "reject": "rejected", "cancel": "cancelled"},
    "approved": {"activate": "active", "complete": "completed", "close": "closed"},
    "active": {"complete": "completed", "close": "closed"},
    "rejected": {"reopen": "draft", "cancel": "cancelled"},
    "completed": {"close": "closed", "reopen": "active"},
    "cancelled": {"reopen": "draft"},
    "closed": {},
}

EDITABLE_STATUSES = frozenset({"draft", "rejected"})
DELETABLE_STATUSES = frozenset({"draft", "rejected", "cancelled"})
APPROVER_ACTIONS = frozenset({"review", "approve", "reject", "activate", "complete", "close"})


def next_status(current_status: str, action: str) -> str | None:
    """Resolve a workflow action without allowing arbitrary status writes."""
    return STATUS_TRANSITIONS.get(current_status, {}).get(action)

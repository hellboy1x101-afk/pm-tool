from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator

from app.schemas.base import ORMModel


# ── Enums (mirror models for Pydantic) ──

# Re-use the model enum so the API accepts exactly the roles the database stores
# (the old copy was missing super_admin). Who may grant which role is checked in the routers.
from app.models.models import TechnicalRole  # noqa: E402

class BusinessRole(str, Enum):
    partner = "partner"
    director = "director"
    ca_manager = "ca_manager"
    paid_assistant = "paid_assistant"
    staff = "staff"
    article = "article"
    data_analyst = "data_analyst"

class EngagementType(str, Enum):
    statutory_audit = "statutory_audit"
    internal_audit = "internal_audit"
    tax_audit = "tax_audit"
    consulting = "consulting"
    special_assignment = "special_assignment"
    other = "other"

class RecurrencePattern(str, Enum):
    one_off = "one_off"
    weekly = "weekly"
    fortnightly = "fortnightly"
    monthly = "monthly"
    quarterly = "quarterly"
    annual = "annual"

class EngagementStatus(str, Enum):
    active = "active"
    on_hold = "on_hold"
    completed = "completed"
    lost_client = "lost_client"

class InstanceStatus(str, Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    delayed = "delayed"

class LeaveType(str, Enum):
    sick = "sick"
    vacation = "vacation"
    exam_leave = "exam_leave"
    other = "other"

class LeaveStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# ── User ──

class UserCreate(BaseModel):
    email: str
    display_name: str
    technical_role: TechnicalRole = TechnicalRole.viewer
    azure_oid: Optional[str] = None

class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    technical_role: Optional[TechnicalRole] = None
    is_active: Optional[bool] = None

class UserRead(ORMModel):
    id: int
    email: str
    display_name: str
    is_active: bool
    azure_oid: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TeamMemberCreate(BaseModel):
    employee_code: Optional[str] = None
    name: str
    email: str
    business_role: BusinessRole
    is_oversight_only: bool = False
    seniority_level: Optional[str] = None
    date_of_joining: Optional[date] = None
    date_of_relieving: Optional[date] = None

class TeamMemberUpdate(BaseModel):
    employee_code: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    business_role: Optional[BusinessRole] = None
    is_oversight_only: Optional[bool] = None
    seniority_level: Optional[str] = None
    date_of_joining: Optional[date] = None
    date_of_relieving: Optional[date] = None
    is_active: Optional[bool] = None

class TeamMemberRead(ORMModel):
    id: int
    employee_code: Optional[str] = None
    name: str
    email: str
    business_role: BusinessRole
    is_oversight_only: bool
    seniority_level: Optional[str] = None
    date_of_joining: Optional[date] = None
    date_of_relieving: Optional[date] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ── Client ──

class ClientCreate(BaseModel):
    name: str
    code: Optional[str] = None
    industry: Optional[str] = None

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    industry: Optional[str] = None
    is_active: Optional[bool] = None

class ClientRead(ORMModel):
    id: int
    name: str
    code: Optional[str] = None
    industry: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ── Engagement ──

class EngagementCreate(BaseModel):
    client_id: int
    name: str
    engagement_type: EngagementType
    recurrence_pattern: RecurrencePattern
    default_team_template_json: Optional[dict] = None
    start_date: date
    end_date: Optional[date] = None
    status: EngagementStatus = EngagementStatus.active

class EngagementUpdate(BaseModel):
    name: Optional[str] = None
    engagement_type: Optional[EngagementType] = None
    recurrence_pattern: Optional[RecurrencePattern] = None
    default_team_template_json: Optional[dict] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[EngagementStatus] = None
    is_active: Optional[bool] = None

class EngagementRead(ORMModel):
    id: int
    client_id: int
    name: str
    engagement_type: EngagementType
    recurrence_pattern: RecurrencePattern
    default_team_template_json: Optional[dict] = None
    start_date: date
    end_date: Optional[date] = None
    status: EngagementStatus
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ── EngagementInstance ──

class EngagementInstanceCreate(BaseModel):
    engagement_id: int
    period_label: str
    start_date: date
    end_date: date
    due_date: Optional[date] = None
    status: InstanceStatus = InstanceStatus.planned

class EngagementInstanceUpdate(BaseModel):
    period_label: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    due_date: Optional[date] = None
    status: Optional[InstanceStatus] = None

class EngagementInstanceRead(ORMModel):
    id: int
    engagement_id: int
    period_label: str
    start_date: date
    end_date: date
    due_date: Optional[date] = None
    status: InstanceStatus
    created_at: datetime
    updated_at: datetime


# ── Assignment ──

class AssignmentCreate(BaseModel):
    team_member_id: int
    engagement_instance_id: int
    role_on_engagement: Optional[str] = None
    allocation_percent: int
    start_date: date
    end_date: date

    @field_validator("allocation_percent")
    @classmethod
    def check_range(cls, v):
        if v < 1 or v > 100:
            raise ValueError("allocation_percent must be between 1 and 100")
        return v

    @field_validator("end_date")
    @classmethod
    def check_dates(cls, v, info):
        if "start_date" in info.data and v < info.data["start_date"]:
            raise ValueError("end_date must be on or after start_date")
        return v

class AssignmentUpdate(BaseModel):
    role_on_engagement: Optional[str] = None
    allocation_percent: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    @field_validator("allocation_percent")
    @classmethod
    def check_range(cls, v):
        if v is not None and (v < 1 or v > 100):
            raise ValueError("allocation_percent must be between 1 and 100")
        return v

class AssignmentRead(ORMModel):
    id: int
    team_member_id: int
    engagement_instance_id: int
    role_on_engagement: Optional[str] = None
    allocation_percent: int
    start_date: date
    end_date: date
    created_by_user_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


# ── Leave ──

class LeaveCreate(BaseModel):
    team_member_id: int
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: Optional[str] = None

class LeaveUpdate(BaseModel):
    leave_type: Optional[LeaveType] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[LeaveStatus] = None
    reason: Optional[str] = None

class LeaveRead(ORMModel):
    id: int
    team_member_id: int
    leave_type: LeaveType
    start_date: date
    end_date: date
    status: LeaveStatus
    reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ── SystemSetting ──

class SystemSettingUpdate(BaseModel):
    value: str

class SystemSettingRead(ORMModel):
    id: int
    key: str
    value: str
    description: Optional[str] = None
    updated_by_user_id: Optional[int] = None


# ── Bulk Upload ──

class BulkUploadRow(BaseModel):
    employee_code: Optional[str] = None
    name: str
    email: str
    business_role: BusinessRole
    is_oversight_only: bool = False
    seniority_level: Optional[str] = None
    date_of_joining: Optional[date] = None
    date_of_relieving: Optional[date] = None

class BulkUploadError(BaseModel):
    row: int
    field: str
    reason: str

class BulkUploadResult(BaseModel):
    status: str  # "success" | "partial_success" | "failure"
    inserted: int
    failed: int
    errors: list[BulkUploadError] = []


# ── Pagination ──

class PaginatedResponse(BaseModel):
    items: list
    total: int
    limit: int
    offset: int


# ── Dashboard ──

class BenchItem(BaseModel):
    team_member_id: int
    name: str
    business_role: BusinessRole
    last_assignment_end: Optional[date] = None
    days_since_last_assignment: Optional[int] = None
    is_rolling_off: bool = False

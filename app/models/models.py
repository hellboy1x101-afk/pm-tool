import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class TechnicalRole(str, enum.Enum):
    super_admin = "super_admin"
    admin = "admin"
    moderator = "moderator"
    viewer = "viewer"


class BusinessRole(str, enum.Enum):
    partner = "partner"
    director = "director"
    ca_manager = "ca_manager"
    paid_assistant = "paid_assistant"
    staff = "staff"
    article = "article"
    data_analyst = "data_analyst"


class EngagementType(str, enum.Enum):
    statutory_audit = "statutory_audit"
    internal_audit = "internal_audit"
    tax_audit = "tax_audit"
    consulting = "consulting"
    special_assignment = "special_assignment"
    other = "other"


class RecurrencePattern(str, enum.Enum):
    one_off = "one_off"
    weekly = "weekly"
    fortnightly = "fortnightly"
    monthly = "monthly"
    quarterly = "quarterly"
    annual = "annual"


class EngagementStatus(str, enum.Enum):
    active = "active"
    on_hold = "on_hold"
    completed = "completed"
    lost_client = "lost_client"


class InstanceStatus(str, enum.Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    delayed = "delayed"


class LeaveType(str, enum.Enum):
    sick = "sick"
    vacation = "vacation"
    exam_leave = "exam_leave"
    other = "other"


class LeaveStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ResourceType(str, enum.Enum):
    assignment = "assignment"
    engagement = "engagement"
    client = "client"
    team_member = "team_member"
    leave = "leave"


class OperationType(str, enum.Enum):
    create = "create"
    update = "update"
    delete = "delete"


class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# ── Multi-Tenancy Models ──


class Firm(Base):
    __tablename__ = "firms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    logo_url = Column(String(500), nullable=True)
    allowed_domains = Column(Text, nullable=True)  # Comma-separated: "pkfindia.com,pkf.in"
    is_active = Column(Boolean, default=True, nullable=False)

    # License fields
    license_key = Column(String(255), nullable=True)  # Full key for validation
    license_key_hash = Column(String(64), nullable=True, index=True)
    license_tier = Column(String(50), nullable=True)  # "standard", "enterprise"
    license_expires_at = Column(DateTime(timezone=True), nullable=True)  # NULL = perpetual
    license_activated_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    branches = relationship("Branch", back_populates="firm")
    firm_users = relationship("FirmUser", back_populates="firm")
    approval_rules = relationship("ApprovalRule", back_populates="firm")


class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    city = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    firm = relationship("Firm", back_populates="branches")
    team_members = relationship("TeamMember", back_populates="branch")


class FirmUser(Base):
    __tablename__ = "firm_users"
    __table_args__ = (
        UniqueConstraint("user_id", "firm_id", name="uq_firm_user"),
        Index("ix_firm_user_firm_id", "firm_id"),
        Index("ix_firm_user_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    technical_role = Column(Enum(TechnicalRole), nullable=False, default=TechnicalRole.viewer)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="firm_users")
    firm = relationship("Firm", back_populates="firm_users")


class ApprovalRule(Base):
    __tablename__ = "approval_rules"
    __table_args__ = (
        UniqueConstraint("firm_id", "resource_type", "operation", name="uq_approval_rule"),
    )

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    resource_type = Column(Enum(ResourceType), nullable=False)
    operation = Column(Enum(OperationType), nullable=False)
    is_enabled = Column(Boolean, default=False, nullable=False)
    approver_role = Column(Enum(TechnicalRole), nullable=False, default=TechnicalRole.admin)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    firm = relationship("Firm", back_populates="approval_rules")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    resource_type = Column(Enum(ResourceType), nullable=False)
    resource_id = Column(Integer, nullable=True)
    operation = Column(Enum(OperationType), nullable=False)
    requested_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(Enum(ApprovalStatus), nullable=False, default=ApprovalStatus.pending)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    firm = relationship("Firm")
    requested_by = relationship("User", foreign_keys=[requested_by_user_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_user_id])

    __table_args__ = (
        Index("ix_approval_request_firm_status", "firm_id", "status"),
    )


class ExtensionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ExtensionRequest(Base):
    """Request to extend allocation beyond original plan."""
    __tablename__ = "extension_requests"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    team_member_id = Column(Integer, ForeignKey("team_members.id", ondelete="RESTRICT"), nullable=False)
    engagement_instance_id = Column(Integer, ForeignKey("engagement_instances.id", ondelete="RESTRICT"), nullable=False)
    allocation_percent = Column(Integer, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    role_on_engagement = Column(String(255), nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(Enum(ExtensionStatus), nullable=False, default=ExtensionStatus.pending)
    requested_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    firm = relationship("Firm")
    team_member = relationship("TeamMember")
    engagement_instance = relationship("EngagementInstance")
    requested_by = relationship("User", foreign_keys=[requested_by_user_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_user_id])

    __table_args__ = (
        Index("ix_extension_request_firm_status", "firm_id", "status"),
        CheckConstraint("allocation_percent >= 0 AND allocation_percent <= 100", name="ck_extension_allocation_range"),
        CheckConstraint("end_date >= start_date", name="ck_extension_date_order"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=True)
    totp_secret = Column(String(64), nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    azure_oid = Column(String(255), unique=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)

    firm_users = relationship("FirmUser", back_populates="user")


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    purpose = Column(String(50), nullable=False, default="verification")
    otp_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    hourly_count = Column(Integer, nullable=False, default=1)
    hourly_reset_at = Column(DateTime(timezone=True), nullable=False)
    last_sent_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id", ondelete="SET NULL"), nullable=True)
    employee_code = Column(String(50), unique=True, nullable=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    business_role = Column(Enum(BusinessRole), nullable=False)
    is_oversight_only = Column(Boolean, default=False, nullable=False)
    seniority_level = Column(String(100), nullable=True)
    date_of_joining = Column(Date, nullable=True)
    date_of_relieving = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    firm = relationship("Firm")
    branch = relationship("Branch", back_populates="team_members")
    assignments = relationship("Assignment", back_populates="team_member")
    leaves = relationship("Leave", back_populates="team_member")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(255), nullable=False)
    code = Column(String(50), unique=True, nullable=True)
    industry = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    firm = relationship("Firm")
    engagements = relationship("Engagement", back_populates="client")


class Engagement(Base):
    __tablename__ = "engagements"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(255), nullable=False)
    engagement_type = Column(Enum(EngagementType), nullable=False)
    recurrence_pattern = Column(Enum(RecurrencePattern), nullable=False)
    default_team_template_json = Column(JSON, nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    status = Column(Enum(EngagementStatus), nullable=False, default=EngagementStatus.active)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    client = relationship("Client", back_populates="engagements")
    instances = relationship("EngagementInstance", back_populates="engagement")


class EngagementInstance(Base):
    __tablename__ = "engagement_instances"

    id = Column(Integer, primary_key=True, index=True)
    engagement_id = Column(
        Integer, ForeignKey("engagements.id", ondelete="RESTRICT"), nullable=False
    )
    period_label = Column(String(255), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    status = Column(Enum(InstanceStatus), nullable=False, default=InstanceStatus.planned)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    engagement = relationship("Engagement", back_populates="instances")
    assignments = relationship("Assignment", back_populates="engagement_instance")

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_engagement_instance_date_order"),
    )


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    team_member_id = Column(
        Integer, ForeignKey("team_members.id", ondelete="RESTRICT"), nullable=False
    )
    engagement_instance_id = Column(
        Integer, ForeignKey("engagement_instances.id", ondelete="RESTRICT"), nullable=False
    )
    role_on_engagement = Column(String(255), nullable=True)
    allocation_percent = Column(Integer, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    team_member = relationship("TeamMember", back_populates="assignments")
    engagement_instance = relationship("EngagementInstance", back_populates="assignments")
    created_by = relationship("User")

    __table_args__ = (
        Index("ix_assignment_team_member_dates", "team_member_id", "start_date", "end_date"),
        Index("ix_assignment_instance", "engagement_instance_id"),
        Index("ix_assignment_team_instance", "team_member_id", "engagement_instance_id"),
        CheckConstraint("allocation_percent >= 0 AND allocation_percent <= 100", name="ck_assignment_allocation_range"),
        CheckConstraint("end_date >= start_date", name="ck_assignment_date_order"),
    )


class Leave(Base):
    __tablename__ = "leaves"

    id = Column(Integer, primary_key=True, index=True)
    team_member_id = Column(
        Integer, ForeignKey("team_members.id", ondelete="RESTRICT"), nullable=False
    )
    leave_type = Column(Enum(LeaveType), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(Enum(LeaveStatus), nullable=False, default=LeaveStatus.pending)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    team_member = relationship("TeamMember", back_populates="leaves")

    __table_args__ = (
        Index("ix_leave_team_member_dates", "team_member_id", "start_date", "end_date"),
        CheckConstraint("end_date >= start_date", name="ck_leave_date_order"),
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id", ondelete="CASCADE"), nullable=True)
    key = Column(String(255), nullable=False)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    updated_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    firm = relationship("Firm")

    # One platform default per key, and at most one override per firm per key.
    __table_args__ = (
        Index("uq_system_settings_default_key", "key", unique=True, postgresql_where=firm_id.is_(None)),
        Index("uq_system_settings_firm_key", "firm_id", "key", unique=True, postgresql_where=firm_id.isnot(None)),
    )


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id = Column(Integer, primary_key=True, index=True)
    recipient_email = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(50), default="pending", nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    assignment_id = Column(
        Integer, ForeignKey("assignments.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)

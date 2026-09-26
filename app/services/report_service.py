"""Report service — generation, export, role-based access."""

import csv
import io
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, and_, case
from sqlalchemy.orm import Session

from app.models.models import (
    Assignment, Client, Engagement, EngagementInstance,
    FirmUser, Leave, LeaveStatus, TeamMember, User,
)
from app.models.firm_business_role import FirmBusinessRole

# ── Report Permissions ──

REPORT_PERMISSIONS = {
    # Tier 1: All roles
    "team_utilization": ["viewer", "moderator", "admin", "super_admin"],
    "bench_report": ["viewer", "moderator", "admin", "super_admin"],
    "rolling_off_soon": ["viewer", "moderator", "admin", "super_admin"],
    "leave_summary": ["viewer", "moderator", "admin", "super_admin"],
    "engagement_status": ["viewer", "moderator", "admin", "super_admin"],
    
    # Tier 2: Moderator+
    "allocation_by_engagement": ["moderator", "admin", "super_admin"],
    "team_capacity_planning": ["moderator", "admin", "super_admin"],
    "client_portfolio": ["moderator", "admin", "super_admin"],
    
    # Tier 3: Admin only (costing)
    "budget_vs_actual": ["admin", "super_admin"],
    "cost_by_role": ["admin", "super_admin"],
    "firm_cost_summary": ["admin", "super_admin"],
}

REPORT_CATALOG = [
    # Tier 1
    {"slug": "team_utilization", "name": "Team Utilization", "description": "Allocation % per team member", "tier": 1},
    {"slug": "bench_report", "name": "Bench Report", "description": "Members with 0% allocation", "tier": 1},
    {"slug": "rolling_off_soon", "name": "Rolling Off Soon", "description": "Assignments ending within X days", "tier": 1},
    {"slug": "leave_summary", "name": "Leave Summary", "description": "Approved/pending leaves", "tier": 1},
    {"slug": "engagement_status", "name": "Engagement Status", "description": "Status breakdown of engagements", "tier": 1},
    # Tier 2
    {"slug": "allocation_by_engagement", "name": "Allocation by Engagement", "description": "Team allocated per engagement", "tier": 2},
    {"slug": "team_capacity_planning", "name": "Team Capacity Planning", "description": "Available capacity per role", "tier": 2},
    {"slug": "client_portfolio", "name": "Client Portfolio", "description": "Engagement summary per client", "tier": 2},
    # Tier 3
    {"slug": "budget_vs_actual", "name": "Budget vs Actual", "description": "Cost comparison per engagement", "tier": 3},
    {"slug": "cost_by_role", "name": "Cost by Business Role", "description": "Aggregated cost per role", "tier": 3},
    {"slug": "firm_cost_summary", "name": "Firm Cost Summary", "description": "Overall cost dashboard", "tier": 3},
]


def list_available_reports(user_role: str) -> list[dict]:
    """List reports available to the user's role."""
    available = []
    for report in REPORT_CATALOG:
        allowed_roles = REPORT_PERMISSIONS.get(report["slug"], [])
        if user_role in allowed_roles:
            available.append(report)
    return available


def check_report_access(user_role: str, report_name: str) -> bool:
    """Check if user has access to a report."""
    allowed_roles = REPORT_PERMISSIONS.get(report_name, [])
    return user_role in allowed_roles


# ── Cost Calculation Helpers ──

def get_active_cost_rate(db: Session, firm_id: int, role_code: str, as_of_date: date = None) -> Optional[dict]:
    """Get the active cost rate for a role in a firm."""
    if as_of_date is None:
        as_of_date = date.today()
    
    rate = db.query(FirmBusinessRole).filter(
        FirmBusinessRole.firm_id == firm_id,
        FirmBusinessRole.role_code == role_code,
        FirmBusinessRole.is_enabled == True,
    ).first()
    
    if rate and rate.rate_value:
        return {
            "rate_type": rate.rate_type,
            "rate_value": float(rate.rate_value),
            "currency": rate.currency,
        }
    return None


def calculate_cost(allocation_pct: float, rate_info: dict, working_days: int, hours_per_day: int = 8) -> float:
    """Calculate cost for an assignment."""
    if not rate_info or not rate_info.get("rate_value"):
        return 0.0
    
    alloc = allocation_pct / 100
    rate = rate_info["rate_value"]
    
    if rate_info["rate_type"] == "daily":
        return alloc * rate * working_days
    else:  # hourly
        return alloc * rate * hours_per_day * working_days


def working_days_between(start: date, end: date) -> int:
    """Calculate working days between two dates (excluding weekends)."""
    if start > end:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:  # Monday=0, Friday=4
            days += 1
        current += timedelta(days=1)
    return days


# ── Report Builders ──

def build_team_utilization(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Team utilization report."""
    today = date.today()
    
    # Get all active team members
    members = db.query(TeamMember).filter(
        TeamMember.firm_id == firm_id,
        TeamMember.is_active == True,
    ).all()
    
    rows = []
    for member in members:
        # Get current allocations
        allocations = db.query(func.sum(Assignment.allocation_percent)).filter(
            Assignment.team_member_id == member.id,
            Assignment.start_date <= today,
            Assignment.end_date >= today,
        ).scalar() or 0
        
        # Get assignment count
        assignment_count = db.query(func.count(Assignment.id)).filter(
            Assignment.team_member_id == member.id,
            Assignment.start_date <= today,
            Assignment.end_date >= today,
        ).scalar() or 0
        
        # Determine status
        if allocations == 0:
            status = "bench"
        elif allocations <= 40:
            status = "light"
        elif allocations <= 80:
            status = "medium"
        elif allocations <= 100:
            status = "heavy"
        else:
            status = "over"
        
        rows.append({
            "member_id": member.id,
            "name": member.name,
            "email": member.email,
            "business_role": member.business_role.value if member.business_role else "",
            "allocation_pct": allocations,
            "assignment_count": assignment_count,
            "status": status,
            "is_oversight_only": member.is_oversight_only,
        })
    
    # Sort by allocation descending
    rows.sort(key=lambda x: x["allocation_pct"], reverse=True)
    
    # Summary
    total = len(rows)
    bench = sum(1 for r in rows if r["status"] == "bench")
    over = sum(1 for r in rows if r["status"] == "over")
    avg_alloc = sum(r["allocation_pct"] for r in rows) / total if total else 0
    
    return {
        "title": "Team Utilization",
        "columns": ["Name", "Email", "Role", "Allocation %", "Assignments", "Status"],
        "rows": rows,
        "summary": {
            "total_members": total,
            "bench_count": bench,
            "over_allocated": over,
            "avg_allocation": round(avg_alloc, 1),
        },
    }


def build_bench_report(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Bench report — members with 0% allocation."""
    today = date.today()
    
    # Get members with no current assignments
    assigned_member_ids = db.query(Assignment.team_member_id).filter(
        Assignment.start_date <= today,
        Assignment.end_date >= today,
    ).subquery()
    
    bench_members = db.query(TeamMember).filter(
        TeamMember.firm_id == firm_id,
        TeamMember.is_active == True,
        ~TeamMember.id.in_(assigned_member_ids),
    ).all()
    
    rows = []
    for member in bench_members:
        # Calculate days on bench (from last assignment end date)
        last_assignment = db.query(func.max(Assignment.end_date)).filter(
            Assignment.team_member_id == member.id,
        ).scalar()
        
        days_on_bench = (today - last_assignment).days if last_assignment else None
        
        rows.append({
            "member_id": member.id,
            "name": member.name,
            "email": member.email,
            "business_role": member.business_role.value if member.business_role else "",
            "days_on_bench": days_on_bench,
            "date_of_joining": member.date_of_joining.isoformat() if member.date_of_joining else "",
        })
    
    return {
        "title": "Bench Report",
        "columns": ["Name", "Email", "Role", "Days on Bench", "Date of Joining"],
        "rows": rows,
        "summary": {"total_on_bench": len(rows)},
    }


def build_rolling_off_soon(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Members whose assignments end within X days."""
    from app.services.settings_service import get_setting_value
    
    days = int(get_setting_value(db, "bench_rolloff_days", firm_id, "7"))
    today = date.today()
    cutoff = today + timedelta(days=days)
    
    assignments = db.query(Assignment).join(TeamMember).filter(
        TeamMember.firm_id == firm_id,
        TeamMember.is_active == True,
        Assignment.end_date >= today,
        Assignment.end_date <= cutoff,
    ).order_by(Assignment.end_date).all()
    
    rows = []
    for a in assignments:
        eng_name = ""
        if a.engagement_instance and a.engagement_instance.engagement:
            eng_name = a.engagement_instance.engagement.name
        
        rows.append({
            "assignment_id": a.id,
            "member_name": a.team_member.name if a.team_member else "",
            "member_id": a.team_member_id,
            "business_role": a.team_member.business_role.value if a.team_member and a.team_member.business_role else "",
            "engagement": eng_name,
            "allocation_pct": a.allocation_percent,
            "end_date": a.end_date.isoformat(),
            "days_remaining": (a.end_date - today).days,
        })
    
    return {
        "title": f"Rolling Off Within {days} Days",
        "columns": ["Member", "Role", "Engagement", "Allocation %", "End Date", "Days Remaining"],
        "rows": rows,
        "summary": {"total_rolling_off": len(rows), "days_threshold": days},
    }


def build_leave_summary(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Leave summary report."""
    today = date.today()
    start_date = filters.get("start_date", today - timedelta(days=30)) if filters else today - timedelta(days=30)
    end_date = filters.get("end_date", today + timedelta(days=30)) if filters else today + timedelta(days=30)
    
    leaves = db.query(Leave).join(TeamMember).filter(
        TeamMember.firm_id == firm_id,
        TeamMember.is_active == True,
        Leave.start_date <= end_date,
        Leave.end_date >= start_date,
    ).order_by(Leave.start_date).all()
    
    rows = []
    for leave in leaves:
        days_count = (leave.end_date - leave.start_date).days + 1
        
        rows.append({
            "leave_id": leave.id,
            "member_name": leave.team_member.name if leave.team_member else "",
            "member_id": leave.team_member_id,
            "business_role": leave.team_member.business_role.value if leave.team_member and leave.team_member.business_role else "",
            "leave_type": leave.leave_type.value if leave.leave_type else "",
            "start_date": leave.start_date.isoformat(),
            "end_date": leave.end_date.isoformat(),
            "days_count": days_count,
            "status": leave.status.value if leave.status else "",
        })
    
    # Summary
    pending = sum(1 for r in rows if r["status"] == "pending")
    approved = sum(1 for r in rows if r["status"] == "approved")
    total_days = sum(r["days_count"] for r in rows if r["status"] == "approved")
    
    return {
        "title": "Leave Summary",
        "columns": ["Member", "Role", "Type", "Start Date", "End Date", "Days", "Status"],
        "rows": rows,
        "summary": {"pending": pending, "approved": approved, "total_leave_days": total_days},
    }


def build_engagement_status(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Engagement status breakdown."""
    engagements = db.query(Engagement).join(Client).filter(
        Client.firm_id == firm_id,
    ).all()
    
    rows = []
    status_counts = {"active": 0, "on_hold": 0, "completed": 0, "lost_client": 0}
    
    for eng in engagements:
        # Count instances
        instance_count = db.query(func.count(EngagementInstance.id)).filter(
            EngagementInstance.engagement_id == eng.id,
        ).scalar() or 0
        
        completed_count = db.query(func.count(EngagementInstance.id)).filter(
            EngagementInstance.engagement_id == eng.id,
            EngagementInstance.status == "completed",
        ).scalar() or 0
        
        status = eng.status.value if eng.status else ""
        status_counts[status] = status_counts.get(status, 0) + 1
        
        rows.append({
            "engagement_id": eng.id,
            "name": eng.name,
            "client_name": eng.client.name if eng.client else "",
            "engagement_type": eng.engagement_type.value if eng.engagement_type else "",
            "status": status,
            "instance_count": instance_count,
            "completed_count": completed_count,
            "start_date": eng.start_date.isoformat() if eng.start_date else "",
            "end_date": eng.end_date.isoformat() if eng.end_date else "",
        })
    
    return {
        "title": "Engagement Status",
        "columns": ["Name", "Client", "Type", "Status", "Instances", "Completed", "Start", "End"],
        "rows": rows,
        "summary": status_counts,
    }


def build_budget_vs_actual(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Budget vs actual cost report (admin only)."""
    today = date.today()
    start_date = filters.get("start_date", today - timedelta(days=90)) if filters else today - timedelta(days=90)
    end_date = filters.get("end_date", today) if filters else today
    
    # Get all assignments in period
    assignments = db.query(Assignment).join(TeamMember).filter(
        TeamMember.firm_id == firm_id,
        Assignment.start_date <= end_date,
        Assignment.end_date >= start_date,
    ).all()
    
    rows = []
    total_budget = 0
    total_actual = 0
    
    for a in assignments:
        if not a.team_member or not a.team_member.business_role:
            continue
        
        role_code = a.team_member.business_role.value
        rate_info = get_active_cost_rate(db, firm_id, role_code)
        
        if not rate_info:
            continue
        
        # Calculate working days in period
        actual_start = max(a.start_date, start_date)
        actual_end = min(a.end_date, end_date)
        working_days = working_days_between(actual_start, actual_end)
        
        # Budget = full assignment cost
        budget = calculate_cost(a.allocation_percent, rate_info, working_days_between(a.start_date, a.end_date))
        
        # Actual = cost for period only
        actual = calculate_cost(a.allocation_percent, rate_info, working_days)
        
        total_budget += budget
        total_actual += actual
        
        eng_name = ""
        if a.engagement_instance and a.engagement_instance.engagement:
            eng_name = a.engagement_instance.engagement.name
        
        rows.append({
            "member_name": a.team_member.name,
            "business_role": role_code,
            "engagement": eng_name,
            "allocation_pct": a.allocation_percent,
            "working_days": working_days,
            "budget": round(budget, 2),
            "actual": round(actual, 2),
            "variance": round(budget - actual, 2),
        })
    
    # Group by role
    by_role = {}
    for row in rows:
        role = row["business_role"]
        if role not in by_role:
            by_role[role] = {"budget": 0, "actual": 0}
        by_role[role]["budget"] += row["budget"]
        by_role[role]["actual"] += row["actual"]
    
    return {
        "title": "Budget vs Actual",
        "columns": ["Member", "Role", "Engagement", "Allocation %", "Working Days", "Budget", "Actual", "Variance"],
        "rows": rows,
        "summary": {
            "total_budget": round(total_budget, 2),
            "total_actual": round(total_actual, 2),
            "total_variance": round(total_budget - total_actual, 2),
            "by_role": by_role,
        },
    }


def build_cost_by_role(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Cost breakdown by business role."""
    today = date.today()
    start_date = filters.get("start_date", today - timedelta(days=90)) if filters else today - timedelta(days=90)
    end_date = filters.get("end_date", today) if filters else today
    
    # Get enabled roles
    roles = db.query(FirmBusinessRole).filter(
        FirmBusinessRole.firm_id == firm_id,
        FirmBusinessRole.is_enabled == True,
    ).all()
    
    rows = []
    total_budget = 0
    total_actual = 0
    
    for role in roles:
        # Get assignments for this role
        assignments = db.query(Assignment).join(TeamMember).filter(
            TeamMember.firm_id == firm_id,
            TeamMember.business_role == role.role_code,
            Assignment.start_date <= end_date,
            Assignment.end_date >= start_date,
        ).all()
        
        rate_info = {"rate_type": role.rate_type, "rate_value": float(role.rate_value) if role.rate_value else 0, "currency": role.currency}
        
        member_count = db.query(func.count(TeamMember.id)).filter(
            TeamMember.firm_id == firm_id,
            TeamMember.business_role == role.role_code,
            TeamMember.is_active == True,
        ).scalar() or 0
        
        budget = 0
        actual = 0
        
        for a in assignments:
            actual_start = max(a.start_date, start_date)
            actual_end = min(a.end_date, end_date)
            
            budget += calculate_cost(a.allocation_percent, rate_info, working_days_between(a.start_date, a.end_date))
            actual += calculate_cost(a.allocation_percent, rate_info, working_days_between(actual_start, actual_end))
        
        total_budget += budget
        total_actual += actual
        
        rows.append({
            "role_code": role.role_code,
            "role_name": role.role_code.replace("_", " ").title(),
            "member_count": member_count,
            "rate_type": role.rate_type,
            "rate_value": float(role.rate_value) if role.rate_value else 0,
            "budget": round(budget, 2),
            "actual": round(actual, 2),
        })
    
    return {
        "title": "Cost by Business Role",
        "columns": ["Role", "Members", "Rate Type", "Rate", "Budget", "Actual"],
        "rows": rows,
        "summary": {"total_budget": round(total_budget, 2), "total_actual": round(total_actual, 2)},
    }


def build_firm_cost_summary(db: Session, firm_id: int, filters: dict = None) -> dict:
    """Firm-wide cost summary."""
    cost_by_role = build_cost_by_role(db, firm_id, filters)
    budget_vs_actual = build_budget_vs_actual(db, firm_id, filters)
    utilization = build_team_utilization(db, firm_id, filters)
    
    return {
        "title": "Firm Cost Summary",
        "summary": {
            "total_budget": cost_by_role["summary"]["total_budget"],
            "total_actual": cost_by_role["summary"]["total_actual"],
            "total_members": utilization["summary"]["total_members"],
            "avg_allocation": utilization["summary"]["avg_allocation"],
            "bench_count": utilization["summary"]["bench_count"],
            "cost_by_role": cost_by_role["rows"],
        },
    }


# ── Report Dispatcher ──

REPORT_BUILDERS = {
    "team_utilization": build_team_utilization,
    "bench_report": build_bench_report,
    "rolling_off_soon": build_rolling_off_soon,
    "leave_summary": build_leave_summary,
    "engagement_status": build_engagement_status,
    "budget_vs_actual": build_budget_vs_actual,
    "cost_by_role": build_cost_by_role,
    "firm_cost_summary": build_firm_cost_summary,
}


def generate_report(db: Session, firm_id: int, report_name: str, filters: dict = None) -> dict:
    """Generate a report by name."""
    builder = REPORT_BUILDERS.get(report_name)
    if not builder:
        return {"error": f"Report '{report_name}' not found"}
    return builder(db, firm_id, filters)


def stream_report_csv(db: Session, firm_id: int, report_name: str, filters: dict = None):
    """Stream report as CSV."""
    report = generate_report(db, firm_id, report_name, filters)
    
    if "error" in report:
        yield report["error"]
        return
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(report["columns"])
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)
    
    # Write rows
    for row in report["rows"]:
        writer.writerow([row.get(col.lower().replace(" ", "_").replace("%", "pct"), "") for col in report["columns"]])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

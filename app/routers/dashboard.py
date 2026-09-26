from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.auth.auth import get_current_user
from app.database import get_db
from app.models.models import (
    ApprovalRequest,
    Assignment,
    Client,
    Engagement,
    EngagementInstance,
    FirmUser,
    Leave,
    TeamMember,
    TechnicalRole,
    User,
)
from app.services import report_service
from app.csrf_utils import get_csrf_token
from app.templates_setup import templates

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse)
def home_dashboard(
    request: Request,
    business_role: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    today = date.today()
    thirty_days_later = today + timedelta(days=30)
    firm_id = request.session.get("firm_id")

    # Every query below is limited to the active firm. Engagements are owned via their
    # client; assignments and leaves via their team member.
    in_firm_member = TeamMember.firm_id == firm_id

    # ── Team metrics ──
    total_members = db.query(func.count(TeamMember.id)).filter(in_firm_member, TeamMember.is_active == True).scalar() or 0
    total_admins = db.query(func.count(FirmUser.id)).filter(
        FirmUser.firm_id == firm_id,
        FirmUser.technical_role.in_([TechnicalRole.admin, TechnicalRole.super_admin]),
        FirmUser.is_active == True,
    ).scalar() or 0

    # ── Engagement metrics ──
    active_engagements = (
        db.query(func.count(Engagement.id)).join(Client, Engagement.client_id == Client.id)
        .filter(Client.firm_id == firm_id, Engagement.status == "active").scalar() or 0
    )
    total_clients = db.query(func.count(Client.id)).filter(Client.firm_id == firm_id, Client.is_active == True).scalar() or 0

    # ── Assignment metrics ──
    current_assignments = (
        db.query(func.count(Assignment.id)).join(TeamMember, Assignment.team_member_id == TeamMember.id)
        .filter(in_firm_member, Assignment.start_date <= today, Assignment.end_date >= today)
        .scalar() or 0
    )

    assigned_member_ids = select(Assignment.team_member_id).where(
        Assignment.start_date <= today, Assignment.end_date >= today
    ).distinct()
    bench_count = (
        db.query(func.count(TeamMember.id))
        .filter(in_firm_member, TeamMember.is_active == True, TeamMember.id.not_in(assigned_member_ids))
        .scalar() or 0
    )

    # ── Leave metrics ──
    pending_leaves = (
        db.query(func.count(Leave.id)).join(TeamMember, Leave.team_member_id == TeamMember.id)
        .filter(in_firm_member, Leave.status == "pending").scalar() or 0
    )
    upcoming_leaves = (
        db.query(func.count(Leave.id)).join(TeamMember, Leave.team_member_id == TeamMember.id)
        .filter(in_firm_member, Leave.status == "approved", Leave.start_date > today, Leave.start_date <= thirty_days_later)
        .scalar() or 0
    )

    # ── Recent assignments ──
    week_ago = today - timedelta(days=7)
    recent_assignments = (
        db.query(Assignment)
        .join(TeamMember, Assignment.team_member_id == TeamMember.id)
        .options(joinedload(Assignment.team_member), joinedload(Assignment.engagement_instance))
        .filter(in_firm_member, Assignment.created_at >= week_ago)
        .order_by(Assignment.created_at.desc())
        .limit(5)
        .all()
    )

    # ══════════════════════════════════════════════
    # SECTION 1: Utilization
    # ══════════════════════════════════════════════
    active_members = db.query(TeamMember).filter(in_firm_member, TeamMember.is_active == True).all()
    member_ids = [m.id for m in active_members]

    # Current allocations per member (active assignments)
    member_allocations = {}
    if member_ids:
        alloc_rows = (
            db.query(
                Assignment.team_member_id,
                func.sum(Assignment.allocation_percent).label("total_alloc"),
            )
            .filter(
                Assignment.team_member_id.in_(member_ids),
                Assignment.start_date <= today,
                Assignment.end_date >= today,
            )
            .group_by(Assignment.team_member_id)
            .all()
        )
        for row in alloc_rows:
            member_allocations[row.team_member_id] = int(row.total_alloc or 0)

    # Build utilization data
    utilization_data = []
    for m in active_members:
        alloc = member_allocations.get(m.id, 0)
        util_pct = min(alloc, 100)
        if alloc == 0:
            status = "bench"
        elif alloc <= 50:
            status = "under"
        elif alloc <= 80:
            status = "optimal"
        else:
            status = "over"
        utilization_data.append({
            "name": m.name,
            "id": m.id,
            "allocation": alloc,
            "utilization": util_pct,
            "status": status,
        })

    total_capacity = total_members * 100 if total_members else 1
    total_allocated = sum(member_allocations.get(mid, 0) for mid in member_ids)
    firm_utilization = round((total_allocated / total_capacity) * 100) if total_capacity else 0
    bench_rate = round((bench_count / total_members) * 100) if total_members else 0
    over_allocated_count = sum(1 for u in utilization_data if u["status"] == "over")
    optimal_count = sum(1 for u in utilization_data if u["status"] == "optimal")
    under_count = sum(1 for u in utilization_data if u["status"] == "under")
    bench_members = [u for u in utilization_data if u["status"] == "bench"]

    # ══════════════════════════════════════════════
    # SECTION 3: Timeline
    # ══════════════════════════════════════════════
    three_months_later = today + timedelta(days=90)
    timeline_query = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.team_member),
            joinedload(Assignment.engagement_instance).joinedload(EngagementInstance.engagement),
        )
        .join(TeamMember, Assignment.team_member_id == TeamMember.id)
        .filter(in_firm_member, Assignment.end_date >= today, Assignment.start_date <= three_months_later)
    )
    if business_role:
        timeline_query = timeline_query.filter(TeamMember.business_role == business_role)
    timeline_assignments = timeline_query.order_by(Assignment.start_date).all()
    timeline_data = []
    for a in timeline_assignments:
        eng_name = ""
        inst_label = ""
        client_name = ""
        if a.engagement_instance:
            inst_label = a.engagement_instance.period_label or ""
            if a.engagement_instance.engagement:
                eng_name = a.engagement_instance.engagement.name or ""
                if a.engagement_instance.engagement.client:
                    client_name = a.engagement_instance.engagement.client.name or ""
        timeline_data.append({
            "id": a.id,
            "member_name": a.team_member.name if a.team_member else "?",
            "member_id": a.team_member_id,
            "role": a.team_member.business_role.value if a.team_member and a.team_member.business_role else "",
            "engagement": eng_name,
            "client": client_name,
            "instance": inst_label,
            "alloc": a.allocation_percent,
            "start": a.start_date.isoformat() if a.start_date else "",
            "end": a.end_date.isoformat() if a.end_date else "",
        })

    # ══════════════════════════════════════════════
    # SECTION 4: Analytics
    # ══════════════════════════════════════════════
    # Workload distribution buckets
    workload_buckets = {"bench": 0, "light": 0, "medium": 0, "heavy": 0, "over": 0}
    for u in utilization_data:
        if u["status"] == "bench":
            workload_buckets["bench"] += 1
        elif u["allocation"] <= 40:
            workload_buckets["light"] += 1
        elif u["allocation"] <= 80:
            workload_buckets["medium"] += 1
        elif u["allocation"] <= 100:
            workload_buckets["heavy"] += 1
        else:
            workload_buckets["over"] += 1

    # Engagement pipeline
    eng_by_status = {}
    for status_val in ["active", "on_hold", "completed"]:
        cnt = (
            db.query(func.count(Engagement.id)).join(Client, Engagement.client_id == Client.id)
            .filter(Client.firm_id == firm_id, Engagement.status == status_val).scalar() or 0
        )
        eng_by_status[status_val] = cnt

    # Pending approvals (admin only)
    pending_approvals = []
    pending_approval_count = 0
    if request.session.get("user_role") in ("admin", "super_admin"):
        pending_approvals = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.firm_id == firm_id, ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.created_at.desc())
            .limit(5)
            .all()
        )
        pending_approval_count = len(pending_approvals)
        req_user_ids = set(r.requested_by_user_id for r in pending_approvals)
        if req_user_ids:
            from app.models.models import User as UserModel
            users_map = {u.id: u for u in db.query(UserModel).filter(UserModel.id.in_(req_user_ids)).all()}
            for req in pending_approvals:
                req.requestor_name = users_map[req.requested_by_user_id].display_name if req.requested_by_user_id in users_map else "Unknown"

    # User's own recent approvals/rejections (for notifications)
    user_id = request.session.get("user_id")
    my_recent_requests = []
    if user_id:
        my_recent_requests = (
            db.query(ApprovalRequest)
            .filter(
                ApprovalRequest.firm_id == firm_id,
                ApprovalRequest.requested_by_user_id == user_id,
                ApprovalRequest.status.in_(["approved", "rejected"]),
                ApprovalRequest.updated_at >= today - timedelta(days=7),
            )
            .order_by(ApprovalRequest.updated_at.desc())
            .limit(5)
            .all()
        )

    now_str = today.strftime("%d %b %Y, %I:%M %p")

    # License status
    from app.models.models import Firm
    from app.services.license_service import check_license, get_days_remaining
    firm = db.query(Firm).filter(Firm.id == firm_id).first() if firm_id else None
    license_status = check_license(firm) if firm else "no_license"
    license_days_remaining = get_days_remaining(firm) if firm else None

    # Check if this is a new firm (created within last 7 days)
    show_onboarding = False
    if firm:
        from datetime import datetime, timezone
        if firm.created_at and firm.created_at > datetime.now(timezone.utc) - timedelta(days=7):
            # Check if firm has no team members yet
            if total_members == 0:
                show_onboarding = True

    resp = templates.TemplateResponse(request, "dashboard/home.html", {
        # License info
        "license_status": license_status,
        "license_days_remaining": license_days_remaining,
        "show_onboarding": show_onboarding,
        # Overview (Section 2)
        "total_members": total_members,
        "now": now_str,
        "total_admins": total_admins,
        "active_engagements": active_engagements,
        "total_clients": total_clients,
        "current_assignments": current_assignments,
        "bench_count": bench_count,
        "pending_leaves": pending_leaves,
        "upcoming_leaves": upcoming_leaves,
        "upcoming_leave_count": upcoming_leaves,
        "recent_assignments": recent_assignments,
        "pending_approvals": pending_approvals,
        "pending_approval_count": pending_approval_count,
        # Utilization (Section 1)
        "utilization_data": utilization_data,
        "firm_utilization": firm_utilization,
        "bench_rate": bench_rate,
        "over_allocated_count": over_allocated_count,
        "optimal_count": optimal_count,
        "under_count": under_count,
        "bench_members": bench_members,
        # Timeline (Section 3)
        "timeline_data": timeline_data,
        "today": today.isoformat(),
        "selected_role": business_role or "",
        "business_roles": ["partner", "director", "ca_manager", "paid_assistant", "staff", "article", "data_analyst"],
        # Analytics (Section 4)
        "workload_buckets": workload_buckets,
        "eng_by_status": eng_by_status,
        "my_recent_requests": my_recent_requests,
        "csrf_token": get_csrf_token(request),
    })
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@router.get("/bench", response_class=HTMLResponse)
def bench_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    today = date.today()
    report = report_service.build_bench_report(db, request.session.get("firm_id"))
    items = [
        {
            "name": row["name"],
            "business_role": row["business_role"],
            "days_since_last_assignment": row["days_on_bench"],
            "last_assignment_end": (
                (today - timedelta(days=row["days_on_bench"])).isoformat()
                if row["days_on_bench"] is not None else None
            ),
            "is_rolling_off": False,
        }
        for row in report["rows"]
    ]
    return templates.TemplateResponse(request, "dashboard/bench.html", {
        "items": items,
    })

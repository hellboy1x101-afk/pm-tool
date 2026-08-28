# splanly — Complete Project Context for AI Agents

## Project Overview
**splanly** is a production ERP-style web app for staffing/resource planning, scaled for large teams/clients in CA firms.

**Primary Operating Model:**
- Server-rendered app (Jinja2 + Tailwind/Franken UI) with JSON API v1 for Next.js frontend
- Multi-tenant: Firm-scoped data with branch support
- Managers/moderators assign people to engagement work
- Visibility for partners/directors
- Configurable approval gates for all resource operations
- Licensing system with Standard/Enterprise tiers

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | FastAPI | Python 3.11+ |
| Templates | Jinja2 + Tailwind/Franken UI | Server-side rendering only |
| Database | PostgreSQL (Supabase) | Sync SQLAlchemy; `DATABASE_URL` is supplied by Render |
| ORM | SQLAlchemy sync | Not async |
| Migrations | Alembic | All schema changes via migrations |
| Deployment | Render | Build/start commands in render.yaml |
| Auth | DIY (Password + MS365 OAuth + 2FA + OTP) | bcrypt + TOTP + OAuth OIDC + JWT |
| CSRF | Library-based | Shared macro in templates |
| Email | SMTP (ZeptoMail) | OTP delivery and async outbox worker |
| Frontend API | JWT-based API v1 | For Next.js frontend |

---

## Non-Negotiable Architecture Rules

1. **Business logic in `app/services/` only** — Routers remain thin: validate → call service → return response
2. **All schema changes via Alembic migrations** — Never use `create_all()` in app runtime
3. **All mutating endpoints require auth + role check** with `require_role()`
4. **Assignment creation must be concurrency-safe:**
   - Lock TeamMember row with `.with_for_update()`
   - Sum overlapping allocations in requested date window
   - Block if total > 100
   - Block if approved leave overlaps
   - Do all checks + insert in one transaction
5. **Soft delete** for TeamMember, Client, Engagement, User (not hard delete)
6. **Pagination + search/filter** required for list endpoints > ~50 rows
7. **CSV exports must stream** (generator + yield_per + joinedload)
8. **Bulk CSV upload** must validate row-by-row and return structured per-row results
9. **/health route** must not touch DB/session and must be excluded from auth
10. **SQLAlchemy engine** must use `pool_pre_ping=True` and `pool_recycle=280`
11. **All mutating forms** must include CSRF token via shared macro
12. **Add `created_at` and `updated_at`** to all domain tables
13. **No client-side SPA frameworks** (SSR only, Next.js frontend uses API v1)
14. **No hardcoded secrets**
15. **Prefer ORM over raw SQL** where possible
16. **All data is firm-scoped** — every domain table has `firm_id` FK
17. **Approval checks** via `app/approval_check.py` before mutations (configurable per firm)

---

## Domain Model

### Enums

| Enum | Values |
|------|--------|
| `TechnicalRole` | `super_admin`, `admin`, `moderator`, `viewer` |
| `BusinessRole` | `partner`, `director`, `ca_manager`, `paid_assistant`, `staff`, `article`, `data_analyst` |
| `EngagementType` | `statutory_audit`, `internal_audit`, `tax_audit`, `consulting`, `special_assignment`, `other` |
| `RecurrencePattern` | `one_off`, `weekly`, `fortnightly`, `monthly`, `quarterly`, `annual` |
| `EngagementStatus` | `active`, `on_hold`, `completed`, `lost_client` |
| `InstanceStatus` | `planned`, `in_progress`, `completed`, `delayed` |
| `LeaveType` | `sick`, `vacation`, `exam_leave`, `other` |
| `LeaveStatus` | `pending`, `approved`, `rejected` |
| `ResourceType` | `assignment`, `engagement`, `client`, `team_member`, `leave` |
| `OperationType` | `create`, `update`, `delete` |
| `ApprovalStatus` | `pending`, `approved`, `rejected` |
| `ExtensionStatus` | `pending`, `approved`, `rejected` |

### Tables (19 total)

All tables are in the PostgreSQL `public` schema. RLS is enabled by the
`f2b3c4d5e6f7` migration; no public policies are defined, so PostgREST API
roles are denied by default. The server connects with a privileged database
role for its SQLAlchemy queries.

#### Firm (`firms`)
- `id` (Integer, PK)
- `name` (String 255, not null)
- `code` (String 50, unique, not null)
- `logo_url` (String 500, nullable)
- `allowed_domains` (Text, nullable) — comma-separated email domains
- `is_active` (Boolean, default True)
- `license_key` (String 255, nullable)
- `license_key_hash` (String 255, indexed, nullable)
- `license_tier` (String 50, nullable) — "standard" or "enterprise"
- `license_expires_at` (DateTime, nullable)
- `license_activated_at` (DateTime, nullable)
- `created_at`, `updated_at`
- Relationships: `branches`, `firm_users`, `approval_rules`

#### Branch (`branches`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id)
- `name` (String 255, not null)
- `code` (String 50, nullable)
- `city` (String 255, nullable)
- `address` (Text, nullable)
- `is_active` (Boolean, default True)
- `created_at`, `updated_at`
- Relationships: `firm`, `team_members`

#### FirmUser (`firm_users`)
- `id` (Integer, PK)
- `user_id` (Integer, FK → users.id, RESTRICT)
- `firm_id` (Integer, FK → firms.id, RESTRICT)
- `technical_role` (Enum: TechnicalRole)
- `is_active` (Boolean, default True)
- `created_at`, `updated_at`
- Constraints: `UniqueConstraint("user_id", "firm_id")`, Indexes on `firm_id`, `user_id`
- Relationships: `user`, `firm`

#### User (`users`)
- `id` (Integer, PK)
- `email` (String 255, unique, not null)
- `display_name` (String 255, not null)
- `password_hash` (String 255, nullable)
- `totp_secret` (String 64, nullable)
- `totp_enabled` (Boolean, default False)
- `is_active` (Boolean, default True)
- `azure_oid` (String 255, unique, nullable)
- `created_at`, `updated_at`, `deleted_at`, `deleted_by_user_id`
- Relationships: `firm_users`

#### TeamMember (`team_members`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id)
- `branch_id` (Integer, FK → branches.id, SET NULL)
- `employee_code` (String 50, unique, nullable)
- `name` (String 255, not null)
- `email` (String 255, unique, not null)
- `business_role` (Enum: BusinessRole)
- `is_oversight_only` (Boolean, default False)
- `seniority_level` (String 100, nullable)
- `date_of_joining` (Date, nullable)
- `date_of_relieving` (Date, nullable)
- `is_active` (Boolean, default True)
- `created_at`, `updated_at`
- Relationships: `firm`, `branch`, `assignments`, `leaves`

#### Client (`clients`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id)
- `name` (String 255, not null)
- `code` (String 50, unique, nullable)
- `industry` (String 255, nullable)
- `is_active` (Boolean, default True)
- `created_at`, `updated_at`
- Relationships: `firm`, `engagements`

#### Engagement (`engagements`)
- `id` (Integer, PK)
- `client_id` (Integer, FK → clients.id, RESTRICT)
- `name` (String 255, not null)
- `engagement_type` (Enum: EngagementType)
- `recurrence_pattern` (Enum: RecurrencePattern)
- `default_team_template_json` (JSON, nullable)
- `start_date` (Date, not null)
- `end_date` (Date, nullable)
- `status` (Enum: EngagementStatus)
- `is_active` (Boolean, default True)
- `created_at`, `updated_at`
- Relationships: `client`, `instances`

#### EngagementInstance (`engagement_instances`)
- `id` (Integer, PK)
- `engagement_id` (Integer, FK → engagements.id, RESTRICT)
- `period_label` (String 255, not null)
- `start_date` (Date, not null)
- `end_date` (Date, not null)
- `due_date` (Date, nullable)
- `status` (Enum: InstanceStatus)
- `created_at`, `updated_at`
- Check: `end_date >= start_date`
- Relationships: `engagement`, `assignments`

#### Assignment (`assignments`)
- `id` (Integer, PK)
- `team_member_id` (Integer, FK → team_members.id, RESTRICT)
- `engagement_instance_id` (Integer, FK → engagement_instances.id, RESTRICT)
- `role_on_engagement` (String 255, nullable)
- `allocation_percent` (Integer, 0-100, not null)
- `start_date` (Date, not null)
- `end_date` (Date, not null)
- `created_by_user_id` (Integer, FK → users.id, SET NULL)
- `created_at`, `updated_at`
- Indexes: `(team_member_id, start_date, end_date)`, `(engagement_instance_id)`, `(team_member_id, engagement_instance_id)`
- Check: `allocation_percent >= 0 AND <= 100`, `end_date >= start_date`

#### Leave (`leaves`)
- `id` (Integer, PK)
- `team_member_id` (Integer, FK → team_members.id, RESTRICT)
- `leave_type` (Enum: LeaveType)
- `start_date` (Date, not null)
- `end_date` (Date, not null)
- `status` (Enum: LeaveStatus)
- `reason` (Text, nullable)
- `created_at`, `updated_at`
- Index: `(team_member_id, start_date, end_date)`
- Check: `end_date >= start_date`

#### ApprovalRule (`approval_rules`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id)
- `resource_type` (Enum: ResourceType)
- `operation` (Enum: OperationType)
- `is_enabled` (Boolean)
- `approver_role` (Enum: TechnicalRole)
- `created_at`, `updated_at`
- Constraints: `UniqueConstraint("firm_id", "resource_type", "operation")`

#### ApprovalRequest (`approval_requests`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id)
- `resource_type` (Enum: ResourceType)
- `resource_id` (Integer, nullable)
- `operation` (Enum: OperationType)
- `requested_by_user_id` (Integer, FK → users.id)
- `payload` (JSON)
- `status` (Enum: ApprovalStatus)
- `reviewed_by_user_id` (Integer, FK → users.id, SET NULL)
- `review_note` (Text, nullable)
- `created_at`, `updated_at`
- Index: `(firm_id, status)`

#### ExtensionRequest (`extension_requests`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id)
- `team_member_id` (Integer, FK → team_members.id)
- `engagement_instance_id` (Integer, FK → engagement_instances.id)
- `allocation_percent` (Integer, 0-100)
- `start_date`, `end_date`, `role_on_engagement`, `reason`
- `status` (Enum: ExtensionStatus)
- `requested_by_user_id`, `reviewed_by_user_id`, `review_note`
- `created_at`, `updated_at`
- Check: `allocation_percent >= 0 AND <= 100`, `end_date >= start_date`

#### SystemSetting (`system_settings`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id, SET NULL)
- `key` (String 255, unique, not null)
- `value` (Text, not null)
- `description` (Text, nullable)
- `updated_by_user_id` (Integer, FK → users.id, SET NULL)
- `created_at`, `updated_at`

#### EmailOutbox (`email_outbox`)
- `id` (Integer, PK)
- `recipient_email`, `subject`, `body`, `status`, `retry_count`
- `next_attempt_at`, `last_error`
- `assignment_id` (FK → assignments.id, SET NULL)
- `created_at`, `updated_at`, `sent_at`

#### Invitation (`invitations`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id, CASCADE)
- `email`, `role`, `invited_by_user_id` (FK → users.id)
- `token` (unique), `is_used`, `expires_at`, `created_at`

#### SuperAdmin (`super_admins`)
- `id` (Integer, PK)
- `email` (unique), `display_name`, `password_hash`, `is_active`
- `created_at`, `updated_at`

#### FirmBusinessRole (`firm_business_roles`)
- `id` (Integer, PK)
- `firm_id` (Integer, FK → firms.id, CASCADE)
- `role_code`, `is_enabled`
- `rate_type` ("hourly" or "daily"), `rate_value` (Numeric), `currency` (default "INR")
- `created_at`, `updated_at`
- Constraints: `UniqueConstraint("firm_id", "role_code")`

---

## Auth & RBAC

### Technical Roles (RBAC) — 4-tier
- `super_admin`: platform-level admin (SuperAdmin model, separate from User)
- `admin`: full firm admin operations
- `moderator`: staffing operations (assignment create/update etc.)
- `viewer`: read-only

### Permission Matrix
| Operation | super_admin | admin | moderator | viewer |
|-----------|-------------|-------|-----------|--------|
| All mutations | ✅ | ✅ | ✅ (staffing) | ❌ |
| View-only | ✅ | ✅ | ✅ | ✅ |
| Bulk operations | ✅ | ✅ | ❌ | ❌ |
| Settings management | ✅ | ✅ | ❌ | ❌ |
| License management | ✅ | ❌ | ❌ | ❌ |

### Auth Implementation (DIY - Fully Implemented)
- **Password auth**: bcrypt hashed passwords
- **OTP verification**: 6-digit email OTP with rate limiting for registration and login
- **2FA**: TOTP with QR code generation (optional per firm)
- **MS365 OAuth**: OIDC integration with auto-user creation
- **JWT**: HS256 access (24h) + refresh (30d) tokens for API v1
- **Session-based**: cookies with configurable max age (SSR routes)
- **Multi-firm**: user can belong to multiple firms, switch active firm
- `get_current_user` dependency (3-layer: Session → JWT → X-User-Id header)
- `require_role(*roles)` dependency factory checking `FirmUser.technical_role`
- All mutating routes gated
- `/health` excluded from auth middleware
- CSRF: library-based, shared macro `csrf_field()` in `templates/macros/_csrf.html`

### Business Roles (Planning Semantics - NOT RBAC)
- `partner`, `director`, `ca_manager`, `paid_assistant`, `staff`, `article`, `data_analyst`
- Used for planning/semantic purposes, not access control
- Per-firm configurable cost rates (hourly/daily) via `FirmBusinessRole`

---

## Services Layer (18 modules)

| Service | Key Functions |
|---------|---------------|
| `auth_service.py` | hash/verify password, TOTP generate/verify/enable/disable, MS365 OAuth, user auth |
| `firm_service.py` | CRUD firms/branches/firm_users, multi-firm user management |
| `otp_service.py` | generate/verify OTP, rate limiting, domain-based firm lookup |
| `invitation_service.py` | create/send/accept/revoke invitations, bulk invite, role promotion, super admin transfer |
| `approval_service.py` | CRUD approval rules, check/create/approve/reject approval requests |
| `extension_service.py` | create/list/approve/reject extension requests |
| `team_member_service.py` | CRUD team members, bulk deactivate |
| `client_service.py` | CRUD clients |
| `engagement_service.py` | CRUD engagements + instances |
| `allocation_service.py` | CRUD assignments with over-allocation/leave-conflict checks |
| `leave_service.py` | CRUD leaves |
| `user_service.py` | CRUD users, soft delete/restore, list deleted |
| `settings_service.py` | get/update system settings, auth method toggle |
| `email_service.py` | list outbox, retry failed, process outbox |
| `report_service.py` | 11 reports (role-tiered), CSV streaming |
| `license_service.py` | generate/validate/activate license keys, super admin auth |
| `license_tiers.py` | tier limits (Standard: 50 users/599 members, Enterprise: 100/5000) |

---

## Routers (16 SSR + API v1)

### SSR Routes (Jinja2 templates)

#### Health
- `GET /health` — `{"status":"ok"}`, no DB/session/auth

#### Auth (`/auth`)
- Multi-step registration: domain check → OTP verify → set password
- Login: password → OTP or 2FA verification
- MS365 OAuth: `/auth/ms365` → `/auth/callback`
- Invitation acceptance, password change, firm selection
- Logout

#### Dashboard (`/dashboard`)
- `GET /dashboard` — utilization, timeline, analytics, approvals, license status
- `GET /dashboard/bench` — bench members view

#### Team Members (`/team-members`)
- List (HTML + JSON), create (form + JSON API), detail, edit, deactivate, bulk upload, bulk deactivate, extension request

#### Clients (`/clients`)
- List (HTML + JSON), create (form + JSON API), edit, deactivate

#### Engagements (`/engagements`)
- List (HTML + JSON), create (form + JSON API), detail with instances, edit, deactivate
- Instance CRUD (form + JSON API)

#### Assignments (`/assignments`)
- List (HTML + JSON), create (form + JSON API), edit, assign-staff page

#### Leaves (`/leaves`)
- List (HTML + JSON), create (form + JSON API), edit

#### Reports (`/reports`)
- 11 reports with role-tiered access, CSV download
- Tier 1 (all): team_utilization, bench_report, rolling_off_soon, leave_summary, engagement_status
- Tier 2 (moderator+): allocation_by_engagement, team_capacity_planning, client_portfolio
- Tier 3 (admin only): budget_vs_actual, cost_by_role, firm_cost_summary

#### Admin Settings (`/admin/settings`)
- General settings, approval rules, domains, auth method, business roles + cost rates

#### Users (`/users`)
- List + pending approvals + extensions + approval logs + deleted users
- Create, edit, deactivate, restore

#### Invitations (`/invitations`)
- List, create, bulk invite (CSV/text), revoke, manage roles, promote, transfer super admin

#### Contact (`/contact`)
- Contact form with email delivery

#### Outbox (`/admin/outbox`)
- Email outbox viewer, retry failed, manual process trigger

#### License (`/license`)
- Activate, status, expired pages

### API v1 Routes (JSON, JWT auth, for Next.js frontend)

| Prefix | Endpoints |
|--------|-----------|
| `/api/v1/auth` | login, refresh, me, firm/switch |
| `/api/v1/dashboard` | dashboard stats |
| `/api/v1/team-members` | CRUD + list |
| `/api/v1/clients` | CRUD + list |
| `/api/v1/engagements` | CRUD + list |
| `/api/v1/assignments` | list, create, update |
| `/api/v1/leaves` | list, create, update |
| `/api/v1/approval-requests` | list, approve, reject |
| `/api/v1/extensions` | list, create, approve, reject |

---

## Middleware Stack (outermost → innermost)

1. **LicenseMiddleware** — Blocks access if firm has no valid license (exempts auth/health/static/license/api paths)
2. **FlashMiddleware** — Pops flash messages from session for one-time display
3. **SessionMiddleware** — Starlette session (cookie-based)
4. **CORSMiddleware** — Allows localhost:3000/3001, *.vercel.app

---

## Bulk Upload Behavior

**CSV upload for team members:**
1. Validate content type
2. Enforce max size (10MB default)
3. Enforce max rows from setting (`bulk_upload_max_rows`, default 8000)
4. Parse all rows
5. Validate each row with Pydantic schema
6. Detect duplicate emails inside file
7. Detect duplicates against DB
8. Partial success allowed

---

## Pagination Policy

All list endpoints must accept:
- `limit` (default 50, max 200)
- `offset` (default 0)
- `q` (optional search)
- Relevant filters (role/status/active etc.)

---

## Templates (54 files)

Organized by feature in `app/templates/`:
- `auth/` — register, login, 2fa, invitation, password change, firm select
- `dashboard/` — home, bench
- `team_members/` — list, form, detail
- `clients/` — list, form
- `engagements/` — list, form, detail
- `assignments/` — list, form, assign_staff
- `leaves/` — list, form
- `admin/` — settings, outbox
- `reports/` — index, view
- `invitations/` — list, form, bulk, manage
- `users/` — list, form
- `license/` — activate, status, expired
- `errors/` — 404, 500
- `macros/` — csrf, search, pagination, forms
- `partials/` — alert
- Root: base.html, landing.html, contact.html

---

## Error Handling

Centralized exception handlers in `app/exceptions.py`:
- `NotFoundError` → 404
- `OverAllocationError` → 409
- `ConflictWithLeaveError` → 409
- `ValidationError` → 422
- `HTTPException` → 401 redirects to `/auth/login` for HTML requests

---

## Config (`app/config.py`)

pydantic-settings with:
- `APP_NAME` (default "splanly"), `ENV`
- `TESTING` (bool, default False)
- `DATABASE_URL`
- `SECRET_KEY`
- `ALLOWED_ORIGINS`
- `SESSION_COOKIE_NAME`, `SESSION_MAX_AGE_SECONDS`
- `MS365_TENANT_ID`, `MS365_CLIENT_ID`, `MS365_CLIENT_SECRET`, `MS365_REDIRECT_URI`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_INVITATION_FROM_EMAIL`
- `EMAIL_WORKER_INTERVAL_SECONDS`, `EMAIL_MAX_RETRIES`
- `DEFAULT_BENCH_ROLLOFF_DAYS=7`
- `BULK_UPLOAD_MAX_ROWS=8000`
- `CSV_MAX_FILE_SIZE_MB=10`
- `LICENSE_SIGNING_KEY`

---

## Database Bootstrap

Sync engine in `app/database.py`:
```python
create_engine(
  settings.DATABASE_URL,
  pool_pre_ping=True,
  pool_recycle=280,
  pool_size=5,
  max_overflow=10,
)
```

---

## Alembic Migrations (12 total)

1. `4894207a1570` — Initial (core tables)
2. `fb332ebe2708` — Auth fields (password_hash, totp)
3. `b9f07d65b257` — Multi-tenancy (firms, branches, firm_users, approval_rules/requests)
4. `c249c56b7b7c` — Extension requests
5. `968d26aaac9c` — Allowed domains on firms
6. `a1b2c3d4e5f6` — Licensing (super_admins, firm license metadata)
7. `b2c3d4e5f6a7` — Password expiry policy
8. `d3e4f5a6b7c8` — Super-admin TOTP and license inventory
9. `e4f5a6b7c8d9` — Developer portal users
10. `f1a2b3c4d5e6` — User soft-delete fields
11. `f2b3c4d5e6f7` — Enable RLS on public tables
12. `f3c4d5e6f7a8` — Firm license key compatibility column

---

## Tests

### `tests/conftest.py`
- Safety guard: `clean_tables` only runs if `TESTING=true` or DB URL contains "test"
- Fixtures: db, client, admin/moderator/viewer users, team_member, client, engagement, instance

### Test files exist for:
- Health, allocation service, bulk upload, auth, admin settings, DIY auth, email worker, user management

---

## Deployment

- `render.yaml` — service name: `staffplan`, build: pip install, start: `alembic upgrade head && gunicorn ...`
- `.env.example` — all required keys including LICENSE_SIGNING_KEY, SMTP config
- Database: Supabase PostgreSQL; use the Session Pooler URL on Render
- Email: ZeptoMail SMTP (smtp.zeptomail.in:587, STARTTLS)

`DATABASE_URL` must be a URL-encoded PostgreSQL connection string. Direct
Supabase database hosts may resolve to IPv6, which is unavailable on some
Render services; prefer the Session Pooler URL on port 5432.

---

## Quality Bar

- Type hints on services and schemas
- Clear docstrings for complex logic
- No dead code
- No hardcoded secrets
- Keep routers thin
- Keep business logic in services
- Follow soft-delete and role-gating rules strictly

---

## Workflow Preferences

- Confirm all tests pass before moving to the next task
- Get UI approval before pushing changes, then deploy to production
- Discuss and get approval on UI changes before implementing them

---

## Repository Structure

```
pm-tool/
├── app/
│   ├── __init__.py
│   ├── main.py                    # Entry point, middleware, router includes
│   ├── config.py                  # pydantic-settings
│   ├── database.py                # SQLAlchemy engine + session
│   ├── csrf.py                    # CSRF config
│   ├── csrf_utils.py              # CSRF token helpers
│   ├── exceptions.py              # Domain exceptions
│   ├── flash.py                   # Flash message helpers
│   ├── email_worker.py            # Async email outbox processor
│   ├── templates_setup.py         # Jinja2 templates
│   ├── approval_check.py          # Centralized approval gate
│   ├── seed_data.py               # Demo data seeder
│   ├── auth/
│   │   ├── auth.py                # get_current_user, require_role
│   │   └── jwt.py                 # JWT token create/decode
│   ├── middleware/
│   │   └── license.py             # LicenseMiddleware
│   ├── models/
│   │   ├── models.py              # All core models (19 tables)
│   │   ├── invitation.py          # Invitation model
│   │   ├── super_admin.py         # SuperAdmin model
│   │   └── firm_business_role.py  # FirmBusinessRole model
│   ├── schemas/
│   │   ├── schemas.py             # Pydantic schemas
│   │   └── v1/auth.py             # API v1 auth schemas
│   ├── api/
│   │   └── v1/                    # JSON API endpoints
│   │       ├── auth.py
│   │       ├── dashboard.py
│   │       ├── team_members.py
│   │       ├── clients.py
│   │       ├── engagements.py
│   │       ├── assignments.py
│   │       ├── leaves.py
│   │       ├── approval.py
│   │       └── extensions.py
│   ├── routers/                   # SSR routers (16 files)
│   │   ├── health.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── team_members.py
│   │   ├── clients.py
│   │   ├── engagements.py
│   │   ├── assignments.py
│   │   ├── leaves.py
│   │   ├── reports.py
│   │   ├── admin_settings.py
│   │   ├── users.py
│   │   ├── invitations.py
│   │   ├── contact.py
│   │   ├── outbox.py
│   │   └── license.py
│   ├── services/                  # Business logic (18 files)
│   │   ├── allocation_service.py
│   │   ├── approval_service.py
│   │   ├── auth_service.py
│   │   ├── client_service.py
│   │   ├── email_service.py
│   │   ├── engagement_service.py
│   │   ├── extension_service.py
│   │   ├── firm_service.py
│   │   ├── invitation_service.py
│   │   ├── leave_service.py
│   │   ├── license_service.py
│   │   ├── license_tiers.py
│   │   ├── otp_service.py
│   │   ├── report_service.py
│   │   ├── settings_service.py
│   │   ├── team_member_service.py
│   │   └── user_service.py
│   ├── templates/                 # 54 Jinja2 templates
│   │   ├── base.html
│   │   ├── landing.html
│   │   ├── auth/
│   │   ├── dashboard/
│   │   ├── team_members/
│   │   ├── clients/
│   │   ├── engagements/
│   │   ├── assignments/
│   │   ├── leaves/
│   │   ├── admin/
│   │   ├── reports/
│   │   ├── invitations/
│   │   ├── users/
│   │   ├── license/
│   │   ├── errors/
│   │   ├── macros/
│   │   └── partials/
│   └── static/
├── alembic/                       # 6 migrations
├── tests/                         # Test suite
├── requirements.txt
├── .env.example
├── render.yaml
├── CONTEXT.md
├── DEPLOYMENT_GUIDE.md
├── SESSION_SUMMARY.md
├── HEALTH_ROUTE_TODO.md
└── frontend_new.md
```

---

**Last Updated:** 2026-07-27
**Project Status:** Production-ready, feature-complete
**App Name:** splanly
**Database:** Neon PostgreSQL (Render)
**Deployment:** Render (https://staffplan.onrender.com)
**Owner:** PKF Sridhar & Santhanam LLP

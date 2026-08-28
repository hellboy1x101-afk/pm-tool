# Risk Register and Engineering Practices

**As of:** 2026-08-28  
**Scope:** Python application, database/migrations, authentication, business operations, deployment, and tests  
**Review status:** Recorded for prioritization; no fixes are implied by this document.

## Progress Tracker

| ID | Priority | Risk | Status | Owner / Next Action | Validation |
|---|---|---|---|---|---|
| RISK-001 | P0 | Active-firm authorization | In progress | SSR/API active-firm membership enforcement added | Compile/import validation passed; security tests pending |
| RISK-002 | P0 | Unscoped mutation services | In progress | Client, engagement, assignment, and extension paths now receive firm context | Cross-firm client mutation check passed; full isolation tests pending |
| RISK-003 | P0 | Non-atomic approval processing | In progress | Approval lookup is firm-scoped and status commit is deferred until application | Compile/import validation passed; full transaction test pending |
| RISK-004 | P0 | Missing assignment-edit CSRF | In progress | CSRF validation added to assignment edit form | Compile validation passed; request test pending |
| RISK-005 | P0 | Missing firm ownership on creates | In progress | Client, engagement, instance, and assignment creates derive active firm | Compile/import validation passed; endpoint tests pending |
| RISK-006 | P1 | Broken assignment JSON endpoint | In progress | Active firm is now passed to the assignment listing service | Compile/import validation passed; endpoint test pending |
| RISK-007 | P1 | Orphaned user creation | In progress | User and firm membership are now flushed and committed together | Compile/import validation passed; rollback test pending |
| RISK-008 | P1 | Process-local OTP state | In progress | OTP challenges are now durable, hashed, expiring, rate-limited, and one-time | SQLite round-trip passed; PostgreSQL migration deployment pending |
| RISK-009 | P1 | Hidden SMTP failures | In progress | Resend routes now receive DB sessions; SMTP recipient refusals are treated as failures | Compile validation pending; provider delivery telemetry still pending |
| RISK-010 | P1 | Unreliable test regression gate | Not started | Assign owner; repair PostgreSQL test setup and fixtures | Not started |
| RISK-011 | P1 | ORM/migration drift | Not started | Assign owner; add model-to-schema CI validation | Not started |
| RISK-012 | P2 | RLS policy model undefined | Not started | Decide whether direct PostgREST access is required | Not started |

## Executive Summary

The highest-risk area is tenant isolation. The application has firm-scoped data in its model and list queries, but several authorization and mutation paths still trust global IDs or the first firm membership. This can allow a valid user to read or mutate another firm's records.

The second major area is operational correctness: approval commits can become inconsistent with the requested change, user creation can leave orphaned accounts, and OTP state is process-local while production uses multiple workers.

The test suite currently cannot provide a reliable safety net. It reports schema/setup drift and many errors before exercising the relevant business behavior.

## Risk Register

### RISK-001: Active-firm authorization is not enforced

- **Severity:** Critical
- **Area:** Authentication, authorization, tenant isolation
- **Evidence:** `app/auth/auth.py` checks the first active `FirmUser` instead of the session/JWT firm. `app/api/v1/deps.py` falls back to the first firm when firm context is missing or invalid.
- **Impact:** A user who belongs to multiple firms may receive a role from one firm while operating in another firm. This can cause cross-tenant access or unauthorized mutations.
- **Recommended practice:** Resolve the active firm once per request, require an active membership in that exact firm, and reject missing, invalid, or mismatched firm context. Never silently fall back for a mutating request.
- **Suggested priority:** P0

### RISK-002: Mutation services accept unscoped record IDs

- **Severity:** Critical
- **Area:** Business logic, tenant isolation
- **Evidence:** Client, engagement, and assignment update/delete services load records by primary key without requiring `firm_id`. Assignment creation does not verify that the team member and engagement instance belong to the caller's firm or to the same firm.
- **Impact:** A caller who obtains another firm's record ID may update, deactivate, or create relationships involving records outside the active tenant.
- **Recommended practice:** Every firm-owned service method must accept `firm_id` and include it in the query and relationship checks. Enforce ownership in services, not only in routers. Add negative cross-firm tests for every CRUD operation.
- **Suggested priority:** P0

### RISK-003: Approval processing can report success without applying the change

- **Severity:** High
- **Area:** Transactions, approval workflow
- **Evidence:** API approval first commits the request as approved, then applies the requested mutation. The mutation can fail afterward. Approval lookup also lacks firm scoping.
- **Impact:** The approval record can say `approved` while the business change was never applied. A request from another firm may also be approved if its ID is known.
- **Recommended practice:** Load the approval request with `firm_id`, revalidate the current resource and business invariants, apply the mutation, and commit the status and mutation in one transaction. Roll back both on failure. Use idempotency or row locking for concurrent review actions.
- **Suggested priority:** P0

### RISK-004: Assignment edit form lacks CSRF validation

- **Severity:** High
- **Area:** Web security
- **Evidence:** `app/routers/assignments.py` processes `POST /assignments/{assignment_id}/edit` without calling `validate_csrf()`.
- **Impact:** A logged-in browser user can be induced to submit an unauthorized assignment change from another site.
- **Recommended practice:** Require the shared CSRF token on every state-changing browser form. Add a test that rejects missing and invalid tokens for every mutating form route.
- **Suggested priority:** P0

### RISK-005: Firm-scoped create paths omit ownership fields

- **Severity:** High
- **Area:** CRUD flow, data integrity
- **Evidence:** Client and engagement create routes call services without adding `firm_id`, although the database columns are required.
- **Impact:** Requests can fail with a database `NOT NULL` error or create data without a valid tenant association if the schema later becomes permissive.
- **Recommended practice:** Derive `firm_id` from the authenticated active-firm context and pass it explicitly to the service. Do not accept tenant ownership from untrusted request bodies.
- **Suggested priority:** P0

### RISK-006: SSR assignment JSON endpoint has a missing required argument

- **Severity:** High
- **Area:** Functional correctness
- **Evidence:** `app/routers/assignments.py` calls `list_assignments()` without the required `firm_id` argument.
- **Impact:** The endpoint raises `TypeError` instead of returning data.
- **Recommended practice:** Add route-level smoke tests for every registered endpoint and use typed service signatures so missing required context is caught early.
- **Suggested priority:** P1

### RISK-007: User creation can leave an orphaned account

- **Severity:** High
- **Area:** Transactions, onboarding
- **Evidence:** `user_service.create_user()` commits the user before the router adds the user to the firm.
- **Impact:** If firm membership creation fails, the user remains without a firm. A retry then fails on duplicate email.
- **Recommended practice:** Create the user and firm membership in one service-level transaction. Use `flush()` to obtain the user ID, then commit once after all related records succeed.
- **Suggested priority:** P1

### RISK-008: OTP state is local to one worker process

- **Severity:** High
- **Area:** Signup/login reliability
- **Evidence:** `app/services/otp_service.py` stores OTPs and rate-limit data in module-level dictionaries. Render is configured with two Gunicorn workers.
- **Impact:** The OTP may be generated by one worker and verified by another, or disappear after restart. Valid signup codes can fail intermittently.
- **Recommended practice:** Store OTP challenges and rate limits in a shared database or Redis with expiry, attempt limits, and atomic consumption. Keep OTP values out of normal logs in production.
- **Suggested priority:** P1

### RISK-009: SMTP failures are hidden from signup users

- **Severity:** Medium
- **Area:** Signup operations, observability
- **Evidence:** OTP email exceptions are caught and logged, while the route still displays the verification step as though delivery succeeded.
- **Impact:** Users wait for an email that was never sent and have no actionable error. Support cannot easily distinguish delivery failure from an invalid code.
- **Recommended practice:** Record delivery status, expose a generic retryable user message, instrument structured failure metrics, and provide a controlled resend path. Never reveal OTP values in production logs.
- **Suggested priority:** P1

### RISK-010: Test suite is not currently a reliable regression gate

- **Severity:** High
- **Area:** Quality and release safety
- **Evidence:** The review run produced `36 failed, 15 passed, 123 errors`. Failures include uninitialized SQLite tables and fixtures passing the removed `TeamMember.technical_role` field.
- **Impact:** New security or business regressions can pass unnoticed, while failures obscure whether application behavior is correct.
- **Recommended practice:** Use migration-based test database setup, preferably PostgreSQL for PostgreSQL-specific behavior. Update fixtures to current models, isolate tests transactionally, and make CI fail clearly on setup errors.
- **Suggested priority:** P1

### RISK-011: ORM and migration drift has caused production failures

- **Severity:** High
- **Area:** Database lifecycle
- **Evidence:** Production previously lacked `users.deleted_at`, `users.deleted_by_user_id`, and `firms.license_key` even though the ORM selected them. The deployment also required a migration to enable RLS.
- **Impact:** Basic signup and firm-domain lookup returned HTTP 500 errors.
- **Recommended practice:** Treat migrations as the source of truth, review model-to-schema diffs in CI, run `alembic upgrade head` against a clean database before release, and add a startup/schema health check that does not expose secrets.
- **Suggested priority:** P1

### RISK-012: RLS is enabled without an explicit application policy model

- **Severity:** Medium
- **Area:** Supabase security
- **Evidence:** RLS was enabled on public tables to clear the linter, but the application currently relies on a privileged server database role and defines no PostgREST policies.
- **Impact:** Direct server access is protected by application authorization, while API-role behavior is deny-by-default. Future direct Supabase API usage may fail unexpectedly or lead to rushed permissive policies.
- **Recommended practice:** Keep server-side database credentials private and privileged. If PostgREST access is introduced, define least-privilege policies using trusted claims and firm ownership predicates, enable RLS on every exposed table, and never authorize from editable user metadata.
- **Suggested priority:** P2

## Best-Practice Baseline

### Tenant Isolation

- Require `firm_id` in every service operation involving firm-owned data.
- Derive tenant context from authenticated identity, never from client-supplied ownership fields.
- Validate that all related records belong to the same firm before writing relationships.
- Add cross-firm denial tests for reads, creates, updates, deletes, approvals, and exports.

### Authorization

- Separate authentication, active-firm resolution, membership validation, and role checking.
- Reject invalid firm context instead of falling back to the first membership.
- Apply authorization at the service boundary as a defense in depth measure.
- Do not use development-only headers such as `X-User-Id` in production.

### Transactions

- Keep related writes in one service-level transaction.
- Use `flush()` for IDs and commit once after all invariants pass.
- Roll back on all exceptions before reusing a session.
- For approvals, apply and mark approved atomically, with current-state revalidation.
- Use row locks or database constraints for concurrent allocation decisions.

### Database and Migrations

- Every ORM column must have a corresponding migration.
- Avoid editing already-applied migrations to repair production; add a forward migration.
- Use idempotent compatibility SQL only when it is intentional and documented.
- Run migrations against a clean PostgreSQL database and a representative existing database in CI.
- Keep `DATABASE_URL` as a URL-encoded secret and use TLS-required Supabase Session Pooler connections on Render where direct IPv6 access is unavailable.

### Authentication and OTP

- Store OTP challenges in shared durable storage with expiry and atomic one-time consumption.
- Hash or encrypt OTP material where appropriate and never log raw OTPs in production.
- Rate-limit by account and source, with bounded attempts and audit events.
- Make email delivery state observable without exposing sensitive details.

### RLS and Supabase

- Enable RLS on every table exposed through PostgREST.
- Default to deny and add only policies required by the API.
- Combine `TO authenticated` with an ownership predicate; authentication alone is not authorization.
- Do not use `user_metadata` for authorization decisions.
- Never expose a service-role or database password to a browser client.

### Testing and Release

- Run tests against a PostgreSQL-compatible schema created through Alembic.
- Add endpoint smoke tests for status codes and basic response contracts.
- Add security tests for CSRF, tenant isolation, role boundaries, and authorization context.
- Add failure-injection tests for approval application, firm membership creation, email delivery, and retries.
- Make migration and test setup failures distinct from behavior failures in CI.

## Suggested Fix Order

1. **P0:** Enforce active-firm membership and service-level tenant scoping.
2. **P0:** Make approval application atomic and firm-scoped.
3. **P0:** Add CSRF validation to every mutating browser route.
4. **P1:** Fix missing firm ownership on creates and the broken assignment JSON route.
5. **P1:** Make signup/login OTP state shared and durable.
6. **P1:** Repair the PostgreSQL test harness and update stale fixtures.
7. **P1:** Add model-to-migration drift checks and endpoint/security regression tests.
8. **P2:** Define explicit PostgREST policies if browser/API clients will access Supabase directly.

## Review Notes

This register records risks identified during the 2026-08-28 review. The suggested priorities are recommendations only; product impact, deployment urgency, and available rollback options should determine the final order.

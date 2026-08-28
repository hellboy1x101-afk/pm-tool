# splanly Development Session Summary

## Latest Update: July 27, 2026

### UI & Landing Page
- Login & register pages redesigned with dark theme (grid stencil, capacity card carousel)
- Light/dark theme toggle working on all auth pages
- Brand panel text hardcoded for contrast in both themes
- Landing page: mobile hamburger nav, scroll-to-top, testimonials carousel
- Standalone pages (FAQ, docs, legal) all have mobile hamburger nav
- Zoho Calendar booking: all "Book Demo" links redirect to Zoho URL directly
- Steps section: dotted lines connecting 1-2-3, no sluggish animations
- Removed heavy animations from landing page for performance

### Best Practices (FastAPI audit)
- Swagger/ReDoc fully disabled (docs-app page serves as public docs)
- Shared ORMModel base class (eliminates from_attributes duplication)
- CSRF require_csrf() dependency created
- Gunicorn + UvicornWorker in render.yaml
- print() replaced with logger.debug()/warning()
- 5 safe async def → def conversions
- Custom 404 handler for missing routes

### Auth & Security
- Forgot password flow with OTP verification
- Password expiry policy (admin-configurable, max 90 days)
- PasswordExpiryMiddleware forces password change when expired
- T&C checkbox required at registration
- T&C + Privacy links on login page

### Pages & Templates
- FAQ, developer docs, legal pages (Privacy, Terms, DPDP, Cookies, Refund)
- Custom error pages: 403, 404, 500 with friendly designs
- Book demo page → redirects to Zoho Calendar
- Contact form with email delivery

### Bug Fixes
- Invitation model: removed broken relationship("Firm") causing 500
- Reports page: added | safe filter for SVG icons
- Fixed Alembic revision chain typo
- Login/register light theme contrast fixes
- Mobile hamburger visibility on landing page

---

## Previous Sessions Summary

### Multi-Tenancy & Approval System (July 2026)
- Added Firm, Branch, FirmUser models for multi-tenancy
- Implemented approval rules and approval requests system
- Added extension requests for allocation extensions
- Firm-scoped all data with firm_id FKs

### Licensing System (July 2026)
- Added SuperAdmin model (platform-level admin)
- License key generation with HMAC signing
- Standard/Enterprise tiers with user/member limits
- LicenseMiddleware blocks access for expired licenses
- Trial/grace/expired states

### API v1 for Next.js Frontend (July 2026)
- JWT authentication (access + refresh tokens)
- Full JSON API mirroring all web routes
- Endpoints: auth, dashboard, team-members, clients, engagements, assignments, leaves, approvals, extensions

### OTP & Invitation System (July 2026)
- 6-digit email OTP for registration and login
- Rate limiting on OTP resend
- Token-based invitations with bulk CSV support
- Role promotion and super admin transfer

### FrankenUI Migration (July 2, 2026)
- Updated all templates to FrankenUI 2.x
- Flash message positioning, searchable dropdowns
- Assign Staff page, engagement detail page
- Deployed to Render

---

## Key URLs
- **Render:** https://app.skilledca.in
- **Custom Domain:** https://app.skilledca.in
- **Login:** /auth/login
- **Dashboard:** /dashboard

---

## Environment Variables
- `DATABASE_URL` — Supabase Session Pooler PostgreSQL connection string
- `SECRET_KEY` — App secret key
- `ENV` — production/development
- `TESTING` — false (must be true to run tests against DB)
- `LICENSE_SIGNING_KEY` — HMAC key for license validation
- SMTP config (ZeptoMail: smtp.zeptomail.in:587, STARTTLS; username: emailapikey)
- MS365 OAuth config (optional)

# splanly Deployment & Setup Guide

## Part 1: Deploy to Render

### Prerequisites
- GitHub account with the `pm-tool` repository
- Render account (free at [render.com](https://render.com))
- Supabase PostgreSQL project

---

### Step 1: Push Latest Changes to GitHub

```bash
cd pm-tool
git add .
git commit -m "your commit message"
git push origin main
```

---

### Step 2: Create Render Web Service

1. Go to [render.com](https://render.com) and sign in
2. Click **New** → **Web Service**
3. Connect your GitHub account
4. Select the `pm-tool` repository

---

### Step 3: Configure the Web Service

| Setting | Value |
|---------|-------|
| **Name** | `staffplan` |
| **Region** | Choose closest to your users |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `alembic upgrade head && gunicorn app.main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT` |
| **Instance Type** | Free or paid for production |

---

### Step 4: Add Environment Variables

| Variable | Value | Notes |
|----------|-------|-------|
| `DATABASE_URL` | `postgresql://...` | Supabase Session Pooler URL; use port 5432 and `sslmode=require` |
| `SECRET_KEY` | Generate: `python -c "import secrets; print(secrets.token_hex(32))"` | Must be unique |
| `ENV` | `production` | |
| `LICENSE_SIGNING_KEY` | Generate: `python -c "import secrets; print(secrets.token_hex(32))"` | For license validation |

**Optional:**

| Variable | Value | Notes |
|----------|-------|-------|
| `MS365_TENANT_ID` | Azure AD tenant ID | For OAuth |
| `MS365_CLIENT_ID` | Azure AD app client ID | For OAuth |
| `MS365_CLIENT_SECRET` | Azure AD app secret | For OAuth |
| `MS365_REDIRECT_URI` | `https://your-app.onrender.com/auth/callback` | OAuth callback |
| `SMTP_HOST` | `smtp.zeptomail.in` | For emails |
| `SMTP_PORT` | `587` | |
| `SMTP_USER` | `emailapikey` | ZeptoMail SMTP username |
| `SMTP_PASSWORD` | Your ZeptoMail API key | |
| `SMTP_FROM_EMAIL` | `noreply@yourdomain.com` | Default From for OTP, contact, notifications |
| `SMTP_USE_TLS` | `true` | Set true for port 587 (STARTTLS), false for implicit SSL |
| `SMTP_USE_SSL` | `false` | Set true for port 465 (SSL) |
| `SMTP_INVITATION_FROM_EMAIL` | `hello@yourdomain.com` | From address for invitations & role-change emails |

---

### Step 5: Deploy

1. Click **Create Web Service**
2. Wait for build to complete (2-5 minutes)
3. Check logs for errors
4. Access your app at the Render URL

After deployment, confirm the logs contain `Running 'alembic upgrade head ...'`
and that the migration reaches the current head. The application database user
must be able to run migrations and bypass RLS; do not use an anonymous or
authenticated API key as `DATABASE_URL`.

---

### Step 6: Create Developer Account

The seed migration automatically creates a sample firm ("skilledca enterprises") with
an admin user (`samarth@skilledca.in`). To create a platform-level **developer** account
for the dev portal, use Render Shell:

```bash
python -c "
from app.database import SessionLocal
from app.models.super_admin import SuperAdmin
from app.services.auth_service import hash_password
db = SessionLocal()
dev = SuperAdmin(email='dev@yourcompany.com', display_name='Developer', password_hash=hash_password('your-secure-password'), is_active=True)
db.add(dev)
db.commit()
print('Developer account created!')
"
```

**Developer** = platform-level access to the dev portal (private endpoint).
**Admin** = firm-level access with UAM + reports.
**Super Admin** = firm-level access with all rights (incl. license management).

---

## Part 2: Authentication Methods

### Password + OTP (Default)
1. User registers with email
2. Domain matched to firm
3. OTP sent to email for verification
4. User sets password
5. Login: password → OTP verification

### 2FA (TOTP) — Optional per firm
- Enable in Admin Settings → Auth Method
- User scans QR code with authenticator app
- Login: password → 6-digit TOTP code

### MS365 OAuth — Optional
- Configure Azure AD app credentials in env vars
- Users click "Sign in with Microsoft"
- Auto-creates user and links to firm by domain

---

## Part 3: License Management

### License Tiers
| Tier | Users | Team Members | Features |
|------|-------|--------------|----------|
| Standard | 50 | 599 | Core features |
| Enterprise | 100 | 5000 | All features |

### Activate License
1. Go to `/license/activate`
2. Enter license key
3. Key is validated and firm is activated

### Trial Mode
New firms get a trial period. After expiry, access is blocked until license is activated.

---

## Troubleshooting

**"Invalid CSRF token" on forms:**
- Clear browser cookies
- Try incognito mode

**License middleware blocking access:**
- Check `/license/status` for license state
- Activate license if expired

**Email not sending:**
- Check SMTP config in env vars
- Check `/admin/outbox` for failed emails
- Email worker only starts if SMTP_HOST is configured

**Tests wiping production data:**
- Ensure `TESTING=false` in `.env`
- Safety guards prevent cleanup unless `TESTING=true`

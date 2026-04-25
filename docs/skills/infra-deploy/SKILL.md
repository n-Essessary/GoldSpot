---
name: infra-deploy
description: "Deployment, environment config, and cloud integration (Vercel, Railway)."
---

# Infrastructure & Deploy

## Platforms

- **Frontend**: Vercel (auto-deploy from GitHub main)
- **Backend**: Railway (auto-deploy from GitHub main)
- **Database**: Railway PostgreSQL (persistent, same project as backend)

---

## Environment Setup

### Railway Backend
```
DATABASE_URL=postgres://postgres:PASSWORD@centerbeam.proxy.rlwy.net:23586/railway
ADMIN_API_KEY=<secret>
ALLOWED_ORIGINS=https://gold-spot.vercel.app
```

### Vercel Frontend
```
VITE_API_URL=https://scintillating-flexibility-production-809a.up.railway.app
```

---

## Deploy Rules

- Never hardcode backend URL in frontend source
- Never hardcode DB credentials in source
- `ALLOWED_ORIGINS` must include exact Vercel URL (no trailing slash)
- Adding new frontend domain → append to `ALLOWED_ORIGINS` comma-separated

---

## Post-Deploy Verification

```bash
# 1. Backend alive + parsers running
curl https://scintillating-flexibility-production-809a.up.railway.app/parser-status

# 2. Frontend reaching backend
# Check browser Network tab: 200 on /meta every 10s

# 3. DB migration applied
# Check Railway logs: "alembic upgrade head" completed without error
```

---

## Alembic Migration Deploy

Start command (Railway): `alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT`

1. Add migration file locally
2. Test: `alembic upgrade head` locally against Railway DB URL
3. Push to GitHub → Railway deploys → migration runs on startup
4. Verify in Railway logs: no `alembic` errors

---

## Rollback

- Railway: "Rollback to previous deploy" in dashboard
- DB schema: `alembic downgrade -1` (only if migration has downgrade path)
- Frontend: Vercel "Instant Rollback" in dashboard

---

## Common Config Mistakes

| Mistake | Result | Fix |
|---|---|---|
| CORS missing Vercel URL | Browser blocks all API calls | Add to `ALLOWED_ORIGINS` |
| Missing `DATABASE_URL` | Parsers work, no DB persistence | Set in Railway variables |
| Missing `VITE_API_URL` | Frontend hits localhost | Set in Vercel environment |
| Wrong `ADMIN_API_KEY` | `/admin/*` returns 403 | Verify value matches Railway var |
| Trailing slash in `ALLOWED_ORIGINS` | CORS fails silently | Remove trailing slash |

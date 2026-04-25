---
name: devops
description: "Infrastructure, deployment, monitoring, and reliability."
---

# DevOps

## Infrastructure

| Component | Platform | URL |
|---|---|---|
| Frontend | Vercel | gold-spot.vercel.app |
| Backend | Railway | scintillating-flexibility-production-809a.up.railway.app |
| Database | Railway PostgreSQL | centerbeam.proxy.rlwy.net:23586 |

---

## Environment Variables

### Backend (Railway)
```
DATABASE_URL         # postgres://postgres:...@centerbeam.proxy.rlwy.net:23586/railway
ADMIN_API_KEY        # for /admin/* endpoints
ALLOWED_ORIGINS      # https://gold-spot.vercel.app (comma-separated)
```

### Frontend (Vercel)
```
VITE_API_URL         # https://scintillating-flexibility-production-809a.up.railway.app
```

**Never hardcode URLs. Never commit secrets.**

---

## Deployment Checklist

### Backend (Railway)
1. Push to main → Railway auto-deploys
2. Verify: `GET /parser-status` → `offers > 0` within 60s
3. If schema changed: verify `alembic current` matches expected revision
4. Check Railway logs for `WARNING` (missing aliases, parser errors)

### Frontend (Vercel)
1. Push to main → Vercel auto-deploys
2. Verify `VITE_API_URL` set in Vercel dashboard
3. Check browser console for CORS errors

---

## Monitoring (built-in endpoints)

```bash
curl https://.../parser-status
curl -H "X-Admin-Key: $KEY" https://.../admin/quarantine
curl -H "X-Admin-Key: $KEY" https://.../admin/unresolved-servers
curl -H "X-Admin-Key: $KEY" https://.../admin/price-profiles
```

### Alert Conditions
- `funpay.offers == 0` for > 2 cycles → parser broken
- `g2g.offers == 0` for > 2 cycles → G2G API issue or block
- Quarantine growing rapidly → missing aliases
- `last_error != null` → check Railway logs

---

## Logging Configuration

```python
# Suppress httpx INFO spam
for name in ("httpx", "httpcore"):
    logging.getLogger(name).setLevel(logging.WARNING)
```

Log levels: `INFO` for cycle completion, `WARNING` for degraded states, `ERROR` for failures, `DEBUG` for per-offer details (disabled in prod).

---

## Common Prod Issues

| Issue | Diagnosis | Fix |
|---|---|---|
| CORS error | `ALLOWED_ORIGINS` missing URL | Update Railway env var |
| Admin 403 | Wrong `X-Admin-Key` value | Verify matches `ADMIN_API_KEY` |
| `offers: 0` after deploy | Cold start — wait 60s | Check `/parser-status` |
| DB connection refused | Railway PostgreSQL restarted | Backend reconnects via pool |
| Alias cache stale | New alias added | Wait 60s or redeploy |

---

## Scaling Notes

- Current: single Railway instance, in-memory cache
- Bottleneck at scale: in-memory cache not shared across instances
- G2G Semaphore(20): limits concurrent Phase-2 HTTP connections
- Snapshot batching (50/gather) prevents DB pool exhaustion

---
name: server-registry
description: "Canonical entity registry and alias management — maps raw parser titles to canonical entity IDs. Currently covers WoW servers; extensible to any game entity."
---

# Entity Registry (Server Registry)

## Overview

Two-table system that maps raw parser titles → canonical entity identities.

```
entities (servers)      — canonical registry (~230 WoW servers)
entity_aliases          — raw title → entity_id mapping (~300+ aliases)
```

The registry is the source of truth for all entity metadata. Parser data is trusted only for prices and quantities — never for entity identity.

---

## `servers` Table Schema

**Full schema (all columns, indexes, unique constraints):** see `_registry` → Section 8.

Key points for entity resolution:
- Unique lookup key: `(game, name, region, version)` — `game` column distinguishes entries across games (`wow_classic`, `wow_retail`, future)
- For non-WoW games: extend with asset-type or platform-specific columns as needed

---

## `server_aliases` Table Schema

**Full schema:** see `_registry` → Section 8.

Hot path: `alias_key` lookup runs on every offer in every parse cycle. Unique index mandatory. Cached in memory with TTL (see `_registry` § 13).

---

## `alias_key` Construction

```python
def _build_alias_key(display_server, server_name, faction, source):
    parts = [
        display_server.lower().strip(),
        (server_name or "").lower().strip(),
        faction.lower().strip(),
        (source or "").lower().strip(),
    ]
    return "|".join(parts)
```

Examples:
```
"(eu) anniversary|spineshatter|alliance|g2g"
"(eu) classic|firemaw|horde|funpay"
"(eu) anniversary||all|g2g"     ← empty server_name is valid
```

**Always build via `_build_alias_key()` — never construct manually.**

---

## Alias Cache (`server_resolver.py`)

**TTL value:** see `_registry` → Section 13.

- In-memory dict: `alias_key → server_id`
- Load: single `SELECT alias_key, server_id FROM server_aliases`
- Must use **exponential backoff + circuit breaker** on DB failure
  - Railway cold starts cause transient connection errors → log spam without circuit breaker
- Cache failure → return `{}`, do NOT raise → pipeline uses `entity_id=None`
- New alias registered via admin endpoint → visible in cache within one TTL cycle (no redeployment)

---

## Adding a New Entity

### 1. Add to `servers` table
```sql
INSERT INTO servers (name, region, version) VALUES ('Firemaw', 'EU', 'Classic Era');
```

### 2. Add aliases
```sql
INSERT INTO server_aliases (alias_key, server_id, source)
VALUES ('(eu) classic era|firemaw|horde|g2g', 42, 'g2g');
```

### 3. Via admin endpoint (preferred for production)
```bash
curl -X POST \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  "https://.../admin/register-alias?alias=...&server_id=42&source=g2g"
```

---

## Diagnosing Missing Aliases

```bash
# Most frequent unresolved titles (by count DESC)
curl -H "X-Admin-Key: $KEY" https://.../admin/unresolved-servers

# Quarantine log
curl -H "X-Admin-Key: $KEY" https://.../admin/quarantine
```

Workflow: unresolved → look up server_id in `servers` → call `/admin/register-alias`.

---

## Region Override Rules

- FunPay sometimes labels AU realms as US → override in normalization, not in alias
- Source `raw_region` preserved for traceability
- Canonical `region` in `servers` table is ground truth

---

## Version Canonicalization (before alias lookup)

**Canonical version aliases:** see `_registry` → Section 2.
**Source of truth in code:** `backend/service/version_utils.py::_VERSION_ALIASES`

Apply before building `alias_key`. Example: `"(EU) Seasonal"` → `"(EU) Season of Discovery"` → then build key.

---

## Special Character Rules

- Unicode apostrophe `\u2019` in realm names → normalize to ASCII `'` before alias_key
- Cyrillic names (FunPay RU): stored as-is in `servers`, lowercased in `alias_key`
- `#` prefix (FunPay Hardcore indicator) → strip before alias_key

---

## What Must NOT Happen

- Server added without corresponding aliases → unresolved forever
- Manual alias_key construction (not via `_build_alias_key()`) → key mismatch
- `display_server` containing `realm_type` registered as alias → corrupts grouping
- Cache bypassed on every offer → DB overload
- Missing alias causing quarantine → only `entity_id=None`, not rejection
- Parser region used as canonical region without normalization

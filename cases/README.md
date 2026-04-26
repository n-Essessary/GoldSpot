# Case Log — README

A living archive of bugs, fixes, and architectural decisions for GoldSpot.

## Purpose

Not documentation of "how things work today" — that lives in chat instructions and skills.
This is **why things ended up the way they are** and **what was tried before**.

When building a new parser, debugging a similar issue, or considering a redesign:
read the relevant `cases-*.md` first. Avoid re-walking paths already proven dead.

## Files

| File | Scope |
|---|---|
| `cases-parsers.md` | G2G, FunPay, future marketplaces — adapter layer |
| `cases-pipeline.md` | normalize_pipeline, alias cache, server_resolver, validation |
| `cases-db.md` | PostgreSQL schema, migrations, snapshots, alembic |
| `cases-frontend.md` | React, lightweight-charts, useOffers, frontend state |
| `cases-infra.md` | Railway, Vercel, external APIs, deployment, CORS |

## Format

Two case types:

**[BUG]** — atomic format
```
Symptom → Root cause → What was tried → Resolution → Prevention
```

**[ARCH]** — narrative format
```
Context → Options considered (with rejection reasons) → Chosen → Invariants → If building similar
```

Skip sections that have nothing real to fill. Honesty over template completeness.

## When Claude generates a new case

Triggered automatically by chat instructions when a case closes ("готово", "закрыто", "кейс закрыт").

Claude will output a markdown block prefixed with:
```
📋 CASE LOG → paste into project knowledge file: cases-{domain}.md
```

User pastes it into the appropriate file via Project knowledge UI.

## What does NOT belong here

- Routine features without surprises (changelog material, not case material)
- Refactorings without sticking points
- Rules already in chat instructions (don't duplicate — link instead)
- Speculation about untested approaches

## Updating existing cases

- New solution superseded old → mark old `Status: superseded by [link]`, do not delete
- Additional root cause discovered → add to `Root cause` section, append date to header
- Invariant changed → update + note what triggered the change

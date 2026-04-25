---
name: cowork-prompt
description: "Template and rules for generating Cowork task prompts. Use when user asks to generate a prompt for Cowork, or when any multi-file backend task is delegated to Cowork. Ensures every Cowork prompt has file targets, Do NOT Touch section, skill references from docs/skills/, and verification commands pulled from failure-modes."
---

# Cowork Task Prompt Generation

> **Authority:** This skill is the canonical template for Cowork task prompts. Chat instructions reference this skill instead of duplicating the template.
>
> **Language:** All Cowork prompts in **English**.

---

## Path Convention (critical)

Cowork **does not see** Claude.ai's `/mnt/skills/user/` directory. Cowork reads files from the **project repository**, where skills live at `docs/skills/`.

Rules:
- In Cowork prompts → always use `docs/skills/<n>/SKILL.md` (repo-relative path)
- In Claude chat (this environment) → still use `/mnt/skills/user/<n>/SKILL.md`
- The two locations must be kept in sync — see Sync Protocol at the bottom

If Cowork reports it cannot find a skill at `docs/skills/...` → that means the skill was added in Claude.ai but not yet committed to the repo. Tell the user and stop.

---

## When to use this skill

- User asks "generate a prompt for Cowork" / "промпт для Cowork"
- User delegates a multi-file backend task and Cowork is the chosen tool
- User describes a task and asks Claude to produce a ready-to-paste Cowork prompt

---

## Template (fill every section, delete those that don't apply)

```markdown
# Task: <short imperative title>

## Context
<2–4 sentences: what problem, why now, what was already tried if anything>

## Required Reading
Before writing any code, read:
- docs/skills/_registry/SKILL.md  (always)
- docs/skills/failure-modes/SKILL.md  (grep for: <keywords from symptom>)
- docs/skills/<skill-1>/SKILL.md
- docs/skills/<skill-2>/SKILL.md

## Critical Values (fallback if file unreadable)
<inline copy of the most relevant 3–8 values from _registry sections that this task depends on. Always include this — Cowork uses it if file read fails for any reason.>

## Files to Modify
- backend/parser/<file>.py
- backend/service/<file>.py
- <list every expected file — no surprises>

## Do NOT Touch
- backend/api/schemas.py  (API contract)
- backend/db/writer.py  (write path, COALESCE guard, _flatten_param helper)
- _snapshot_running flag / background loop structure
- <anything else critical to this task>

## Task
1. <concrete step>
2. <concrete step>
3. <concrete step>

## Acceptance Criteria
- <specific, testable condition>
- Existing tests still pass
- No new quarantine entries for previously-working offers

## Verification
- `alembic heads | wc -l` → must be 1
- <curl command with expected response>
- Regression Guard from failure-modes (verbatim): "<copy exact text>"

## Registry Check
If your changes introduce or modify any of: game version, brand_id, cycle config, schema column, magic number, env var, API endpoint
→ update docs/skills/_registry/SKILL.md § <N> in the same commit.
After commit, also notify in your report so the Claude.ai mirror can be synced.

## Reporting
Use the Output Format from the Cowork system prompt. List uncertainties explicitly.
```

---

## Mandatory Rules When Filling the Template

1. **All paths use `docs/skills/...`** — never `/mnt/skills/user/...`. Cowork cannot see the latter.

2. **Required Reading must always include `_registry` and `failure-modes`** — no exceptions. Other skills selected per Skill Activation Map.

3. **Critical Values section is mandatory** — copy 3–8 most relevant values inline as fallback. If Cowork fails to read the file (repo state mismatch, etc.), this prevents wrong assumptions. Always include version enum if relevant, brand_id if relevant, magic numbers and formulas the task depends on.

4. **Do NOT Touch must always include at minimum:**
   - `db/writer.py` guards (`_flatten_param`, `COALESCE(sources, ARRAY[]::text[])`)
   - `_snapshot_running` flag
   - API response shapes (unless task explicitly changes them)
   - Any parser unrelated to the task

5. **Verification must pull Regression Guards from `failure-modes` verbatim** — copy exact text in quotes, do not summarize.

6. **Registry Check must name specific `_registry` sections** — not "update registry if needed". Example: `§ 6` for cycles, `§ 8` for schema, `§ 11` for endpoints, `§ 13` for magic numbers.

7. **Files to Modify must be exhaustive** — if unsure which files are in scope, ask the user before generating the prompt.

8. **Never generate a Cowork prompt without file targets.** If unclear → ask, do not guess.

---

## Full Example — Parser Bug Fix

```markdown
# Task: Fix G2G Phase 1 pagination stopping at page 1

## Context
Classic Era offer count dropped from ~300 to ~50 overnight. Checked Railway logs — no errors. Suspect Phase 1 pagination loop broken.

## Required Reading
- docs/skills/_registry/SKILL.md
- docs/skills/failure-modes/SKILL.md  (grep: "G2G low offer count", "pagination")
- docs/skills/parser/SKILL.md
- docs/skills/marketplace-architecture/SKILL.md

## Critical Values (fallback if file unreadable)
- Phase 1 pagination rule (registry § 6): paginate until `len(results) < page_size` OR `page > max_pages`
- Classic max_pages: 10, page_size: 48
- Phase 1 prices are NEVER used — discovery only (registry § 5)
- filter_attr formula (registry § 5): `prefix = re.sub(r"_\d+$", "", og); fa = f"{prefix}:{og}"`

## Files to Modify
- backend/parser/g2g_parser.py

## Do NOT Touch
- Phase 2 logic (filter_attr construction, group=0 param)
- API response parsing
- Semaphore / concurrency setup
- Any other parser
- db/writer.py

## Task
1. Read current Phase 1 loop in g2g_parser.py
2. Verify pagination condition matches registry § 6 (loop until `len(results) < page_size` OR `page > max_pages`)
3. Fix the bug
4. Add a log line showing total unique (offer_group, region_id) pairs collected after Phase 1

## Acceptance Criteria
- `len(unique_pairs) >= 100` after Phase 1 on Classic Era cycle
- Offer count in /offers returns to ~300
- No changes to Phase 2 logic
- Log line visible in Railway: `Phase 1 collected <N> unique pairs for <game>`

## Verification
- Deploy to Railway
- Check Railway logs for new log line
- `curl https://scintillating-flexibility-production-809a.up.railway.app/offers | jq '.count'` → ~300, not ~50
- Regression Guard from failure-modes § [parser] G2G low offer count (verbatim):
  "log `len(unique_pairs)` after Phase 1 — must be ≥ expected_server_count × ~2 (two factions per server)"

## Registry Check
If `_MAX_PAGES_CLASSIC` or `page_size` changes → update docs/skills/_registry/SKILL.md § 6.

## Reporting
Use the Output Format. If Phase 1 was stopping due to a different root cause, flag clearly.
```

---

## Full Example — Schema Migration

```markdown
# Task: Add `game` column to `server_aliases` table

## Context
`server_aliases` has no `game` discriminator. When WoW Classic alias "Firemaw" collides with a future game's alias, lookup breaks. Add `game` column and backfill.

## Required Reading
- docs/skills/_registry/SKILL.md  (§ 8 for current schema)
- docs/skills/failure-modes/SKILL.md  (grep: "alembic", "alias")
- docs/skills/database-engineer/SKILL.md
- docs/skills/server-registry/SKILL.md

## Critical Values (fallback if file unreadable)
- servers schema (registry § 8): UNIQUE (game, name, region, version) — `game` already exists on `servers`
- server_aliases current schema (registry § 8): id, alias_key UNIQUE, server_id, source, created_at — NO `game` yet
- Alias cache TTL (registry § 13): 60s
- Alembic discipline (failure-modes § [db]): always `alembic heads` before new migration

## Files to Modify
- backend/alembic/versions/022_*.py  (new migration)
- backend/db/writer.py  (update queries — keep guards intact)
- backend/service/server_resolver.py  (update alias_key cache logic)

## Do NOT Touch
- `servers` table schema (already has game column)
- Alias cache TTL value
- Hot-path lookup function signature — keep backward compatible
- `_flatten_param()` helper and `COALESCE(sources, ARRAY[]::text[])` guard

## Task
1. Run `alembic heads` → confirm single head before writing migration
2. Create migration 022:
   - Add column `game TEXT NOT NULL DEFAULT 'wow_classic'`
   - Backfill: `UPDATE server_aliases SET game = 'wow_retail' WHERE server_id IN (SELECT id FROM servers WHERE game = 'wow_retail')`
   - Drop unique constraint on `alias_key` alone, recreate as `UNIQUE (game, alias_key)`
3. Update `db/writer.py` insert query to include `game` — preserve COALESCE guard
4. Update `server_resolver.py` alias cache to key on `(game, alias_key)`
5. Update docs/skills/_registry/SKILL.md § 8 with new schema

## Acceptance Criteria
- `alembic upgrade head` runs clean on fresh DB
- `alembic upgrade head` runs clean on current prod snapshot
- All existing alias lookups still resolve
- registry § 8 reflects new schema

## Verification
- `alembic heads | wc -l` → 1
- Local: apply migration, run `python -c "from backend.service.server_resolver import resolve; print(resolve('firemaw', 'wow_classic'))"` → returns server_id
- Regression Guard from failure-modes § [db] Alembic broken chain (verbatim):
  "CI check `alembic heads | wc -l` == 1"

## Registry Check
Update docs/skills/_registry/SKILL.md § 8 after migration written.

## Reporting
Flag if backfill query takes > 30s on prod snapshot — may need batched version.
```

---

## Anti-Patterns to Avoid

- ❌ Path `/mnt/skills/user/...` in a Cowork prompt — Cowork can't read it
- ❌ Skipping the "Critical Values" inline section — file reads can fail
- ❌ "Please fix the parser bug" → no file targets, no reading, no verification
- ❌ "Update the _registry if needed" → not specific enough; name the section
- ❌ Skipping "Do NOT Touch" because "it's obvious" → it isn't, and Cowork will touch things
- ❌ Paraphrasing a Regression Guard instead of copying verbatim → loses specificity
- ❌ Leaving "Files to Modify" vague or open-ended → Cowork will modify unrelated files

---

## Sync Protocol — Two Skill Locations

There are two copies of the skills:

| Location | Used by | Update trigger |
|---|---|---|
| `/mnt/skills/user/<n>/SKILL.md` | Claude.ai chat | Claude updates directly during a session |
| `docs/skills/<n>/SKILL.md` | Cowork (and Cursor / Claude Code in repo) | Committed to git via PR |

**The git copy is the source of truth.** When they diverge:

1. **Claude.ai-side change is fresher** (just edited in chat) → user must commit the same change to repo before next Cowork run
2. **Repo-side change is fresher** (Cowork pushed an update) → Claude.ai mirror is stale; user must paste updated content into Claude.ai skill on next session
3. **Both sides changed** → conflict-resolution skill applies

**On every Claude.ai skill edit, Claude must remind:** "Don't forget to mirror this change to `docs/skills/<name>/SKILL.md` in the repo, otherwise next Cowork run will use stale data."

---

## Output When Claude Generates a Cowork Prompt

Wrap the generated prompt in a code block for easy copy-paste:

````
Here's the Cowork prompt:

```markdown
# Task: ...
...
```

Copy everything between the triple backticks into Cowork.
````

Do not add commentary before or after unless the user asked for explanation.

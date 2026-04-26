# Skills Bundle — Installation Guide

## What is this

A snapshot of all GoldSpot skills, ready to be committed to your repo at `docs/skills/`. After you commit, Cowork (and Cursor / Claude Code) can read them directly from the project filesystem.

Currently 22 skills, ~60KB total.

---

## One-time setup

From the repo root:

```bash
# 1. Make sure docs/ exists
mkdir -p docs

# 2. Extract the bundle (assumes you downloaded the docs-skills-bundle.tar.gz)
tar -xzf docs-skills-bundle.tar.gz

# 3. Verify
ls docs/skills/ | wc -l   # should print: 22

# 4. Commit
git add docs/skills/
git commit -m "Add skills documentation for AI assistants (Cowork, Cursor, Claude Code)"
git push
```

That's it. Cowork will now find files at paths like `docs/skills/_registry/SKILL.md`.

---

## How updates work going forward

When Claude (in chat) edits a skill, you'll see a reminder like:

> ⚠ Don't forget to mirror this change to `docs/skills/parser/SKILL.md` in the repo and commit, otherwise Cowork/Cursor will use stale data.

Workflow:
1. Claude edits `/mnt/skills/user/<n>/SKILL.md` in chat (you see the diff)
2. You manually paste the same content into `docs/skills/<n>/SKILL.md` in your IDE
3. Commit + push
4. Next Cowork run picks up the new version

When Cowork edits a skill (during a task), it will report:

```
## Skill Updates
- docs/skills/_registry/SKILL.md § 6: updated max_pages from 10 to 25 for retail
```

Workflow:
1. Cowork commits the change
2. Pull to local
3. Open updated file, copy content, paste into Claude.ai skill UI (Settings → Profile → Skills) so Claude.ai mirror stays current
4. Or — at the start of next chat session, Claude can re-read from the repo if you paste the file content

---

## Sync conventions

- **Source of truth**: the git copy at `docs/skills/`
- **Claude.ai copy**: convenience for in-chat editing; must be manually mirrored
- **Conflict** (both edited): the git copy wins; revert Claude.ai change and reapply against latest

---

## File structure

```
docs/skills/
├── _registry/SKILL.md          ← always read first; volatile values
├── failure-modes/SKILL.md      ← known bugs registry
├── cowork-prompt/SKILL.md      ← template for Cowork prompts (used by Claude in chat)
├── parser/SKILL.md
├── marketplace-architecture/SKILL.md
├── normalize-pipeline/SKILL.md
├── server-registry/SKILL.md
├── database-engineer/SKILL.md
├── backend-api/SKILL.md
├── data-logic/SKILL.md
├── data-science/SKILL.md
├── frontend/SKILL.md
├── layout-ui/SKILL.md
├── qa-testing-skill/SKILL.md
├── senior-python/SKILL.md
├── debug/SKILL.md
├── conflict-resolution/SKILL.md
├── system-architecture/SKILL.md
├── analytics-product/SKILL.md
├── price-profiles/SKILL.md
├── infra-deploy/SKILL.md
└── devops/SKILL.md
```

---

## Optional: gitignore exclusion (don't do this)

Don't add `docs/skills/` to `.gitignore`. The whole point is they're versioned with the code so Cowork reads consistent snapshots tied to your branch.

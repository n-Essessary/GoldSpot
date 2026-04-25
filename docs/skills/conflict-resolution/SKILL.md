---
name: conflict-resolution
description: "What to do when skill documentation contradicts real code. Mandatory protocol before taking any action on a conflict."
---

# Conflict Resolution

## When This Applies

This skill activates whenever you detect a contradiction between:
- A skill's documented rules and the actual code in the repository
- Instructions and the actual API/DB schema
- Two skills that describe the same component differently
- A prompt's assumptions and the code you observe when opening files

**Do not resolve the conflict silently. Do not pick a side. Stop and ask.**

---

## Mandatory Protocol

1. **State the conflict explicitly** — quote both sides (skill vs code)
2. **Identify the scope** — which files, which behavior, which rule is affected
3. **Present options** — always offer at least 3 choices (see below)
4. **Wait for explicit user confirmation** — do not proceed until answered

---

## Standard Options to Present

When a conflict is detected, always offer these options (adapt wording to context):

**Option A — Trust the code, update the skill**
> "The code is correct. Update the skill/instructions to match what the code actually does."
> Use when: the code was recently changed and documentation wasn't updated yet.

**Option B — Trust the skill, update the code**
> "The skill describes the intended design. Refactor the code to match the documented architecture."
> Use when: the code is an old implementation that drifted from the intended design.

**Option C — Both are partially correct, merge**
> "Neither is fully right. I'll propose a reconciled version that takes the correct parts from each."
> Use when: the skill captures intent correctly but misses implementation details, or vice versa.

**Option D — Freeze, investigate first**
> "Don't touch anything yet. Show me the full diff between what the skill says and what the code does, so I can decide."
> Use when: the scope of the conflict is unclear and action could cause regression.

---

## Conflict Report Format

```
CONFLICT DETECTED
─────────────────────────────────────────
Skill/doc says:
  [exact quote from skill or instructions]

Code does:
  [exact quote or description from file + line numbers]

Affected files:
  [list of files involved]

Risk if ignored:
  [what breaks or drifts if left unresolved]
─────────────────────────────────────────
How should I proceed?

  A) Trust the code — update skill/instructions to match
  B) Trust the skill — refactor code to match documentation
  C) Merge both — I'll propose a reconciled version
  D) Freeze — show me the full diff first, I'll decide
```

---

## Rules

- Never silently pick Option A or B without asking
- Never partially apply a fix without stating which option was chosen
- If the user picks Option B (refactor code): treat it as a new task — write a full implementation prompt, do not inline the refactor
- If the user picks Option A (update docs): update all affected skills and instructions in one pass, not piecemeal
- If the user picks Option C: present the merged proposal before writing any code or docs
- After resolution: add the conflict + chosen resolution to the relevant skill's changelog or notes section

---

## Priority

This skill overrides all other skills when a conflict is detected.
No other skill's rules apply until the conflict is resolved.

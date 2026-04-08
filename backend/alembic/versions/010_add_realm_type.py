"""Add realm_type to servers table + fix Hardcore domain model.

Domain model correction (per canonical server spec):
  Hardcore is NOT a game version — it is a realm_type.
  Correct model: version = "Classic Era" | "Anniversary" | "Seasonal"
                 realm_type = "Normal" | "Hardcore"

Changes:
  1. Add realm_type column (TEXT, NOT NULL, DEFAULT 'Normal').
  2. For Classic Era Hardcore realms (Stitches EU, Nek'Rosh EU):
       - version='Hardcore' entries: UPDATE version='Classic Era', realm_type='Hardcore'
         (no duplicate Classic Era entry exists for these — safe UPDATE in-place)
  3. For US Hardcore realms with Classic Era duplicate (Skull Rock, Defias Pillager):
       - Existing (name, US, 'Classic Era') entries → realm_type='Hardcore'
       - Old (name, US, 'Hardcore') entries → is_active=FALSE
       - Redirect aliases from old Hardcore server_id to Classic Era server_id
  4. For Anniversary Hardcore realms (Soulseeker EU, Doomhowl US):
       - Existing (name, region, 'Anniversary') entries → realm_type='Hardcore'
       - Old (name, region, 'Hardcore') entries → is_active=FALSE
       - Redirect aliases from old Hardcore server_id to Anniversary server_id
  5. Add canonical "Classic Era" aliases for Stitches/Nek'Rosh (now Classic Era Hardcore).
  6. Add Season of Mastery placeholder with is_active=FALSE if not present
     (SoM realms are deprecated and must be quarantined).

Revision ID: 010
Revises: 009
Create Date: 2026-04-08
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def _q(s: str) -> str:
    """Minimal SQL string quoting."""
    return "'" + s.replace("'", "''") + "'"


# ── Server lists for alias generation ────────────────────────────────────────

# Classic Era Hardcore servers that have NO existing Classic Era entry.
# Safe to UPDATE version in-place (no UNIQUE conflict).
_CLASSIC_ERA_HC_INPLACE: list[tuple[str, str]] = [
    ("Stitches",  "EU"),
    ("Nek'Rosh",  "EU"),
]

# US Classic Era Hardcore servers that already have a (name, US, 'Classic Era') entry.
# We mark the Classic Era entry as Hardcore and deactivate the Hardcore-version entry.
_CLASSIC_ERA_HC_WITH_DUPLICATE: list[tuple[str, str]] = [
    ("Skull Rock",      "US"),
    ("Defias Pillager", "US"),
]

# Anniversary Hardcore servers (same pattern as above).
_ANNIVERSARY_HC_WITH_DUPLICATE: list[tuple[str, str]] = [
    ("Soulseeker", "EU"),
    ("Doomhowl",   "US"),
]


def upgrade() -> None:
    # ── 1. Add realm_type column ──────────────────────────────────────────────
    op.execute("""
        ALTER TABLE servers
            ADD COLUMN IF NOT EXISTS realm_type TEXT NOT NULL DEFAULT 'Normal';
    """)

    # ── 2. Classic Era Hardcore (in-place UPDATE: version → 'Classic Era') ────
    # Stitches and Nek'Rosh have no existing (EU, Classic Era) row → no conflict.
    for name, region in _CLASSIC_ERA_HC_INPLACE:
        op.execute(f"""
            UPDATE servers
               SET version    = 'Classic Era',
                   realm_type = 'Hardcore'
             WHERE name   = {_q(name)}
               AND region = {_q(region)}
               AND version = 'Hardcore';
        """)

    # ── 3. US Classic Era Hardcore (duplicate entries exist) ──────────────────
    for name, region in _CLASSIC_ERA_HC_WITH_DUPLICATE:
        # 3a. Mark the correct Classic Era entry as Hardcore
        op.execute(f"""
            UPDATE servers
               SET realm_type = 'Hardcore'
             WHERE name    = {_q(name)}
               AND region  = {_q(region)}
               AND version = 'Classic Era';
        """)
        # 3b. Deactivate the now-superseded Hardcore-version entry
        op.execute(f"""
            UPDATE servers
               SET is_active = FALSE
             WHERE name    = {_q(name)}
               AND region  = {_q(region)}
               AND version = 'Hardcore';
        """)
        # 3c. Redirect aliases from Hardcore-version server_id → Classic Era server_id
        op.execute(f"""
            UPDATE server_aliases
               SET server_id = (
                       SELECT id FROM servers
                        WHERE name    = {_q(name)}
                          AND region  = {_q(region)}
                          AND version = 'Classic Era'
                   )
             WHERE server_id = (
                       SELECT id FROM servers
                        WHERE name    = {_q(name)}
                          AND region  = {_q(region)}
                          AND version = 'Hardcore'
                   )
               AND server_id IS NOT NULL;
        """)

    # ── 4. Anniversary Hardcore (duplicate entries exist) ─────────────────────
    for name, region in _ANNIVERSARY_HC_WITH_DUPLICATE:
        # 4a. Mark the Anniversary entry as Hardcore
        op.execute(f"""
            UPDATE servers
               SET realm_type = 'Hardcore'
             WHERE name    = {_q(name)}
               AND region  = {_q(region)}
               AND version = 'Anniversary';
        """)
        # 4b. Deactivate the Hardcore-version entry
        op.execute(f"""
            UPDATE servers
               SET is_active = FALSE
             WHERE name    = {_q(name)}
               AND region  = {_q(region)}
               AND version = 'Hardcore';
        """)
        # 4c. Redirect aliases
        op.execute(f"""
            UPDATE server_aliases
               SET server_id = (
                       SELECT id FROM servers
                        WHERE name    = {_q(name)}
                          AND region  = {_q(region)}
                          AND version = 'Anniversary'
                   )
             WHERE server_id = (
                       SELECT id FROM servers
                        WHERE name    = {_q(name)}
                          AND region  = {_q(region)}
                          AND version = 'Hardcore'
                   )
               AND server_id IS NOT NULL;
        """)

    # ── 5. Add Classic Era aliases for Stitches / Nek'Rosh (now Classic Era) ──
    # Their version was changed from 'Hardcore' to 'Classic Era' in step 2.
    # Add both G2G label variants so they resolve from Classic Era titles too.
    for name, region in _CLASSIC_ERA_HC_INPLACE:
        for faction in ("Alliance", "Horde"):
            for ver_label in ("Classic Era", "Classic", "Hardcore"):
                alias = f"{name} [{region} - {ver_label}] - {faction}"
                op.execute(f"""
                    INSERT INTO server_aliases (server_id, alias, source)
                    SELECT s.id, {_q(alias)}, 'g2g'
                      FROM servers s
                     WHERE s.name    = {_q(name)}
                       AND s.region  = {_q(region)}
                       AND s.version = 'Classic Era'
                    ON CONFLICT (alias) DO NOTHING;
                """)

    # ── 6. Add Classic/Hardcore label aliases for Soulseeker/Doomhowl ─────────
    # G2G sometimes labels these as "Hardcore" in titles; ensure those resolve
    # to the Anniversary entry (which is now the canonical Hardcore record).
    for name, region in _ANNIVERSARY_HC_WITH_DUPLICATE:
        for faction in ("Alliance", "Horde"):
            for ver_label in ("Hardcore", "Anniversary"):
                alias = f"{name} [{region} - {ver_label}] - {faction}"
                op.execute(f"""
                    INSERT INTO server_aliases (server_id, alias, source)
                    SELECT s.id, {_q(alias)}, 'g2g'
                      FROM servers s
                     WHERE s.name    = {_q(name)}
                       AND s.region  = {_q(region)}
                       AND s.version = 'Anniversary'
                    ON CONFLICT (alias) DO NOTHING;
                """)

    # ── 7. Season of Mastery — mark is_active = FALSE (deprecated) ───────────
    # SoM realms closed in 2022. Offers with these server names are quarantined
    # with reason="deprecated_version" by the normalization pipeline.
    op.execute("""
        UPDATE servers
           SET is_active = FALSE
         WHERE version = 'Season of Mastery';
    """)
    # Insert known SoM server list (is_active=FALSE from birth).
    # Historical aliases may appear in legacy snapshots — they resolve but quarantine.
    _SOM_SERVERS: list[tuple[str, str]] = [
        ("Shadowstrike", "EU"),   # SoM closed Mar 2022
        ("Jom Gabbar",   "US"),
        ("Risen Spirits","US"),
        ("Tesladin",     "US"),
        ("Dreadnaught",  "US"),
    ]
    for name, region in _SOM_SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version, is_active, realm_type)
            VALUES ({_q(name)}, {_q(region)}, 'Season of Mastery', FALSE, 'Normal')
            ON CONFLICT (name, region, version) DO
                UPDATE SET is_active = FALSE;
        """)


def downgrade() -> None:
    """Reverse: drop realm_type column (data loss for realm_type assignments)."""
    op.execute("""
        ALTER TABLE servers DROP COLUMN IF EXISTS realm_type;
    """)
    # Note: Hardcore server version / is_active corrections are NOT reversed
    # to avoid accidental data corruption on partial rollbacks.

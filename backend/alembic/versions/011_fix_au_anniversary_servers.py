"""Fix AU Anniversary server grouping — add AU canonical entries and redirect
G2G US-coded aliases for Oceanic realms to their correct AU canonical records.

Problem (confirmed by sidebar analysis):
  G2G labels Oceanic/AEDT Anniversary realms with region code [US] in offer
  titles (e.g. "Nightslayer [US - Anniversary] - Horde"). The alias resolver
  therefore maps these to (name, US, Anniversary) server rows added in
  migration 002, and _apply_canonical sets display_server = "(US) Anniversary".
  Maladath, Dreamscythe, Nightslayer, Doomhowl should instead be grouped under
  "(AU) Anniversary" in the sidebar.

Changes:
  1. Insert AU Anniversary server records for Dreamscythe, Nightslayer, Doomhowl.
     (Maladath AU already exists from migration 007.)
  2. UPDATE existing [US - Anniversary] G2G aliases to point to the AU server_id.
     This is safe: the US-coded alias now canonicalises to the AU group.
  3. INSERT AU-coded G2G aliases ("ServerName [AU - Anniversary] - Faction")
     in case G2G ever corrects the region label in future titles.
  4. Insert FunPay-style aliases ("(AU) Anniversary - ServerName") as explicit
     DB entries so the batch alias lookup hits before fuzzy resolve.
  5. Add [US - Seasonal] G2G aliases for the SoD servers that appear bare in the
     sidebar when _server_data_cache is cold (belt-and-suspenders complement to
     the get_servers() guard added in offers_service.py).

SAFE to run repeatedly: INSERTs use ON CONFLICT DO NOTHING;
UPDATEs are idempotent (pointing an alias at the same server_id is a no-op).

Revision ID: 011
Revises: 010
Create Date: 2026-04-09
"""
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# ── AU Anniversary OCE servers (Nightslayer/Dreamscythe/Doomhowl) ─────────────
# Maladath (AU, Anniversary) already exists from migration 007.
# realm_type: Doomhowl is Hardcore; the others are Normal.
_AU_ANNIVERSARY_NEW: list[tuple[str, str]] = [
    ("Dreamscythe", "Normal"),
    ("Nightslayer", "Normal"),
    ("Doomhowl",    "Hardcore"),
]


# ── SoD servers — explicit FunPay-style aliases (belt-and-suspenders) ────────
# These already resolve via fuzzy lookup, but an explicit alias speeds up
# the batch lookup and avoids log noise on every parse cycle.
_SOD_EU = ["Lava Lash", "Crusader Strike", "Living Flame", "Lone Wolf", "Wild Growth"]
_SOD_US = ["Lava Lash", "Crusader Strike", "Living Flame", "Lone Wolf",
           "Wild Growth", "Chaos Bolt", "Penance", "Shadowstrike"]


def upgrade() -> None:
    # ── 1. Insert AU Anniversary server rows ──────────────────────────────────
    for name, realm_type in _AU_ANNIVERSARY_NEW:
        op.execute(f"""
            INSERT INTO servers (name, region, version, realm_type)
            VALUES ({_q(name)}, 'AU', 'Anniversary', {_q(realm_type)})
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    # ── 2. Redirect existing [US - Anniversary] G2G aliases → AU server ───────
    # G2G uses "US" region code for Oceanic servers in offer titles.
    # The batch resolver must map these to (name, AU, Anniversary) so that
    # _apply_canonical sets display_server = "(AU) Anniversary".
    _redirect_targets = ["Dreamscythe", "Nightslayer", "Doomhowl", "Maladath"]
    for name in _redirect_targets:
        for faction in ("Alliance", "Horde"):
            alias = f"{name} [US - Anniversary] - {faction}"
            op.execute(f"""
                UPDATE server_aliases
                   SET server_id = (
                           SELECT id FROM servers
                            WHERE name    = {_q(name)}
                              AND region  = 'AU'
                              AND version = 'Anniversary'
                       )
                 WHERE LOWER(alias) = LOWER({_q(alias)})
                   AND server_id IS DISTINCT FROM (
                           SELECT id FROM servers
                            WHERE name    = {_q(name)}
                              AND region  = 'AU'
                              AND version = 'Anniversary'
                       );
            """)
        # Also handle Doomhowl [US - Hardcore] alias → AU Anniversary (Hardcore)
        if name == "Doomhowl":
            for faction in ("Alliance", "Horde"):
                alias = f"Doomhowl [US - Hardcore] - {faction}"
                op.execute(f"""
                    UPDATE server_aliases
                       SET server_id = (
                               SELECT id FROM servers
                                WHERE name    = 'Doomhowl'
                                  AND region  = 'AU'
                                  AND version = 'Anniversary'
                           )
                     WHERE LOWER(alias) = LOWER({_q(alias)})
                       AND server_id IS DISTINCT FROM (
                               SELECT id FROM servers
                                WHERE name    = 'Doomhowl'
                                  AND region  = 'AU'
                                  AND version = 'Anniversary'
                           );
                """)

    # ── 3. Insert AU-coded G2G aliases (forward-compat) ──────────────────────
    # In case G2G corrects its region labels to [AU] in future title strings.
    for name, realm_type in _AU_ANNIVERSARY_NEW + [("Maladath", "Normal")]:
        for faction in ("Alliance", "Horde"):
            alias_au = f"{name} [AU - Anniversary] - {faction}"
            op.execute(f"""
                INSERT INTO server_aliases (server_id, alias, source)
                SELECT s.id, {_q(alias_au)}, 'g2g'
                  FROM servers s
                 WHERE s.name    = {_q(name)}
                   AND s.region  = 'AU'
                   AND s.version = 'Anniversary'
                ON CONFLICT (alias) DO NOTHING;
            """)

    # ── 4. Explicit FunPay-style aliases for AU Anniversary realms ────────────
    # FunPay sends "(AU) Anniversary - Maladath" etc. Fuzzy resolve handles
    # these, but explicit DB entries speed up the batch lookup path.
    for name, _rt in _AU_ANNIVERSARY_NEW + [("Maladath", "Normal")]:
        alias_fp = f"(AU) Anniversary - {name}"
        op.execute(f"""
            INSERT INTO server_aliases (server_id, alias, source)
            SELECT s.id, {_q(alias_fp)}, 'funpay'
              FROM servers s
             WHERE s.name    = {_q(name)}
               AND s.region  = 'AU'
               AND s.version = 'Anniversary'
            ON CONFLICT (alias) DO NOTHING;
        """)
        # Lowercase variant (alias cache lookup is case-insensitive via LOWER())
        alias_fp_lo = alias_fp.lower()
        op.execute(f"""
            INSERT INTO server_aliases (server_id, alias, source)
            SELECT s.id, {_q(alias_fp_lo)}, 'funpay'
              FROM servers s
             WHERE s.name    = {_q(name)}
               AND s.region  = 'AU'
               AND s.version = 'Anniversary'
            ON CONFLICT (alias) DO NOTHING;
        """)

    # ── 5. FunPay-style aliases for EU SoD servers ────────────────────────────
    # Ensures the batch alias lookup hits before fuzzy resolve for servers
    # that have appeared as bare top-level items in the sidebar.
    for srv_name in _SOD_EU:
        alias_fp = f"(EU) Season of Discovery - {srv_name}"
        op.execute(f"""
            INSERT INTO server_aliases (server_id, alias, source)
            SELECT s.id, {_q(alias_fp)}, 'funpay'
              FROM servers s
             WHERE s.name    = {_q(srv_name)}
               AND s.region  = 'EU'
               AND s.version = 'Season of Discovery'
            ON CONFLICT (alias) DO NOTHING;
        """)

    # ── 6. FunPay-style aliases for US SoD servers ────────────────────────────
    for srv_name in _SOD_US:
        alias_fp = f"(US) Season of Discovery - {srv_name}"
        op.execute(f"""
            INSERT INTO server_aliases (server_id, alias, source)
            SELECT s.id, {_q(alias_fp)}, 'funpay'
              FROM servers s
             WHERE s.name    = {_q(srv_name)}
               AND s.region  = 'US'
               AND s.version = 'Season of Discovery'
            ON CONFLICT (alias) DO NOTHING;
        """)


def downgrade() -> None:
    """Partial rollback — removes the AU server rows added in step 1.
    Does NOT reverse alias UPDATEs (restoring old server_ids is unsafe
    without the exact prior values).
    """
    for name, _rt in _AU_ANNIVERSARY_NEW:
        op.execute(f"""
            DELETE FROM servers
             WHERE name    = {_q(name)}
               AND region  = 'AU'
               AND version = 'Anniversary';
        """)

"""Add missing G2G servers/aliases batch 3 from production unresolved logs.

Revision ID: 008
Revises: 007
Create Date: 2026-04-07
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


_SERVERS = [
    # OCE Classic
    ("Remulos", "OCE", "Classic"),
    ("Felstriker", "OCE", "Classic"),
    ("Arugal", "OCE", "Classic"),
    ("Yojamba", "OCE", "Classic"),
    # RU Classic
    ("Chromie", "RU", "Classic"),
    ("Rhok'delar", "RU", "Classic"),
    ("Wyrmthalak", "RU", "Classic"),
    ("Flamegor", "RU", "Classic"),
    ("Harbinger of Doom", "RU", "Classic"),
    # RU Season of Discovery
    ("Shadowstrike", "RU", "Season of Discovery"),
    ("Penance", "RU", "Season of Discovery"),
    # Reappearing unresolved from batch2 group
    ("Flamelash", "EU", "Classic"),
    ("Stonespine", "EU", "Classic"),
    ("Ten Storms", "EU", "Classic"),
    ("Razorgore", "EU", "Classic"),
    ("Judgement", "EU", "Classic"),
    ("Zandalar Tribe", "EU", "Classic"),
]


def _mk_aliases() -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for name, region, version in _SERVERS:
        rows.append((f"{name} [{region} - {version}] - Alliance", name, region, version, "g2g"))
        rows.append((f"{name} [{region} - {version}] - Horde", name, region, version, "g2g"))

    # Explicit Horde aliases requested (safe duplicate with ON CONFLICT DO NOTHING).
    rows.extend(
        [
            ("Stitches [EU - Classic] - Horde", "Stitches", "EU", "Classic", "g2g"),
            ("Soulseeker [EU - Hardcore] - Horde", "Soulseeker", "EU", "Hardcore", "g2g"),
            ("Old Blanchy [US - Classic] - Horde", "Old Blanchy", "US", "Classic", "g2g"),
            ("Deviate Delight [US - Classic] - Horde", "Deviate Delight", "US", "Classic", "g2g"),
            ("Hydraxian Waterlords [EU - Classic] - Horde", "Hydraxian Waterlords", "EU", "Classic", "g2g"),
            ("Shadowstrike [AU - Season of Discovery] - Horde", "Shadowstrike", "AU", "Season of Discovery", "g2g"),
            ("Penance [AU - Season of Discovery] - Horde", "Penance", "AU", "Season of Discovery", "g2g"),
        ]
    )
    return rows


_ALIASES = _mk_aliases()


def upgrade() -> None:
    for name, region, version in _SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version)
            VALUES ({_q(name)}, {_q(region)}, {_q(version)})
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    for alias, s_name, s_region, s_version, source in _ALIASES:
        op.execute(f"""
            INSERT INTO server_aliases (server_id, alias, source)
            SELECT s.id, {_q(alias)}, {_q(source)}
            FROM servers s
            WHERE s.name = {_q(s_name)}
              AND s.region = {_q(s_region)}
              AND s.version = {_q(s_version)}
            ON CONFLICT (alias) DO NOTHING;
        """)


def downgrade() -> None:
    for alias, _, _, _, _ in _ALIASES:
        op.execute(f"DELETE FROM server_aliases WHERE alias = {_q(alias)};")
    for name, region, version in _SERVERS:
        op.execute(f"""
            DELETE FROM servers
            WHERE name = {_q(name)} AND region = {_q(region)} AND version = {_q(version)};
        """)


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


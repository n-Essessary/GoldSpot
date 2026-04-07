"""Add missing G2G servers/aliases batch 2 from production unresolved logs.

Revision ID: 007
Revises: 006
Create Date: 2026-04-07
"""
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


_SERVERS = [
    # US Classic
    ("Thunderfury", "US", "Classic"),
    ("Smolderweb", "US", "Classic"),
    ("Bigglesworth", "US", "Classic"),
    ("Blaumeux", "US", "Classic"),
    ("Kurinnaxx", "US", "Classic"),
    ("Rattlegore", "US", "Classic"),
    ("Fairbanks", "US", "Classic"),
    ("Anathema", "US", "Classic"),
    ("Arcanite Reaper", "US", "Classic"),
    ("Whitemane", "US", "Classic"),
    ("Mankrik", "US", "Classic"),
    ("Ashkandi", "US", "Classic"),
    ("Westfall", "US", "Classic"),
    ("Pagle", "US", "Classic"),
    ("Windseeker", "US", "Classic"),
    ("Defias Pillager", "US", "Classic"),
    ("Old Blanchy", "US", "Classic"),
    ("Skull Rock", "US", "Classic"),
    ("Deviate Delight", "US", "Classic"),
    ("Doomhowl", "US", "Hardcore"),
    # EU Classic
    ("Noggenfogger", "EU", "Classic"),
    ("Bloodfang", "EU", "Classic"),
    ("Dragonfang", "EU", "Classic"),
    ("Mograine", "EU", "Classic"),
    ("Earthshaker", "EU", "Classic"),
    ("Skullflame", "EU", "Classic"),
    ("Golemagg", "EU", "Classic"),
    ("Gandling", "EU", "Classic"),
    ("Ashbringer", "EU", "Classic"),
    ("Firemaw", "EU", "Classic"),
    ("Nethergarde Keep", "EU", "Classic"),
    ("Pyrewood Village", "EU", "Classic"),
    ("Mirage Raceway", "EU", "Classic"),
    ("Zandalar Tribe", "EU", "Classic"),
    ("Stitches", "EU", "Classic"),
    ("Nek'Rosh", "EU", "Classic"),
    ("Hydraxian Waterlords", "EU", "Classic"),
    # EU / AU special versions
    ("Thunderstrike", "EU", "Anniversary"),
    ("Soulseeker", "EU", "Hardcore"),
    ("Maladath", "AU", "Anniversary"),
    ("Penance", "AU", "Season of Discovery"),
    ("Shadowstrike", "AU", "Season of Discovery"),
]


def _mk_aliases() -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for name, region, version in _SERVERS:
        rows.append((f"{name} [{region} - {version}] - Alliance", name, region, version, "g2g"))
        rows.append((f"{name} [{region} - {version}] - Horde", name, region, version, "g2g"))
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


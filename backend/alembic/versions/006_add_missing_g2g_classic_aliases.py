"""Add missing G2G Classic aliases (EU/RU) from production unresolved logs.

Revision ID: 006
Revises: 005
Create Date: 2026-04-07
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


_SERVERS = [
    ("Flamelash", "EU", "Classic"),
    ("Stonespine", "EU", "Classic"),
    ("Ten Storms", "EU", "Classic"),
    ("Razorgore", "EU", "Classic"),
    ("Judgement", "EU", "Classic"),
    ("Flamegor", "RU", "Classic"),
    ("Harbinger of Doom", "RU", "Classic"),
]

_ALIASES: list[tuple[str, str, str, str, str]] = [
    ("Flamelash [EU - Classic] - Alliance", "Flamelash", "EU", "Classic", "g2g"),
    ("Stonespine [EU - Classic] - Alliance", "Stonespine", "EU", "Classic", "g2g"),
    ("Ten Storms [EU - Classic] - Horde", "Ten Storms", "EU", "Classic", "g2g"),
    ("Razorgore [EU - Classic] - Alliance", "Razorgore", "EU", "Classic", "g2g"),
    ("Judgement [EU - Classic] - Alliance", "Judgement", "EU", "Classic", "g2g"),
    ("Judgement [EU - Classic] - Horde", "Judgement", "EU", "Classic", "g2g"),
    ("Flamegor [RU - Classic] - Alliance", "Flamegor", "RU", "Classic", "g2g"),
    ("Flamegor [RU - Classic] - Horde", "Flamegor", "RU", "Classic", "g2g"),
    ("Harbinger of Doom [RU - Classic] - Horde", "Harbinger of Doom", "RU", "Classic", "g2g"),
]


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


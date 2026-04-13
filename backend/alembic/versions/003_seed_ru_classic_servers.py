"""Add RU Classic servers missing from G2G: Flamegor and Harbinger of Doom.

G2G titles like "Flamegor [RU - Classic] - Horde" are parsed by g2g_parser into
(region="RU", version="Classic").  These servers were absent from the canonical
servers table, causing server_resolver to emit WARNING: unresolved server.

Adds:
  - servers rows: (Flamegor, RU, Classic) and (Harbinger of Doom, RU, Classic)
  - g2g aliases for all observed faction variants

Revision ID: 003b
Revises: 003
Create Date: 2026-04-07
"""
from alembic import op

revision = "003b"
down_revision = "003"
branch_labels = None
depends_on = None


_SERVERS = [
    ("Flamegor",          "RU", "Classic"),
    ("Harbinger of Doom", "RU", "Classic"),
]

# All faction variants that appear in G2G offer titles.
# "Flamegor [RU - Classic] - Alliance" was not seen yet but added for completeness.
_ALIASES: list[tuple[str, str, str, str, str]] = [
    ("Flamegor [RU - Classic] - Horde",            "Flamegor",          "RU", "Classic", "g2g"),
    ("Flamegor [RU - Classic] - Alliance",          "Flamegor",          "RU", "Classic", "g2g"),
    ("Harbinger of Doom [RU - Classic] - Horde",    "Harbinger of Doom", "RU", "Classic", "g2g"),
    ("Harbinger of Doom [RU - Classic] - Alliance", "Harbinger of Doom", "RU", "Classic", "g2g"),
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
            WHERE s.name    = {_q(s_name)}
              AND s.region  = {_q(s_region)}
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
    """Minimal SQL string quoting (single-quote escape)."""
    return "'" + s.replace("'", "''") + "'"

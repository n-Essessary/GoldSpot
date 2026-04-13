"""Add US Classic servers missing from G2G: Anathema and Fairbanks.

G2G labels these as "Anathema [US - Classic] - Horde" — note "Classic" without "Era".
server_resolver._normalise_version("Classic") returns "Classic" (not "Classic Era"),
so _lookup_server("Anathema", "US", "Classic", pool) finds no row and logs a WARNING.

These are distinct from the "Classic Era" realms already seeded in 002 — G2G uses a
different version label for them on their platform.

Adds:
  - servers rows: (Anathema, US, Classic) and (Fairbanks, US, Classic)
  - g2g aliases for all faction variants (both factions for each, pre-emptively)

Revision ID: 004
Revises: 003b
Create Date: 2026-04-07
"""
from alembic import op

revision = "004"
down_revision = "003b"
branch_labels = None
depends_on = None


_SERVERS = [
    ("Anathema", "US", "Classic"),
    ("Fairbanks", "US", "Classic"),
]

# Horde confirmed from logs; Alliance alias added pre-emptively for both.
_ALIASES: list[tuple[str, str, str, str, str]] = [
    ("Anathema [US - Classic] - Horde",    "Anathema", "US", "Classic", "g2g"),
    ("Anathema [US - Classic] - Alliance", "Anathema", "US", "Classic", "g2g"),
    ("Fairbanks [US - Classic] - Horde",   "Fairbanks", "US", "Classic", "g2g"),
    ("Fairbanks [US - Classic] - Alliance","Fairbanks", "US", "Classic", "g2g"),
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

"""Trim MoP Classic aliases to non-colliding servers only (11 realms, 22 aliases).

Removes alias rows where the same alias text exists on a non-MoP server, then
re-seeds G2G aliases only for MoP-unique realm names (resolver handles collisions).

Revision ID: 015
Revises: 014
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

# MoP-only servers (no name+region overlap with other game versions) — both factions.
_UNIQUE_MOP_ALIASES: list[tuple[str, str, str]] = [
    ("Garalon", "EU", "Garalon [EU] - Alliance"),
    ("Garalon", "EU", "Garalon [EU] - Horde"),
    ("Hoptallus", "EU", "Hoptallus [EU] - Alliance"),
    ("Hoptallus", "EU", "Hoptallus [EU] - Horde"),
    ("Norushen", "EU", "Norushen [EU] - Alliance"),
    ("Norushen", "EU", "Norushen [EU] - Horde"),
    ("Ook Ook", "EU", "Ook Ook [EU] - Alliance"),
    ("Ook Ook", "EU", "Ook Ook [EU] - Horde"),
    ("Shek'zeer", "EU", "Shek'zeer [EU] - Alliance"),
    ("Shek'zeer", "EU", "Shek'zeer [EU] - Horde"),
    ("Eranikus", "US", "Eranikus [US] - Alliance"),
    ("Eranikus", "US", "Eranikus [US] - Horde"),
    ("Galakras", "US", "Galakras [US] - Alliance"),
    ("Galakras", "US", "Galakras [US] - Horde"),
    ("Immerseus", "US", "Immerseus [US] - Alliance"),
    ("Immerseus", "US", "Immerseus [US] - Horde"),
    ("Lei Shen", "US", "Lei Shen [US] - Alliance"),
    ("Lei Shen", "US", "Lei Shen [US] - Horde"),
    ("Nazgrim", "US", "Nazgrim [US] - Alliance"),
    ("Nazgrim", "US", "Nazgrim [US] - Horde"),
    ("Ra-den", "US", "Ra-den [US] - Alliance"),
    ("Ra-den", "US", "Ra-den [US] - Horde"),
]

_DELETE_COLLISION_ALIASES = """
DELETE FROM server_aliases
WHERE server_id IN (
    SELECT id FROM servers WHERE version = 'MoP Classic'
)
AND alias IN (
    SELECT alias FROM server_aliases
    WHERE server_id IN (
        SELECT id FROM servers WHERE version != 'MoP Classic'
    )
)
"""

_INSERT_ALIAS_SQL = """
INSERT INTO server_aliases (server_id, alias, source)
SELECT s.id, :alias_text, 'g2g'
FROM servers s
WHERE s.name = :srv_name AND s.region = :srv_region
  AND s.version = 'MoP Classic'
ON CONFLICT (alias) DO NOTHING
"""

_DELETE_ALL_MOP_ALIASES = """
DELETE FROM server_aliases
WHERE server_id IN (
    SELECT id FROM servers WHERE version = 'MoP Classic'
)
"""


def upgrade() -> None:
    op.execute(sa.text(_DELETE_COLLISION_ALIASES))

    conn = op.get_bind()
    params = [
        {"srv_name": name, "srv_region": region, "alias_text": alias}
        for name, region, alias in _UNIQUE_MOP_ALIASES
    ]
    conn.execute(sa.text(_INSERT_ALIAS_SQL), params)


def downgrade() -> None:
    op.execute(sa.text(_DELETE_ALL_MOP_ALIASES))

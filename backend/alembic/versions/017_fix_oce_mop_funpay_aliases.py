"""Add FunPay aliases for OCE MoP realms when parser stamps (US) MoP Classic.

Chip 147 uses region US in ``ChipConfig``, so ``_fetch_chip`` stamps
``(US) MoP Classic - {name}``. Migration 016 added ``(OCE) MoP Classic - {name}``
for Arugal, Remulos, Yojamba — alias lookup failed. These rows map the US-stamped
text to the canonical OCE ``servers`` rows.

Revision ID: 017
Revises: 016
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

_INSERT_FUNPAY_ALIAS_SQL = """
INSERT INTO server_aliases (server_id, alias, source)
SELECT s.id, :alias_text, 'funpay'
FROM servers s
WHERE s.name = :srv_name AND s.region = :srv_region
  AND s.version = 'MoP Classic'
ON CONFLICT (alias) DO NOTHING
"""

# Parser: (US) MoP Classic - {name} → servers(name, region=OCE, version=MoP Classic)
_OCE_MOP_FUNP_ALIASES: list[tuple[str, str]] = [
    ("Arugal", "(US) MoP Classic - Arugal"),
    ("Remulos", "(US) MoP Classic - Remulos"),
    ("Yojamba", "(US) MoP Classic - Yojamba"),
]

_DELETE_BY_ALIAS_SQL = """
DELETE FROM server_aliases
WHERE alias = :alias_text
"""


def upgrade() -> None:
    conn = op.get_bind()
    params = [
        {
            "srv_name": name,
            "srv_region": "OCE",
            "alias_text": alias_text,
        }
        for name, alias_text in _OCE_MOP_FUNP_ALIASES
    ]
    conn.execute(sa.text(_INSERT_FUNPAY_ALIAS_SQL), params)


def downgrade() -> None:
    conn = op.get_bind()
    for _, alias_text in _OCE_MOP_FUNP_ALIASES:
        conn.execute(sa.text(_DELETE_BY_ALIAS_SQL), {"alias_text": alias_text})

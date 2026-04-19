"""Point G2G server_aliases at MoP Classic rows for 47 name/region duplicates.

G2G aliases were created against Classic ``server_id``; G2G now lists MoP Classic
gold on the same realms. Re-target ``source='g2g'`` rows only (FunPay unchanged).

Revision ID: 018
Revises: 017
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

# classic server_id → MoP Classic server_id (47 pairs)
_G2G_CLASSIC_TO_MOP: list[tuple[int, int]] = [
    # EU
    (133, 260),
    (42, 235),
    (129, 261),
    (41, 236),
    (125, 253),
    (34, 237),
    (134, 239),
    (43, 240),
    (135, 241),
    (146, 243),
    (45, 244),
    (126, 254),
    (136, 259),
    (121, 245),
    (37, 246),
    (122, 247),
    (52, 255),
    (123, 250),
    (137, 256),
    (138, 262),
    (46, 252),
    (54, 257),
    (139, 258),
    # OCE
    (203, 288),
    (201, 289),
    (204, 290),
    # RU
    (230, 291),
    (167, 292),
    # US
    (59, 263),
    (172, 264),
    (173, 265),
    (191, 266),
    (174, 267),
    (197, 268),
    (187, 269),
    (175, 271),
    (182, 273),
    (72, 276),
    (176, 277),
    (186, 278),
    (177, 280),
    (178, 281),
    (65, 283),
    (195, 284),
    (179, 285),
    (66, 286),
    (180, 287),
]

_UPGRADE_SQL = """
UPDATE server_aliases
SET server_id = :mop_id
WHERE server_id = :classic_id AND source = 'g2g'
"""

_DOWNGRADE_SQL = """
UPDATE server_aliases
SET server_id = :classic_id
WHERE server_id = :mop_id AND source = 'g2g'
"""


def upgrade() -> None:
    conn = op.get_bind()
    params = [
        {"classic_id": classic_id, "mop_id": mop_id}
        for classic_id, mop_id in _G2G_CLASSIC_TO_MOP
    ]
    conn.execute(sa.text(_UPGRADE_SQL), params)


def downgrade() -> None:
    conn = op.get_bind()
    # Safety: remove versioned MoP aliases (inserted by 019) if they still
    # exist — guards against downgrading 018 without first downgrading 019.
    conn.execute(
        sa.text(
            "DELETE FROM server_aliases"
            " WHERE alias LIKE '% - MoP Classic] - %'"
            " AND source = 'g2g'"
        )
    )
    params = [
        {"classic_id": classic_id, "mop_id": mop_id}
        for classic_id, mop_id in _G2G_CLASSIC_TO_MOP
    ]
    conn.execute(sa.text(_DOWNGRADE_SQL), params)

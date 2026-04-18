"""FunPay aliases for MoP Classic realm-level offers (plain server name → canonical row).

FunPay titles use ``(REGION) MoP Classic - {ServerName}`` when ``server_name`` is set
(see ``_build_alias_key``). One alias per realm; group-level offers without a realm
name are not aliased here.

Revision ID: 016
Revises: 015
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None

# chip 146 — EU (28 realms)
_EU_SERVERS: list[str] = [
    "Amnennar",
    "Ashbringer",
    "Auberdine",
    "Earthshaker",
    "Everlook",
    "Firemaw",
    "Garalon",
    "Gehennas",
    "Giantstalker",
    "Golemagg",
    "Hoptallus",
    "Hydraxian Waterlords",
    "Jin'do",
    "Lakeshire",
    "Mandokir",
    "Mirage Raceway",
    "Mograine",
    "Nethergarde Keep",
    "Norushen",
    "Ook Ook",
    "Patchwerk",
    "Pyrewood Village",
    "Razorfen",
    "Shek'zeer",
    "Sulfuron",
    "Thekal",
    "Transcendence",
    "Venoxis",
]

# chip 147 — US (25 realms; OCE listed separately)
_US_SERVERS: list[str] = [
    "Angerforge",
    "Ashkandi",
    "Atiesh",
    "Azuresong",
    "Benediction",
    "Bloodsail Buccaneers",
    "Earthfury",
    "Eranikus",
    "Faerlina",
    "Galakras",
    "Grobbulus",
    "Immerseus",
    "Lei Shen",
    "Maladath",
    "Mankrik",
    "Myzrael",
    "Nazgrim",
    "Old Blanchy",
    "Pagle",
    "Ra-den",
    "Skyfury",
    "Sulfuras",
    "Westfall",
    "Whitemane",
    "Windseeker",
]

# chip 147 — OCE (same chip id as US; region distinguishes aliases)
_OCE_SERVERS: list[str] = [
    "Arugal",
    "Remulos",
    "Yojamba",
]

_INSERT_FUNPAY_ALIAS_SQL = """
INSERT INTO server_aliases (server_id, alias, source)
SELECT s.id, :alias_text, 'funpay'
FROM servers s
WHERE s.name = :srv_name AND s.region = :srv_region
  AND s.version = 'MoP Classic'
ON CONFLICT (alias) DO NOTHING
"""

_DELETE_FUNPAY_MOP_ALIASES = """
DELETE FROM server_aliases
WHERE source = 'funpay'
  AND server_id IN (
      SELECT id FROM servers WHERE version = 'MoP Classic'
  )
"""


def upgrade() -> None:
    conn = op.get_bind()
    params: list[dict[str, str]] = []
    for name in _EU_SERVERS:
        params.append(
            {
                "srv_name": name,
                "srv_region": "EU",
                "alias_text": f"(EU) MoP Classic - {name}",
            }
        )
    for name in _US_SERVERS:
        params.append(
            {
                "srv_name": name,
                "srv_region": "US",
                "alias_text": f"(US) MoP Classic - {name}",
            }
        )
    for name in _OCE_SERVERS:
        params.append(
            {
                "srv_name": name,
                "srv_region": "OCE",
                "alias_text": f"(OCE) MoP Classic - {name}",
            }
        )
    conn.execute(sa.text(_INSERT_FUNPAY_ALIAS_SQL), params)


def downgrade() -> None:
    op.execute(sa.text(_DELETE_FUNPAY_MOP_ALIASES))

"""Insert server_aliases for all 514 WoW Retail servers (G2G + FunPay).

G2G alias format : "{name} [{bracket} - Retail] - {faction}"
  bracket = notes.replace("-localised", "") when notes ends with "-localised"
  bracket = canonical region ("EU", "US", "OCE", "RU") otherwise

FunPay alias format:
  EU + RU -> "(EU) Retail - {name}"     (FunPay chip/2)
  US      -> "(US) Retail - {name}"     (FunPay chip/25)
  OCE     -> "(US) Retail - {name}"     (FunPay chip/25) -- only 3 servers:
            Aman'Thul, Barthilas, Frostmourne
  9 remaining OCE servers have no FunPay presence -> no alias

ID assignment matches migration 020 insert order:
  EU  ids 293-540  (248 servers, alphabetical by name)
  US  ids 541-774  (234 servers, alphabetical by name)
  OCE ids 775-786  (12  servers, alphabetical by name)
  RU  ids 787-806  (20  servers, alphabetical by name)

Revision ID: 021
Revises: 020
Create Date: 2026-04-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from db.canonical_servers import CANONICAL_SERVERS

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None

# OCE servers that appear on FunPay chip/25
_FUNPAY_OCE = frozenset({"Aman'Thul", "Barthilas", "Frostmourne"})


def _build_aliases() -> list[dict]:
    retail = [s for s in CANONICAL_SERVERS if s.version == "Retail"]

    eu = sorted([s for s in retail if s.region == "EU"], key=lambda x: x.name)
    us = sorted([s for s in retail if s.region == "US"], key=lambda x: x.name)
    oce = sorted([s for s in retail if s.region == "OCE"], key=lambda x: x.name)
    ru = sorted([s for s in retail if s.region == "RU"], key=lambda x: x.name)

    # ID assignment mirrors migration 020 insert order.
    id_map: dict[tuple[str, str], int] = {}
    for i, s in enumerate(eu):
        id_map[(s.name, "EU")] = 293 + i
    for i, s in enumerate(us):
        id_map[(s.name, "US")] = 541 + i
    for i, s in enumerate(oce):
        id_map[(s.name, "OCE")] = 775 + i
    for i, s in enumerate(ru):
        id_map[(s.name, "RU")] = 787 + i

    rows: list[dict] = []
    for s in retail:
        sid = id_map[(s.name, s.region)]

        # G2G bracket: strip "-localised" suffix; fall back to region.
        bracket = (
            s.notes.replace("-localised", "")
            if s.notes.endswith("-localised")
            else s.region
        )

        # G2G: one alias per faction.
        rows.append(
            {
                "server_id": sid,
                "alias": f"{s.name} [{bracket} - Retail] - Alliance",
                "source": "g2g",
            }
        )
        rows.append(
            {
                "server_id": sid,
                "alias": f"{s.name} [{bracket} - Retail] - Horde",
                "source": "g2g",
            }
        )

        # FunPay: one alias per server (no faction).
        if s.region in ("EU", "RU"):
            rows.append(
                {
                    "server_id": sid,
                    "alias": f"(EU) Retail - {s.name}",
                    "source": "funpay",
                }
            )
        elif s.region == "US":
            rows.append(
                {
                    "server_id": sid,
                    "alias": f"(US) Retail - {s.name}",
                    "source": "funpay",
                }
            )
        elif s.region == "OCE" and s.name in _FUNPAY_OCE:
            rows.append(
                {
                    "server_id": sid,
                    "alias": f"(US) Retail - {s.name}",
                    "source": "funpay",
                }
            )
        # else: OCE server not on FunPay -> no alias.

    return rows


_ALIASES: list[dict] = _build_aliases()

_INSERT_SQL = """
INSERT INTO server_aliases (server_id, alias, source)
VALUES (:server_id, :alias, :source)
ON CONFLICT (alias) DO NOTHING
"""


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(_INSERT_SQL), _ALIASES)


def downgrade() -> None:
    conn = op.get_bind()
    # Remove G2G Retail aliases.
    conn.execute(
        sa.text(
            "DELETE FROM server_aliases"
            " WHERE alias LIKE '% - Retail] - %'"
            " AND source = 'g2g'"
        )
    )
    # Remove FunPay EU-chip Retail aliases.
    conn.execute(
        sa.text(
            "DELETE FROM server_aliases"
            " WHERE alias LIKE '(EU) Retail - %'"
            " AND source = 'funpay'"
        )
    )
    # Remove FunPay US-chip Retail aliases.
    conn.execute(
        sa.text(
            "DELETE FROM server_aliases"
            " WHERE alias LIKE '(US) Retail - %'"
            " AND source = 'funpay'"
        )
    )

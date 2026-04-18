"""Insert MoP Classic servers and G2G aliases (INSERT only; schema unchanged).

Revision ID: 014
Revises: 013
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

# ── 58 MoP Classic servers (name, region) ────────────────────────────────────

_MOP_SERVERS: list[tuple[str, str]] = [
    # EU
    ("Ashbringer", "EU"),
    ("Earthshaker", "EU"),
    ("Firemaw", "EU"),
    ("Garalon", "EU"),
    ("Gehennas", "EU"),
    ("Giantstalker", "EU"),
    ("Golemagg", "EU"),
    ("Hoptallus", "EU"),
    ("Hydraxian Waterlords", "EU"),
    ("Jin'do", "EU"),
    ("Mirage Raceway", "EU"),
    ("Mograine", "EU"),
    ("Nethergarde Keep", "EU"),
    ("Norushen", "EU"),
    ("Ook Ook", "EU"),
    ("Pyrewood Village", "EU"),
    ("Shek'zeer", "EU"),
    ("Thekal", "EU"),
    ("Everlook", "EU"),
    ("Lakeshire", "EU"),
    ("Patchwerk", "EU"),
    ("Razorfen", "EU"),
    ("Transcendence", "EU"),
    ("Venoxis", "EU"),
    ("Mandokir", "EU"),
    ("Amnennar", "EU"),
    ("Auberdine", "EU"),
    ("Sulfuron", "EU"),
    # US
    ("Angerforge", "US"),
    ("Ashkandi", "US"),
    ("Atiesh", "US"),
    ("Azuresong", "US"),
    ("Benediction", "US"),
    ("Bloodsail Buccaneers", "US"),
    ("Earthfury", "US"),
    ("Eranikus", "US"),
    ("Faerlina", "US"),
    ("Galakras", "US"),
    ("Grobbulus", "US"),
    ("Immerseus", "US"),
    ("Lei Shen", "US"),
    ("Maladath", "US"),
    ("Mankrik", "US"),
    ("Myzrael", "US"),
    ("Nazgrim", "US"),
    ("Old Blanchy", "US"),
    ("Pagle", "US"),
    ("Ra-den", "US"),
    ("Skyfury", "US"),
    ("Sulfuras", "US"),
    ("Westfall", "US"),
    ("Whitemane", "US"),
    ("Windseeker", "US"),
    # OCE
    ("Arugal", "OCE"),
    ("Remulos", "OCE"),
    ("Yojamba", "OCE"),
    # RU
    ("Chromie", "RU"),
    ("Flamegor", "RU"),
]

_INSERT_SERVER_SQL = """
INSERT INTO servers (name, region, version, is_active, realm_type)
VALUES (:name, :region, 'MoP Classic', true, 'Normal')
ON CONFLICT (name, region, version) DO NOTHING
"""

# (server_name, server_region, alias_text)
_MOP_ALIASES: list[tuple[str, str, str]] = [
    # Standard EU
    ("Ashbringer", "EU", "Ashbringer [EU] - Alliance"),
    ("Ashbringer", "EU", "Ashbringer [EU] - Horde"),
    ("Earthshaker", "EU", "Earthshaker [EU] - Alliance"),
    ("Earthshaker", "EU", "Earthshaker [EU] - Horde"),
    ("Firemaw", "EU", "Firemaw [EU] - Alliance"),
    ("Firemaw", "EU", "Firemaw [EU] - Horde"),
    ("Garalon", "EU", "Garalon [EU] - Alliance"),
    ("Garalon", "EU", "Garalon [EU] - Horde"),
    ("Gehennas", "EU", "Gehennas [EU] - Alliance"),
    ("Gehennas", "EU", "Gehennas [EU] - Horde"),
    ("Giantstalker", "EU", "Giantstalker [EU] - Alliance"),
    ("Giantstalker", "EU", "Giantstalker [EU] - Horde"),
    ("Golemagg", "EU", "Golemagg [EU] - Alliance"),
    ("Golemagg", "EU", "Golemagg [EU] - Horde"),
    ("Hoptallus", "EU", "Hoptallus [EU] - Alliance"),
    ("Hoptallus", "EU", "Hoptallus [EU] - Horde"),
    ("Hydraxian Waterlords", "EU", "Hydraxian Waterlords [EU] - Alliance"),
    ("Hydraxian Waterlords", "EU", "Hydraxian Waterlords [EU] - Horde"),
    ("Jin'do", "EU", "Jin'do [EU] - Alliance"),
    ("Jin'do", "EU", "Jin'do [EU] - Horde"),
    ("Mirage Raceway", "EU", "Mirage Raceway [EU] - Alliance"),
    ("Mirage Raceway", "EU", "Mirage Raceway [EU] - Horde"),
    ("Mograine", "EU", "Mograine [EU] - Alliance"),
    ("Mograine", "EU", "Mograine [EU] - Horde"),
    ("Nethergarde Keep", "EU", "Nethergarde Keep [EU] - Alliance"),
    ("Nethergarde Keep", "EU", "Nethergarde Keep [EU] - Horde"),
    ("Norushen", "EU", "Norushen [EU] - Alliance"),
    ("Norushen", "EU", "Norushen [EU] - Horde"),
    ("Ook Ook", "EU", "Ook Ook [EU] - Alliance"),
    ("Ook Ook", "EU", "Ook Ook [EU] - Horde"),
    ("Pyrewood Village", "EU", "Pyrewood Village [EU] - Alliance"),
    ("Pyrewood Village", "EU", "Pyrewood Village [EU] - Horde"),
    ("Shek'zeer", "EU", "Shek'zeer [EU] - Alliance"),
    ("Shek'zeer", "EU", "Shek'zeer [EU] - Horde"),
    ("Thekal", "EU", "Thekal [EU] - Alliance"),
    ("Thekal", "EU", "Thekal [EU] - Horde"),
    # DE + EU
    ("Everlook", "EU", "Everlook [DE] - Alliance"),
    ("Everlook", "EU", "Everlook [DE] - Horde"),
    ("Everlook", "EU", "Everlook [EU] - Alliance"),
    ("Everlook", "EU", "Everlook [EU] - Horde"),
    ("Lakeshire", "EU", "Lakeshire [DE] - Alliance"),
    ("Lakeshire", "EU", "Lakeshire [DE] - Horde"),
    ("Lakeshire", "EU", "Lakeshire [EU] - Alliance"),
    ("Lakeshire", "EU", "Lakeshire [EU] - Horde"),
    ("Patchwerk", "EU", "Patchwerk [DE] - Alliance"),
    ("Patchwerk", "EU", "Patchwerk [DE] - Horde"),
    ("Patchwerk", "EU", "Patchwerk [EU] - Alliance"),
    ("Patchwerk", "EU", "Patchwerk [EU] - Horde"),
    ("Razorfen", "EU", "Razorfen [DE] - Alliance"),
    ("Razorfen", "EU", "Razorfen [DE] - Horde"),
    ("Razorfen", "EU", "Razorfen [EU] - Alliance"),
    ("Razorfen", "EU", "Razorfen [EU] - Horde"),
    ("Transcendence", "EU", "Transcendence [DE] - Alliance"),
    ("Transcendence", "EU", "Transcendence [DE] - Horde"),
    ("Transcendence", "EU", "Transcendence [EU] - Alliance"),
    ("Transcendence", "EU", "Transcendence [EU] - Horde"),
    ("Venoxis", "EU", "Venoxis [DE] - Alliance"),
    ("Venoxis", "EU", "Venoxis [DE] - Horde"),
    ("Venoxis", "EU", "Venoxis [EU] - Alliance"),
    ("Venoxis", "EU", "Venoxis [EU] - Horde"),
    # ES/FR + EU
    ("Mandokir", "EU", "Mandokir [ES] - Alliance"),
    ("Mandokir", "EU", "Mandokir [ES] - Horde"),
    ("Mandokir", "EU", "Mandokir [EU] - Alliance"),
    ("Mandokir", "EU", "Mandokir [EU] - Horde"),
    ("Amnennar", "EU", "Amnennar [FR] - Alliance"),
    ("Amnennar", "EU", "Amnennar [FR] - Horde"),
    ("Amnennar", "EU", "Amnennar [EU] - Alliance"),
    ("Amnennar", "EU", "Amnennar [EU] - Horde"),
    ("Auberdine", "EU", "Auberdine [FR] - Alliance"),
    ("Auberdine", "EU", "Auberdine [FR] - Horde"),
    ("Auberdine", "EU", "Auberdine [EU] - Alliance"),
    ("Auberdine", "EU", "Auberdine [EU] - Horde"),
    ("Sulfuron", "EU", "Sulfuron [FR] - Alliance"),
    ("Sulfuron", "EU", "Sulfuron [FR] - Horde"),
    ("Sulfuron", "EU", "Sulfuron [EU] - Alliance"),
    ("Sulfuron", "EU", "Sulfuron [EU] - Horde"),
    # US
    ("Angerforge", "US", "Angerforge [US] - Alliance"),
    ("Angerforge", "US", "Angerforge [US] - Horde"),
    ("Ashkandi", "US", "Ashkandi [US] - Alliance"),
    ("Ashkandi", "US", "Ashkandi [US] - Horde"),
    ("Atiesh", "US", "Atiesh [US] - Alliance"),
    ("Atiesh", "US", "Atiesh [US] - Horde"),
    ("Azuresong", "US", "Azuresong [US] - Alliance"),
    ("Azuresong", "US", "Azuresong [US] - Horde"),
    ("Benediction", "US", "Benediction [US] - Alliance"),
    ("Benediction", "US", "Benediction [US] - Horde"),
    ("Bloodsail Buccaneers", "US", "Bloodsail Buccaneers [US] - Alliance"),
    ("Bloodsail Buccaneers", "US", "Bloodsail Buccaneers [US] - Horde"),
    ("Earthfury", "US", "Earthfury [US] - Alliance"),
    ("Earthfury", "US", "Earthfury [US] - Horde"),
    ("Eranikus", "US", "Eranikus [US] - Alliance"),
    ("Eranikus", "US", "Eranikus [US] - Horde"),
    ("Faerlina", "US", "Faerlina [US] - Alliance"),
    ("Faerlina", "US", "Faerlina [US] - Horde"),
    ("Galakras", "US", "Galakras [US] - Alliance"),
    ("Galakras", "US", "Galakras [US] - Horde"),
    ("Grobbulus", "US", "Grobbulus [US] - Alliance"),
    ("Grobbulus", "US", "Grobbulus [US] - Horde"),
    ("Immerseus", "US", "Immerseus [US] - Alliance"),
    ("Immerseus", "US", "Immerseus [US] - Horde"),
    ("Lei Shen", "US", "Lei Shen [US] - Alliance"),
    ("Lei Shen", "US", "Lei Shen [US] - Horde"),
    ("Maladath", "US", "Maladath [US] - Alliance"),
    ("Maladath", "US", "Maladath [US] - Horde"),
    ("Mankrik", "US", "Mankrik [US] - Alliance"),
    ("Mankrik", "US", "Mankrik [US] - Horde"),
    ("Myzrael", "US", "Myzrael [US] - Alliance"),
    ("Myzrael", "US", "Myzrael [US] - Horde"),
    ("Nazgrim", "US", "Nazgrim [US] - Alliance"),
    ("Nazgrim", "US", "Nazgrim [US] - Horde"),
    ("Old Blanchy", "US", "Old Blanchy [US] - Alliance"),
    ("Old Blanchy", "US", "Old Blanchy [US] - Horde"),
    ("Pagle", "US", "Pagle [US] - Alliance"),
    ("Pagle", "US", "Pagle [US] - Horde"),
    ("Ra-den", "US", "Ra-den [US] - Alliance"),
    ("Ra-den", "US", "Ra-den [US] - Horde"),
    ("Skyfury", "US", "Skyfury [US] - Alliance"),
    ("Skyfury", "US", "Skyfury [US] - Horde"),
    ("Sulfuras", "US", "Sulfuras [US] - Alliance"),
    ("Sulfuras", "US", "Sulfuras [US] - Horde"),
    ("Westfall", "US", "Westfall [US] - Alliance"),
    ("Westfall", "US", "Westfall [US] - Horde"),
    ("Whitemane", "US", "Whitemane [US] - Alliance"),
    ("Whitemane", "US", "Whitemane [US] - Horde"),
    ("Windseeker", "US", "Windseeker [US] - Alliance"),
    ("Windseeker", "US", "Windseeker [US] - Horde"),
    # OCE
    ("Arugal", "OCE", "Arugal [OCE] - Alliance"),
    ("Arugal", "OCE", "Arugal [OCE] - Horde"),
    ("Remulos", "OCE", "Remulos [OCE] - Alliance"),
    ("Remulos", "OCE", "Remulos [OCE] - Horde"),
    ("Yojamba", "OCE", "Yojamba [OCE] - Alliance"),
    ("Yojamba", "OCE", "Yojamba [OCE] - Horde"),
    # RU (+ plain names)
    ("Chromie", "RU", "Chromie [RU] - Alliance"),
    ("Chromie", "RU", "Chromie [RU] - Horde"),
    ("Chromie", "RU", "Chromie"),
    ("Flamegor", "RU", "Flamegor [RU] - Alliance"),
    ("Flamegor", "RU", "Flamegor [RU] - Horde"),
    ("Flamegor", "RU", "Flamegor"),
]

_INSERT_ALIAS_SQL = """
INSERT INTO server_aliases (server_id, alias, source)
SELECT s.id, :alias_text, 'g2g'
FROM servers s
WHERE s.name = :srv_name AND s.region = :srv_region AND s.version = 'MoP Classic'
ON CONFLICT (alias) DO NOTHING
"""


def upgrade() -> None:
    conn = op.get_bind()
    server_params = [{"name": n, "region": r} for n, r in _MOP_SERVERS]
    conn.execute(sa.text(_INSERT_SERVER_SQL), server_params)

    alias_params = [
        {"srv_name": sn, "srv_region": sr, "alias_text": alias}
        for sn, sr, alias in _MOP_ALIASES
    ]
    conn.execute(sa.text(_INSERT_ALIAS_SQL), alias_params)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM servers WHERE version = 'MoP Classic'"))
    # server_aliases rows for those servers removed via ON DELETE CASCADE

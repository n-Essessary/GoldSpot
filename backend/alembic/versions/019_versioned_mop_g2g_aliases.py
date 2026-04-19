"""Fix G2G alias collision between Classic Era and MoP Classic servers.

G2G returns identical title formats for both game versions, e.g.:
  Classic Era: "Atiesh [US] - Horde"  (brand_id=lgc_game_27816)
  MoP Classic: "Atiesh [US] - Horde"  (brand_id=lgc_game_29076)

The parser now injects game_version into the bracket for non-Classic-Era
configs, producing unambiguous alias keys:
  Classic Era: "Atiesh [US] - Horde"            (unchanged)
  MoP Classic: "Atiesh [US - MoP Classic] - Horde"

upgrade():
  Step A — Revert migration 018: switch G2G aliases back to Classic Era ids
            (018 had incorrectly re-targeted them at MoP ids).
  Step B — Insert versioned MoP G2G aliases for all 47 duplicate servers
            (Alliance + Horde = 94 rows) pointing at the correct MoP server_ids.

downgrade():
  Re-apply migration 018 logic (switch aliases back to mop_ids).
  Delete versioned MoP aliases.

Revision ID: 019
Revises: 018
Create Date: 2026-04-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

# ── Step A: revert 018 — mop_id → classic_id (inverse of 018's upgrade) ───────
# Format: (mop_id, classic_id)
_MOP_TO_CLASSIC: list[tuple[int, int]] = [
    # EU
    (260, 133),
    (235, 42),
    (261, 129),
    (236, 41),
    (253, 125),
    (237, 34),
    (239, 134),
    (240, 43),
    (241, 135),
    (243, 146),
    (244, 45),
    (254, 126),
    (259, 136),
    (245, 121),
    (246, 37),
    (247, 122),
    (255, 52),
    (250, 123),
    (256, 137),
    (262, 138),
    (252, 46),
    (257, 54),
    (258, 139),
    # OCE
    (288, 203),
    (289, 201),
    (290, 204),
    # RU
    (291, 230),
    (292, 167),
    # US
    (263, 59),
    (264, 172),
    (265, 173),
    (266, 191),
    (267, 174),
    (268, 197),
    (269, 187),
    (271, 175),
    (273, 182),
    (276, 72),
    (277, 176),
    (278, 186),
    (280, 177),
    (281, 178),
    (283, 65),
    (284, 195),
    (285, 179),
    (286, 66),
    (287, 180),
]

# ── Step B: versioned MoP G2G aliases ─────────────────────────────────────────
# Each entry: (server_id, alias)
# Alias format: "{name} [{subregion} - MoP Classic] - {faction}"
# 47 servers × 2 factions = 94 rows
_MOP_VERSIONED_ALIASES: list[tuple[int, str]] = [
    # ── EU ────────────────────────────────────────────────────────────────────
    # Amnennar — FR subregion — mop_id=260
    (260, "Amnennar [FR - MoP Classic] - Alliance"),
    (260, "Amnennar [FR - MoP Classic] - Horde"),
    # Ashbringer — EU subregion — mop_id=235
    (235, "Ashbringer [EU - MoP Classic] - Alliance"),
    (235, "Ashbringer [EU - MoP Classic] - Horde"),
    # Auberdine — FR subregion — mop_id=261
    (261, "Auberdine [FR - MoP Classic] - Alliance"),
    (261, "Auberdine [FR - MoP Classic] - Horde"),
    # Earthshaker — EU subregion — mop_id=236
    (236, "Earthshaker [EU - MoP Classic] - Alliance"),
    (236, "Earthshaker [EU - MoP Classic] - Horde"),
    # Everlook — DE subregion — mop_id=253
    (253, "Everlook [DE - MoP Classic] - Alliance"),
    (253, "Everlook [DE - MoP Classic] - Horde"),
    # Firemaw — EU subregion — mop_id=237
    (237, "Firemaw [EU - MoP Classic] - Alliance"),
    (237, "Firemaw [EU - MoP Classic] - Horde"),
    # Gehennas — EU subregion — mop_id=239
    (239, "Gehennas [EU - MoP Classic] - Alliance"),
    (239, "Gehennas [EU - MoP Classic] - Horde"),
    # Giantstalker — EU subregion — mop_id=240
    (240, "Giantstalker [EU - MoP Classic] - Alliance"),
    (240, "Giantstalker [EU - MoP Classic] - Horde"),
    # Golemagg — EU subregion — mop_id=241
    (241, "Golemagg [EU - MoP Classic] - Alliance"),
    (241, "Golemagg [EU - MoP Classic] - Horde"),
    # Hydraxian Waterlords — EU subregion — mop_id=243
    (243, "Hydraxian Waterlords [EU - MoP Classic] - Alliance"),
    (243, "Hydraxian Waterlords [EU - MoP Classic] - Horde"),
    # Jin'do — EU subregion — mop_id=244
    (244, "Jin'do [EU - MoP Classic] - Alliance"),
    (244, "Jin'do [EU - MoP Classic] - Horde"),
    # Lakeshire — DE subregion — mop_id=254
    (254, "Lakeshire [DE - MoP Classic] - Alliance"),
    (254, "Lakeshire [DE - MoP Classic] - Horde"),
    # Mandokir — ES subregion — mop_id=259
    (259, "Mandokir [ES - MoP Classic] - Alliance"),
    (259, "Mandokir [ES - MoP Classic] - Horde"),
    # Mirage Raceway — EU subregion — mop_id=245
    (245, "Mirage Raceway [EU - MoP Classic] - Alliance"),
    (245, "Mirage Raceway [EU - MoP Classic] - Horde"),
    # Mograine — EU subregion — mop_id=246
    (246, "Mograine [EU - MoP Classic] - Alliance"),
    (246, "Mograine [EU - MoP Classic] - Horde"),
    # Nethergarde Keep — EU subregion — mop_id=247
    (247, "Nethergarde Keep [EU - MoP Classic] - Alliance"),
    (247, "Nethergarde Keep [EU - MoP Classic] - Horde"),
    # Patchwerk — DE subregion — mop_id=255
    (255, "Patchwerk [DE - MoP Classic] - Alliance"),
    (255, "Patchwerk [DE - MoP Classic] - Horde"),
    # Pyrewood Village — EU subregion — mop_id=250
    (250, "Pyrewood Village [EU - MoP Classic] - Alliance"),
    (250, "Pyrewood Village [EU - MoP Classic] - Horde"),
    # Razorfen — DE subregion — mop_id=256
    (256, "Razorfen [DE - MoP Classic] - Alliance"),
    (256, "Razorfen [DE - MoP Classic] - Horde"),
    # Sulfuron — FR subregion — mop_id=262
    (262, "Sulfuron [FR - MoP Classic] - Alliance"),
    (262, "Sulfuron [FR - MoP Classic] - Horde"),
    # Thekal — EU subregion — mop_id=252
    (252, "Thekal [EU - MoP Classic] - Alliance"),
    (252, "Thekal [EU - MoP Classic] - Horde"),
    # Transcendence — DE subregion — mop_id=257
    (257, "Transcendence [DE - MoP Classic] - Alliance"),
    (257, "Transcendence [DE - MoP Classic] - Horde"),
    # Venoxis — DE subregion — mop_id=258
    (258, "Venoxis [DE - MoP Classic] - Alliance"),
    (258, "Venoxis [DE - MoP Classic] - Horde"),
    # ── OCE ───────────────────────────────────────────────────────────────────
    # Arugal — OCE subregion — mop_id=288
    (288, "Arugal [OCE - MoP Classic] - Alliance"),
    (288, "Arugal [OCE - MoP Classic] - Horde"),
    # Remulos — OCE subregion — mop_id=289
    (289, "Remulos [OCE - MoP Classic] - Alliance"),
    (289, "Remulos [OCE - MoP Classic] - Horde"),
    # Yojamba — OCE subregion — mop_id=290
    (290, "Yojamba [OCE - MoP Classic] - Alliance"),
    (290, "Yojamba [OCE - MoP Classic] - Horde"),
    # ── RU ────────────────────────────────────────────────────────────────────
    # Chromie — RU subregion — mop_id=291
    (291, "Chromie [RU - MoP Classic] - Alliance"),
    (291, "Chromie [RU - MoP Classic] - Horde"),
    # Flamegor — RU subregion — mop_id=292
    (292, "Flamegor [RU - MoP Classic] - Alliance"),
    (292, "Flamegor [RU - MoP Classic] - Horde"),
    # ── US ────────────────────────────────────────────────────────────────────
    # Angerforge — US subregion — mop_id=263
    (263, "Angerforge [US - MoP Classic] - Alliance"),
    (263, "Angerforge [US - MoP Classic] - Horde"),
    # Ashkandi — US subregion — mop_id=264
    (264, "Ashkandi [US - MoP Classic] - Alliance"),
    (264, "Ashkandi [US - MoP Classic] - Horde"),
    # Atiesh — US subregion — mop_id=265
    (265, "Atiesh [US - MoP Classic] - Alliance"),
    (265, "Atiesh [US - MoP Classic] - Horde"),
    # Azuresong — US subregion — mop_id=266
    (266, "Azuresong [US - MoP Classic] - Alliance"),
    (266, "Azuresong [US - MoP Classic] - Horde"),
    # Benediction — US subregion — mop_id=267
    (267, "Benediction [US - MoP Classic] - Alliance"),
    (267, "Benediction [US - MoP Classic] - Horde"),
    # Bloodsail Buccaneers — US subregion — mop_id=268
    (268, "Bloodsail Buccaneers [US - MoP Classic] - Alliance"),
    (268, "Bloodsail Buccaneers [US - MoP Classic] - Horde"),
    # Earthfury — US subregion — mop_id=269
    (269, "Earthfury [US - MoP Classic] - Alliance"),
    (269, "Earthfury [US - MoP Classic] - Horde"),
    # Faerlina — US subregion — mop_id=271
    (271, "Faerlina [US - MoP Classic] - Alliance"),
    (271, "Faerlina [US - MoP Classic] - Horde"),
    # Grobbulus — US subregion — mop_id=273
    (273, "Grobbulus [US - MoP Classic] - Alliance"),
    (273, "Grobbulus [US - MoP Classic] - Horde"),
    # Maladath — US subregion — mop_id=276
    (276, "Maladath [US - MoP Classic] - Alliance"),
    (276, "Maladath [US - MoP Classic] - Horde"),
    # Mankrik — US subregion — mop_id=277
    (277, "Mankrik [US - MoP Classic] - Alliance"),
    (277, "Mankrik [US - MoP Classic] - Horde"),
    # Myzrael — US subregion — mop_id=278
    (278, "Myzrael [US - MoP Classic] - Alliance"),
    (278, "Myzrael [US - MoP Classic] - Horde"),
    # Old Blanchy — US subregion — mop_id=280
    (280, "Old Blanchy [US - MoP Classic] - Alliance"),
    (280, "Old Blanchy [US - MoP Classic] - Horde"),
    # Pagle — US subregion — mop_id=281
    (281, "Pagle [US - MoP Classic] - Alliance"),
    (281, "Pagle [US - MoP Classic] - Horde"),
    # Skyfury — US subregion — mop_id=283
    (283, "Skyfury [US - MoP Classic] - Alliance"),
    (283, "Skyfury [US - MoP Classic] - Horde"),
    # Sulfuras — US subregion — mop_id=284
    (284, "Sulfuras [US - MoP Classic] - Alliance"),
    (284, "Sulfuras [US - MoP Classic] - Horde"),
    # Westfall — US subregion — mop_id=285
    (285, "Westfall [US - MoP Classic] - Alliance"),
    (285, "Westfall [US - MoP Classic] - Horde"),
    # Whitemane — US subregion — mop_id=286
    (286, "Whitemane [US - MoP Classic] - Alliance"),
    (286, "Whitemane [US - MoP Classic] - Horde"),
    # Windseeker — US subregion — mop_id=287
    (287, "Windseeker [US - MoP Classic] - Alliance"),
    (287, "Windseeker [US - MoP Classic] - Horde"),
]

_REVERT_018_SQL = """
UPDATE server_aliases
SET server_id = :classic_id
WHERE server_id = :mop_id AND source = 'g2g'
"""

_INSERT_ALIAS_SQL = """
INSERT INTO server_aliases (server_id, alias, source)
VALUES (:server_id, :alias, 'g2g')
ON CONFLICT (alias) DO NOTHING
"""

_REAPPLY_018_SQL = """
UPDATE server_aliases
SET server_id = :mop_id
WHERE server_id = :classic_id AND source = 'g2g'
"""

# classic_id → mop_id (same as 018's upgrade list, for downgrade re-apply)
_CLASSIC_TO_MOP: list[tuple[int, int]] = [
    (classic_id, mop_id) for mop_id, classic_id in _MOP_TO_CLASSIC
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step A: revert 018 — switch G2G aliases back to Classic Era server_ids ──
    revert_params = [
        {"mop_id": mop_id, "classic_id": classic_id}
        for mop_id, classic_id in _MOP_TO_CLASSIC
    ]
    conn.execute(sa.text(_REVERT_018_SQL), revert_params)

    # ── Step B: insert versioned MoP G2G aliases ────────────────────────────────
    insert_params = [
        {"server_id": server_id, "alias": alias}
        for server_id, alias in _MOP_VERSIONED_ALIASES
    ]
    conn.execute(sa.text(_INSERT_ALIAS_SQL), insert_params)


def downgrade() -> None:
    conn = op.get_bind()

    # Delete versioned MoP aliases inserted in Step B
    conn.execute(
        sa.text(
            "DELETE FROM server_aliases"
            " WHERE alias LIKE '% - MoP Classic] - %'"
            " AND source = 'g2g'"
        )
    )

    # Re-apply migration 018: switch G2G aliases back to mop_ids
    reapply_params = [
        {"classic_id": classic_id, "mop_id": mop_id}
        for classic_id, mop_id in _CLASSIC_TO_MOP
    ]
    conn.execute(sa.text(_REAPPLY_018_SQL), reapply_params)

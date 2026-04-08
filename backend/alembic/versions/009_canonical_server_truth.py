"""Canonical server truth list — deterministic, zero-duplicate, zero-fake-server migration.

Sources:
  3A) Warcraft Wiki (https://warcraft.wiki.gg/wiki/Classic_realms_list):
      Classic Era, Season of Discovery, Hardcore, TBC Classic
  3B) G2G + FunPay offer title analysis:
      Anniversary servers (not on Warcraft Wiki)

Version naming rules (Task 3D):
  "Anniversary"        — WoW 20th Anniversary realms (Nov 2024)
  "Season of Discovery" — SoD / Seasonal realms
  "Classic Era"        — Permanent vanilla-level-cap servers  (wiki canonical)
  "Hardcore"           — Hardcore permadeath realms
  "Classic"            — G2G/FunPay alias version string (historical DB entries 006-008)

SAFE to run repeatedly: all INSERTs use ON CONFLICT DO NOTHING.
Does NOT remove or alter any existing rows.

Revision ID: 009
Revises: 008
Create Date: 2026-04-08
"""
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────────────────
# 3A — Classic Era servers from Warcraft Wiki (canonical English names)
# These use version="Classic Era" (deterministic canonical per Task 3D).
# ON CONFLICT DO NOTHING safely coexists with existing "Classic" entries
# added by migrations 006-008 for the same server names.
# ─────────────────────────────────────────────────────────────────────────────

_CLASSIC_ERA_SERVERS: list[tuple[str, str]] = [
    # EU — English PvP (all verified on Warcraft Wiki)
    ("Bloodfang",        "EU"),
    ("Dreadmist",        "EU"),
    ("Gandling",         "EU"),
    ("Noggenfogger",     "EU"),
    ("Stonespine",       "EU"),
    ("Flamelash",        "EU"),
    ("Ten Storms",       "EU"),
    ("Razorgore",        "EU"),
    ("Judgement",        "EU"),
    ("Zandalar Tribe",   "EU"),
    ("Skullflame",       "EU"),
    ("Dragonfang",       "EU"),
    ("Gehennas",         "EU"),
    ("Golemagg",         "EU"),
    ("Mograine",         "EU"),
    ("Firemaw",          "EU"),
    ("Ashbringer",       "EU"),
    ("Earthshaker",      "EU"),
    # EU — English Normal/PvE
    ("Mirage Raceway",   "EU"),
    ("Pyrewood Village", "EU"),
    ("Nethergarde Keep", "EU"),
    # EU — English RP
    ("Hydraxian Waterlords", "EU"),
    # EU — German PvP
    ("Venoxis",          "EU"),
    ("Razorfen",         "EU"),
    ("Patchwerk",        "EU"),
    # EU — German Normal
    ("Everlook",         "EU"),
    ("Lakeshire",        "EU"),
    ("Transcendence",    "EU"),
    # EU — French PvP
    ("Sulfuron",         "EU"),
    ("Amnennar",         "EU"),
    # EU — French Normal
    ("Auberdine",        "EU"),
    # EU — Spanish
    ("Mandokir",         "EU"),
    # EU — Other (Giantstalker, Jin'do, Thekal)
    ("Giantstalker",     "EU"),
    ("Jin'do",           "EU"),
    ("Thekal",           "EU"),

    # US — West PvP
    ("Whitemane",        "US"),
    ("Angerforge",       "US"),
    ("Skyfury",          "US"),
    # US — West Normal
    ("Atiesh",           "US"),
    ("Azuresong",        "US"),
    ("Old Blanchy",      "US"),
    ("Myzrael",          "US"),
    # US — West RP-PvP
    ("Grobbulus",        "US"),
    # US — East PvP
    ("Benediction",      "US"),
    ("Faerlina",         "US"),
    # US — East Normal
    ("Mankrik",          "US"),
    ("Ashkandi",         "US"),
    ("Pagle",            "US"),
    ("Westfall",         "US"),
    ("Windseeker",       "US"),
    ("Earthfury",        "US"),
    # US — East RP
    ("Bloodsail Buccaneers", "US"),
    # US — PvP (historical, may be merged)
    ("Sulfuras",         "US"),
    ("Thunderfury",      "US"),
    ("Rattlegore",       "US"),
    ("Blaumeux",         "US"),
    ("Kurinnaxx",        "US"),
    ("Fairbanks",        "US"),
    ("Anathema",         "US"),
    ("Smolderweb",       "US"),
    ("Bigglesworth",     "US"),
    ("Arcanite Reaper",  "US"),
    ("Deviate Delight",  "US"),
    ("Defias Pillager",  "US"),
    ("Skull Rock",       "US"),

    # OCE
    ("Arugal",           "OCE"),
    ("Remulos",          "OCE"),
    ("Yojamba",          "OCE"),
    ("Felstriker",       "OCE"),

    # KR
    ("Frostmourne",      "KR"),
    ("Iceblood",         "KR"),
    ("Lokholar",         "KR"),
    ("Ragnaros",         "KR"),
    ("Shimmering Flats", "KR"),

    # TW
    ("Arathi Basin",     "TW"),
    ("Golemagg",         "TW"),
    ("Murloc",           "TW"),
    ("Windseeker",       "TW"),
    ("Zeliek",           "TW"),
    ("Ivus",             "TW"),
    ("Maraudon",         "TW"),
    ("Wushoolay",        "TW"),
]


# ─────────────────────────────────────────────────────────────────────────────
# 3A — Hardcore servers from Warcraft Wiki (canonical "Hardcore" version)
# Stitches + Nek'Rosh are EU Hardcore (not "Classic" even if G2G mislabels).
# Skull Rock + Defias Pillager are US Hardcore.
# Doomhowl is US Anniversary Hardcore.
# Soulseeker is EU Anniversary Hardcore.
# ─────────────────────────────────────────────────────────────────────────────

_HARDCORE_SERVERS: list[tuple[str, str]] = [
    # EU Hardcore (Aug 2023)
    ("Stitches",         "EU"),
    ("Nek'Rosh",         "EU"),
    # US Hardcore (Aug 2023)
    ("Skull Rock",       "US"),
    ("Defias Pillager",  "US"),
    # Note: Doomhowl (US) and Soulseeker (EU) are Anniversary-type Hardcore:
    # they appear in migrations 002/007 under "Anniversary" and "Hardcore" resp.
    # Leave as-is: Doomhowl → US Anniversary, Soulseeker → EU Hardcore (G2G label)
]


# ─────────────────────────────────────────────────────────────────────────────
# 3A — RU Classic Era: English transliterations from Warcraft Wiki
# Cyrillic entries exist in migration 002 under EU Classic Era.
# G2G uses the English transliterated names with region "RU".
# Migrations 006-008 added them as version="Classic" (G2G format).
# Here we add version="Classic Era" canonical forms (ON CONFLICT DO NOTHING).
# ─────────────────────────────────────────────────────────────────────────────

_RU_CLASSIC_ERA_SERVERS: list[str] = [
    "Chromie",           # Хроми
    "Rhok'delar",        # Рок'далар
    "Wyrmthalak",        # Вирмталак
    "Flamegor",          # Пламегор
    "Harbinger of Doom", # Предвестник Судьбы
]

_RU_SOD_SERVERS: list[str] = [
    "Shadowstrike",
    "Penance",
]


# ─────────────────────────────────────────────────────────────────────────────
# 3B — Anniversary servers from G2G + FunPay live data analysis
# Warcraft Wiki does NOT list Anniversary servers (launched Nov 2024).
# Source: G2G offer titles: "Thunderstrike [EU - Anniversary] - ..."
# ─────────────────────────────────────────────────────────────────────────────

_ANNIVERSARY_SERVERS: list[tuple[str, str]] = [
    # EU Anniversary (Nov 21, 2024 launch)
    ("Spineshatter",  "EU"),   # PvP       — already in 002, ON CONFLICT DO NOTHING
    ("Thunderstrike", "EU"),   # Normal/PvE — added in 007, ON CONFLICT DO NOTHING
    ("Soulseeker",    "EU"),   # Hardcore   — added in 007 as "Hardcore", this adds "Anniversary"
    # US Anniversary (Nov 21, 2024 launch)
    ("Nightslayer",   "US"),   # PvP       — already in 002
    ("Dreamscythe",   "US"),   # Normal    — already in 002
    ("Doomhowl",      "US"),   # Hardcore  — already in 002
    # AU/OCE Anniversary
    ("Maladath",      "AU"),   # PvP       — added in 007 as AU Anniversary
]


def _mk_classic_era_aliases() -> list[tuple[str, str, str, str, str]]:
    """Generate G2G-format aliases for Classic Era servers (both [Region - Classic Era] and
    [Region - Classic] variants, since G2G uses 'Classic' not 'Classic Era' in titles)."""
    rows: list[tuple[str, str, str, str, str]] = []
    for name, region in _CLASSIC_ERA_SERVERS:
        for faction in ("Alliance", "Horde"):
            # G2G uses "Classic Era" sometimes, "Classic" more often
            rows.append((f"{name} [{region} - Classic Era] - {faction}", name, region, "Classic Era", "g2g"))
            rows.append((f"{name} [{region} - Classic] - {faction}",    name, region, "Classic Era", "g2g"))
    return rows


def _mk_hardcore_aliases() -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for name, region in _HARDCORE_SERVERS:
        for faction in ("Alliance", "Horde"):
            rows.append((f"{name} [{region} - Hardcore] - {faction}", name, region, "Hardcore", "g2g"))
            # Also a "Classic" variant in case G2G mislabels
            rows.append((f"{name} [{region} - Classic] - {faction}", name, region, "Hardcore", "g2g"))
    return rows


def _mk_ru_aliases() -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for name in _RU_CLASSIC_ERA_SERVERS:
        for faction in ("Alliance", "Horde"):
            # G2G format uses "RU" as region code for Russian servers
            rows.append((f"{name} [RU - Classic Era] - {faction}", name, "RU", "Classic Era", "g2g"))
            rows.append((f"{name} [RU - Classic] - {faction}",     name, "RU", "Classic Era", "g2g"))
    for name in _RU_SOD_SERVERS:
        for faction in ("Alliance", "Horde"):
            rows.append((f"{name} [RU - Season of Discovery] - {faction}", name, "RU", "Season of Discovery", "g2g"))
            rows.append((f"{name} [RU - Seasonal] - {faction}",            name, "RU", "Season of Discovery", "g2g"))
    return rows


def _mk_anniversary_aliases() -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for name, region in _ANNIVERSARY_SERVERS:
        for faction in ("Alliance", "Horde"):
            rows.append((f"{name} [{region} - Anniversary] - {faction}", name, region, "Anniversary", "g2g"))
    # Soulseeker is shown as Hardcore in G2G but is an Anniversary realm.
    # Add cross-version aliases so it resolves regardless of the G2G label.
    for faction in ("Alliance", "Horde"):
        rows.append((f"Soulseeker [EU - Hardcore] - {faction}", "Soulseeker", "EU", "Hardcore", "g2g"))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Compile all inserts
# ─────────────────────────────────────────────────────────────────────────────

def upgrade() -> None:
    # ── 1. Insert Classic Era servers (canonical "Classic Era" version) ───────
    for name, region in _CLASSIC_ERA_SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version)
            VALUES ({_q(name)}, {_q(region)}, 'Classic Era')
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    # ── 2. Insert Hardcore servers (canonical "Hardcore" version) ─────────────
    for name, region in _HARDCORE_SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version)
            VALUES ({_q(name)}, {_q(region)}, 'Hardcore')
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    # ── 3. Insert RU Classic Era servers ─────────────────────────────────────
    for name in _RU_CLASSIC_ERA_SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version)
            VALUES ({_q(name)}, 'RU', 'Classic Era')
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    # ── 4. Insert RU SoD servers ──────────────────────────────────────────────
    for name in _RU_SOD_SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version)
            VALUES ({_q(name)}, 'RU', 'Season of Discovery')
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    # ── 5. Insert Anniversary servers ─────────────────────────────────────────
    for name, region in _ANNIVERSARY_SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version)
            VALUES ({_q(name)}, {_q(region)}, 'Anniversary')
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    # ── 6. Insert all aliases (ON CONFLICT DO NOTHING — safe to re-run) ───────
    all_aliases = (
        _mk_classic_era_aliases()
        + _mk_hardcore_aliases()
        + _mk_ru_aliases()
        + _mk_anniversary_aliases()
    )
    for alias, s_name, s_region, s_version, source in all_aliases:
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
    """No destructive downgrade — this migration only adds rows."""
    pass


def _q(s: str) -> str:
    """Minimal SQL quoting: escape single quotes."""
    return "'" + s.replace("'", "''") + "'"

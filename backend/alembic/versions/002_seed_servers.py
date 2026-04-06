"""Seed canonical server registry from Warcraft Wiki + known G2G/FunPay aliases.

Sources:
  - Warcraft Wiki: https://warcraft.wiki.gg/wiki/Classic_realms_list (Nov 2024)
  - G2G live: confirmed Anniversary EU realms include Spineshatter (PvP, not in wiki)
  - FunPay: group labels map to all servers of a region+version

Revision ID: 002
Revises: 001
Create Date: 2026-04-06
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


# ── Server data ───────────────────────────────────────────────────────────────
# Format: (name, region, version)
_SERVERS = [
    # ── Anniversary (20th Anniversary realms, EU) ────────────────────────────
    # These are TBC-Classic-era realms launched Nov 2024
    ("Spineshatter",      "EU",  "Anniversary"),   # PvP — confirmed on G2G, not in wiki
    ("Lava Lash",         "EU",  "Anniversary"),   # TODO: confirm EU Anni realms from wiki
    ("Crusader Strike",   "EU",  "Anniversary"),
    ("Living Flame",      "EU",  "Anniversary"),
    ("Lone Wolf",         "EU",  "Anniversary"),
    ("Wild Growth",       "EU",  "Anniversary"),
    ("Nightfall",         "EU",  "Anniversary"),
    # Anniversary (Americas)
    ("Dreamscythe",       "US",  "Anniversary"),   # PvE, MST
    ("Nightslayer",       "US",  "Anniversary"),   # PvP, MST
    ("Doomhowl",          "US",  "Anniversary"),   # Hardcore, MST
    ("Maladath",          "US",  "Anniversary"),   # PvP, AEDT (OCE-timezone)

    # ── Season of Discovery (Seasonal realms) ────────────────────────────────
    # Americas & Oceanic
    ("Chaos Bolt",        "US",  "Season of Discovery"),
    ("Crusader Strike",   "US",  "Season of Discovery"),
    ("Lava Lash",         "US",  "Season of Discovery"),
    ("Living Flame",      "US",  "Season of Discovery"),
    ("Lone Wolf",         "US",  "Season of Discovery"),
    ("Penance",           "US",  "Season of Discovery"),
    ("Shadowstrike",      "US",  "Season of Discovery"),
    ("Wild Growth",       "US",  "Season of Discovery"),
    # Europe
    ("Chaos Bolt",        "EU",  "Season of Discovery"),
    ("Crusader Strike",   "EU",  "Season of Discovery"),
    ("Lava Lash",         "EU",  "Season of Discovery"),
    ("Living Flame",      "EU",  "Season of Discovery"),
    ("Lone Wolf",         "EU",  "Season of Discovery"),
    ("Wild Growth",       "EU",  "Season of Discovery"),
    ("Penance",           "EU",  "Season of Discovery"),   # RU
    ("Shadowstrike",      "EU",  "Season of Discovery"),   # RU
    # Korea
    ("Lone Wolf",         "KR",  "Season of Discovery"),
    ("Wild Growth",       "KR",  "Season of Discovery"),
    # Taiwan
    ("Crusader Strike",   "TW",  "Season of Discovery"),
    ("Living Flame",      "TW",  "Season of Discovery"),
    ("Lone Wolf",         "TW",  "Season of Discovery"),
    ("Wild Growth",       "TW",  "Season of Discovery"),

    # ── Classic Era (EU English) ──────────────────────────────────────────────
    ("Firemaw",           "EU",  "Classic Era"),
    ("Gehennas",          "EU",  "Classic Era"),
    ("Golemagg",          "EU",  "Classic Era"),
    ("Mograine",          "EU",  "Classic Era"),
    ("Mirage Raceway",    "EU",  "Classic Era"),
    ("Pyrewood Village",  "EU",  "Classic Era"),
    ("Nethergarde Keep",  "EU",  "Classic Era"),
    ("Earthshaker",       "EU",  "Classic Era"),
    ("Ashbringer",        "EU",  "Classic Era"),
    ("Giantstalker",      "EU",  "Classic Era"),
    ("Hydraxian Waterlords", "EU", "Classic Era"),
    ("Jin'do",            "EU",  "Classic Era"),
    ("Thekal",            "EU",  "Classic Era"),
    # EU French
    ("Amnennar",          "EU",  "Classic Era"),
    ("Auberdine",         "EU",  "Classic Era"),
    ("Sulfuron",          "EU",  "Classic Era"),
    # EU German
    ("Everlook",          "EU",  "Classic Era"),
    ("Lakeshire",         "EU",  "Classic Era"),
    ("Patchwerk",         "EU",  "Classic Era"),
    ("Razorfen",          "EU",  "Classic Era"),
    ("Transcendence",     "EU",  "Classic Era"),
    ("Venoxis",           "EU",  "Classic Era"),
    # EU Russian
    ("Хроми",             "EU",  "Classic Era"),   # Chromie
    ("Пламегор",          "EU",  "Classic Era"),   # Flamegor
    # EU Spanish
    ("Mandokir",          "EU",  "Classic Era"),

    # ── Classic Era (US) ──────────────────────────────────────────────────────
    # US West
    ("Angerforge",        "US",  "Classic Era"),
    ("Atiesh",            "US",  "Classic Era"),
    ("Azuresong",         "US",  "Classic Era"),
    ("Grobbulus",         "US",  "Classic Era"),
    ("Myzrael",           "US",  "Classic Era"),
    ("Old Blanchy",       "US",  "Classic Era"),
    ("Skyfury",           "US",  "Classic Era"),
    ("Whitemane",         "US",  "Classic Era"),
    # US East
    ("Ashkandi",          "US",  "Classic Era"),
    ("Benediction",       "US",  "Classic Era"),
    ("Bloodsail Buccaneers", "US", "Classic Era"),
    ("Earthfury",         "US",  "Classic Era"),
    ("Faerlina",          "US",  "Classic Era"),
    ("Maladath",          "US",  "Classic Era"),
    ("Mankrik",           "US",  "Classic Era"),
    ("Pagle",             "US",  "Classic Era"),
    ("Sulfuras",          "US",  "Classic Era"),
    ("Westfall",          "US",  "Classic Era"),
    ("Windseeker",        "US",  "Classic Era"),

    # ── Classic Era (Oceanic) ─────────────────────────────────────────────────
    ("Arugal",            "OCE", "Classic Era"),
    ("Remulos",           "OCE", "Classic Era"),
    ("Yojamba",           "OCE", "Classic Era"),

    # ── Classic Era (Korea) ───────────────────────────────────────────────────
    ("Frostmourne",       "KR",  "Classic Era"),
    ("Iceblood",          "KR",  "Classic Era"),
    ("Lokholar",          "KR",  "Classic Era"),
    ("Ragnaros",          "KR",  "Classic Era"),
    ("Shimmering Flats",  "KR",  "Classic Era"),

    # ── Classic Era (Taiwan) ──────────────────────────────────────────────────
    ("Arathi Basin",      "TW",  "Classic Era"),
    ("Golemagg",          "TW",  "Classic Era"),
    ("Murloc",            "TW",  "Classic Era"),
    ("Windseeker",        "TW",  "Classic Era"),
    ("Zeliek",            "TW",  "Classic Era"),
    ("Ivus",              "TW",  "Classic Era"),
    ("Maraudon",          "TW",  "Classic Era"),
    ("Wushoolay",         "TW",  "Classic Era"),
]


# ── Alias data ────────────────────────────────────────────────────────────────
# Format: (alias_text, server_name, region, version, source)
# FunPay group aliases cover all servers in a region+version group.
# G2G aliases are per-server.
_ALIASES: list[tuple[str, str, str, str, str | None]] = [
    # G2G aliases (from known offer titles parsed by g2g_parser)
    ("Spineshatter [EU - Anniversary] - Alliance", "Spineshatter", "EU", "Anniversary", "g2g"),
    ("Spineshatter [EU - Anniversary] - Horde",    "Spineshatter", "EU", "Anniversary", "g2g"),
    ("Firemaw [EU - Classic Era] - Alliance",       "Firemaw",      "EU", "Classic Era", "g2g"),
    ("Firemaw [EU - Classic Era] - Horde",          "Firemaw",      "EU", "Classic Era", "g2g"),
    ("Gehennas [EU - Classic Era] - Alliance",      "Gehennas",     "EU", "Classic Era", "g2g"),
    ("Gehennas [EU - Classic Era] - Horde",         "Gehennas",     "EU", "Classic Era", "g2g"),
    ("Golemagg [EU - Classic Era] - Alliance",      "Golemagg",     "EU", "Classic Era", "g2g"),
    ("Golemagg [EU - Classic Era] - Horde",         "Golemagg",     "EU", "Classic Era", "g2g"),
    ("Lava Lash [EU - Seasonal] - Alliance",        "Lava Lash",    "EU", "Season of Discovery", "g2g"),
    ("Lava Lash [EU - Seasonal] - Horde",           "Lava Lash",    "EU", "Season of Discovery", "g2g"),
    ("Crusader Strike [EU - Seasonal] - Alliance",  "Crusader Strike", "EU", "Season of Discovery", "g2g"),
    ("Crusader Strike [EU - Seasonal] - Horde",     "Crusader Strike", "EU", "Season of Discovery", "g2g"),
    ("Living Flame [EU - Seasonal] - Alliance",     "Living Flame", "EU", "Season of Discovery", "g2g"),
    ("Living Flame [EU - Seasonal] - Horde",        "Living Flame", "EU", "Season of Discovery", "g2g"),
    ("Mograine [EU - Classic Era] - Alliance",      "Mograine",     "EU", "Classic Era", "g2g"),
    ("Mograine [EU - Classic Era] - Horde",         "Mograine",     "EU", "Classic Era", "g2g"),
    ("Mirage Raceway [EU - Classic Era] - Alliance","Mirage Raceway","EU", "Classic Era", "g2g"),
    ("Mirage Raceway [EU - Classic Era] - Horde",   "Mirage Raceway","EU", "Classic Era", "g2g"),
    ("Pyrewood Village [EU - Classic Era] - Alliance","Pyrewood Village","EU","Classic Era","g2g"),
    ("Pyrewood Village [EU - Classic Era] - Horde", "Pyrewood Village","EU","Classic Era","g2g"),
    ("Everlook [EU - Classic Era] - Alliance",      "Everlook",     "EU", "Classic Era", "g2g"),
    ("Everlook [EU - Classic Era] - Horde",         "Everlook",     "EU", "Classic Era", "g2g"),
    ("Venoxis [EU - Classic Era] - Alliance",       "Venoxis",      "EU", "Classic Era", "g2g"),
    ("Venoxis [EU - Classic Era] - Horde",          "Venoxis",      "EU", "Classic Era", "g2g"),
    ("Patchwerk [EU - Classic Era] - Alliance",     "Patchwerk",    "EU", "Classic Era", "g2g"),
    ("Patchwerk [EU - Classic Era] - Horde",        "Patchwerk",    "EU", "Classic Era", "g2g"),
    ("Faerlina [US - Classic Era] - Alliance",      "Faerlina",     "US", "Classic Era", "g2g"),
    ("Faerlina [US - Classic Era] - Horde",         "Faerlina",     "US", "Classic Era", "g2g"),
    ("Benediction [US - Classic Era] - Alliance",   "Benediction",  "US", "Classic Era", "g2g"),
    ("Benediction [US - Classic Era] - Horde",      "Benediction",  "US", "Classic Era", "g2g"),
    ("Grobbulus [US - Classic Era] - Alliance",     "Grobbulus",    "US", "Classic Era", "g2g"),
    ("Grobbulus [US - Classic Era] - Horde",        "Grobbulus",    "US", "Classic Era", "g2g"),
    ("Whitemane [US - Classic Era] - Alliance",     "Whitemane",    "US", "Classic Era", "g2g"),
    ("Whitemane [US - Classic Era] - Horde",        "Whitemane",    "US", "Classic Era", "g2g"),
    ("Mankrik [US - Classic Era] - Alliance",       "Mankrik",      "US", "Classic Era", "g2g"),
    ("Mankrik [US - Classic Era] - Horde",          "Mankrik",      "US", "Classic Era", "g2g"),
    ("Nightslayer [US - Anniversary] - Alliance",   "Nightslayer",  "US", "Anniversary", "g2g"),
    ("Nightslayer [US - Anniversary] - Horde",      "Nightslayer",  "US", "Anniversary", "g2g"),
    ("Dreamscythe [US - Anniversary] - Alliance",   "Dreamscythe",  "US", "Anniversary", "g2g"),
    ("Dreamscythe [US - Anniversary] - Horde",      "Dreamscythe",  "US", "Anniversary", "g2g"),
    ("Arugal [OCE - Classic Era] - Alliance",       "Arugal",       "OCE","Classic Era", "g2g"),
    ("Arugal [OCE - Classic Era] - Horde",          "Arugal",       "OCE","Classic Era", "g2g"),
    # G2G Seasonal aliases (some variations)
    ("Lava Lash [EU - Season of Discovery] - Horde","Lava Lash",    "EU", "Season of Discovery","g2g"),
    ("Lava Lash [US - Seasonal] - Alliance",        "Lava Lash",    "US", "Season of Discovery","g2g"),
    ("Lava Lash [US - Seasonal] - Horde",           "Lava Lash",    "US", "Season of Discovery","g2g"),
    ("Living Flame [US - Seasonal] - Alliance",     "Living Flame", "US", "Season of Discovery","g2g"),
    ("Living Flame [US - Seasonal] - Horde",        "Living Flame", "US", "Season of Discovery","g2g"),
]


def upgrade() -> None:
    # ── Insert canonical servers ──────────────────────────────────────────────
    for name, region, version in _SERVERS:
        op.execute(f"""
            INSERT INTO servers (name, region, version)
            VALUES ({_q(name)}, {_q(region)}, {_q(version)})
            ON CONFLICT (name, region, version) DO NOTHING;
        """)

    # ── Insert aliases ────────────────────────────────────────────────────────
    for alias, s_name, s_region, s_version, source in _ALIASES:
        src_val = f"'{source}'" if source else "NULL"
        op.execute(f"""
            INSERT INTO server_aliases (server_id, alias, source)
            SELECT s.id, {_q(alias)}, {src_val}
            FROM servers s
            WHERE s.name = {_q(s_name)}
              AND s.region = {_q(s_region)}
              AND s.version = {_q(s_version)}
            ON CONFLICT (alias) DO NOTHING;
        """)


def downgrade() -> None:
    op.execute("DELETE FROM server_aliases;")
    op.execute("DELETE FROM servers;")


def _q(s: str) -> str:
    """Minimal SQL string quoting (single-quote escape)."""
    return "'" + s.replace("'", "''") + "'"

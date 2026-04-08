"""
db/canonical_servers.py — Python-level canonical server registry.

This module is the SINGLE SOURCE OF TRUTH for the WoW Classic canonical
server domain model. It documents the complete list of known servers and
defines the rules for how they are categorised.

Role
----
  • Documentation: authoritative definition of all canonical servers.
  • Testing: test suites import CANONICAL_SERVERS to validate DB state.
  • Seed validation: migration scripts should be consistent with this file.
  • Runtime: the DB (`servers` + `server_aliases` tables) is the live registry;
    this file does NOT replace DB queries at runtime. Use db/server_resolver.py
    for runtime lookups.

Domain Model
------------
Dimension 1 — Game Version (actual game type):
  "Classic"           — original Classic Era (PvP/PvE/Normal)
  "Classic Era"       — permanent vanilla-cap servers (canonical name since 009)
  "Anniversary"       — 20th Anniversary realms (launched Nov 2024); includes
                        TBC Anniversary progression
  "Seasonal"          — Season of Discovery / SoD (G2G alias: "Seasonal")
  "Season of Mastery" — DEPRECATED (SoM closed Mar 2022); is_active=False;
                        offers quarantined with reason="deprecated_version"

Dimension 2 — Realm Type:
  "Normal"    — standard PvP or PvE realm
  "Hardcore"  — permadeath ruleset realm

⚠️  Hardcore is NOT a game version. It is a realm_type. The `version` field
    always reflects the game content (Classic Era, Anniversary, …).
    Example: Gehennas Hardcore EU → version="Classic Era", realm_type="Hardcore"

Region
------
  Region is a FIXED property of a canonical server. It is taken from this
  registry, NOT from the parser source. If FunPay or G2G report a different
  region for a known server, the canonical region overrides it and the event
  is logged as reason="wrong_region_overridden".

  Known regions: "EU", "US", "AU", "OCE", "KR", "TW", "RU", "SEA"

  Special cases:
    • Penance, Shadowstrike  → AU (G2G places them under EU/RU buckets)
    • Maladath               → AU (G2G places it under US bucket)
    • NA is normalised to US in resolver

Alias Uniqueness
----------------
  Each alias string MUST map to exactly ONE canonical server. Duplicate aliases
  are a configuration error and are logged as reason="alias_conflict".
  The conflict is NOT resolved automatically — manual review is required.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CanonicalServer:
    """Canonical server definition.

    Fields
    ------
    name        : Canonical English realm name (e.g. "Firemaw").
    region      : Fixed geographic region ("EU", "US", "AU", "KR", "TW", "RU").
    version     : Game content version (see domain model above).
    realm_type  : "Normal" or "Hardcore".
    is_active   : False for deprecated versions (SoM); offers are quarantined.
    aliases     : All known raw title fragments that map to this server in G2G
                  and FunPay offer titles. The DB server_aliases table stores
                  the full alias strings; these are partial name variants used
                  to generate those strings.
    notes       : Free-form notes for maintainers.
    """
    name:       str
    region:     str
    version:    str
    realm_type: str            = "Normal"
    is_active:  bool           = True
    aliases:    tuple[str, ...]= field(default_factory=tuple)
    notes:      str            = ""


# ── Valid domain values ───────────────────────────────────────────────────────

VALID_VERSIONS: frozenset[str] = frozenset({
    "Classic",           # legacy label still present in DB from migrations 006-008
    "Classic Era",       # canonical since migration 009
    "Anniversary",       # 20th Anniversary (Nov 2024) + TBC Anniversary
    "Seasonal",          # G2G alias for Season of Discovery
    "Season of Discovery",
    "Season of Mastery", # DEPRECATED
})

VALID_REALM_TYPES: frozenset[str] = frozenset({"Normal", "Hardcore"})
VALID_REGIONS: frozenset[str] = frozenset({"EU", "US", "AU", "OCE", "KR", "TW", "RU", "SEA"})

# Versions that map to the same product (for display normalisation):
VERSION_DISPLAY_MAP: dict[str, str] = {
    "Classic":            "Classic Era",     # DB legacy → canonical display
    "Seasonal":           "Season of Discovery",
    "Season of Discovery":"Season of Discovery",
}

# ── Canonical server registry ─────────────────────────────────────────────────
# Ordered by: version group, then region, then name.
# Migrations 001–009 seed the DB from subsets of this list.
# Migration 010 adds realm_type and corrects Hardcore servers.

CANONICAL_SERVERS: tuple[CanonicalServer, ...] = (

    # ═══════════════════════════════════════════════════════════════════════════
    # ANNIVERSARY REALMS (20th Anniversary — launched Nov 21, 2024)
    # Includes TBC Anniversary progression content.
    # ═══════════════════════════════════════════════════════════════════════════

    # ── EU Anniversary — Normal ───────────────────────────────────────────────
    CanonicalServer("Spineshatter",  "EU", "Anniversary", notes="PvP"),
    CanonicalServer("Thunderstrike", "EU", "Anniversary", notes="Normal/PvE"),
    CanonicalServer("Lava Lash",     "EU", "Anniversary"),
    CanonicalServer("Crusader Strike","EU", "Anniversary"),
    CanonicalServer("Living Flame",  "EU", "Anniversary"),
    CanonicalServer("Lone Wolf",     "EU", "Anniversary"),
    CanonicalServer("Wild Growth",   "EU", "Anniversary"),
    CanonicalServer("Nightfall",     "EU", "Anniversary"),
    # ── EU Anniversary — Hardcore ─────────────────────────────────────────────
    CanonicalServer(
        "Soulseeker", "EU", "Anniversary", realm_type="Hardcore",
        aliases=("Soulseeker [EU - Hardcore]", "Soulseeker [EU - Anniversary]"),
        notes="EU Anniversary Hardcore; G2G sometimes labels as 'Hardcore' version",
    ),

    # ── US Anniversary — Normal ───────────────────────────────────────────────
    CanonicalServer("Nightslayer",   "US", "Anniversary", notes="PvP, MST"),
    CanonicalServer("Dreamscythe",   "US", "Anniversary", notes="Normal, MST"),
    # ── US Anniversary — Hardcore ─────────────────────────────────────────────
    CanonicalServer(
        "Doomhowl", "US", "Anniversary", realm_type="Hardcore",
        aliases=("Doomhowl [US - Hardcore]", "Doomhowl [US - Anniversary]"),
        notes="US Anniversary Hardcore",
    ),

    # ── AU Anniversary — Normal ───────────────────────────────────────────────
    CanonicalServer(
        "Maladath", "AU", "Anniversary",
        aliases=("Maladath [AU - Anniversary]", "Maladath [US - Anniversary]"),
        notes="AU/OCE PvP; G2G files under US region bucket — override to AU",
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # SEASON OF DISCOVERY (Seasonal)
    # G2G label: "Seasonal"  Canonical label: "Season of Discovery"
    # ═══════════════════════════════════════════════════════════════════════════

    # ── EU Season of Discovery ────────────────────────────────────────────────
    CanonicalServer("Chaos Bolt",     "EU", "Season of Discovery"),
    CanonicalServer("Crusader Strike","EU", "Season of Discovery"),
    CanonicalServer("Lava Lash",      "EU", "Season of Discovery"),
    CanonicalServer("Living Flame",   "EU", "Season of Discovery"),
    CanonicalServer("Lone Wolf",      "EU", "Season of Discovery"),
    CanonicalServer("Wild Growth",    "EU", "Season of Discovery"),

    # ── US Season of Discovery ────────────────────────────────────────────────
    CanonicalServer("Chaos Bolt",     "US", "Season of Discovery"),
    CanonicalServer("Crusader Strike","US", "Season of Discovery"),
    CanonicalServer("Lava Lash",      "US", "Season of Discovery"),
    CanonicalServer("Living Flame",   "US", "Season of Discovery"),
    CanonicalServer("Lone Wolf",      "US", "Season of Discovery"),
    CanonicalServer("Wild Growth",    "US", "Season of Discovery"),

    # ── AU Season of Discovery ────────────────────────────────────────────────
    # ⚠️ G2G places Penance and Shadowstrike under EU or RU region buckets.
    # Their CANONICAL region is AU. Source region is overridden at normalization.
    CanonicalServer(
        "Penance", "AU", "Season of Discovery",
        aliases=("Penance [EU - Seasonal]", "Penance [RU - Seasonal]",
                 "Penance [US - Seasonal]"),
        notes="AU/OCE SoD; G2G files under EU/RU — override to AU",
    ),
    CanonicalServer(
        "Shadowstrike", "AU", "Season of Discovery",
        aliases=("Shadowstrike [EU - Seasonal]", "Shadowstrike [RU - Seasonal]",
                 "Shadowstrike [US - Seasonal]"),
        notes="AU/OCE SoD; G2G files under EU/RU — override to AU",
    ),

    # ── KR Season of Discovery ────────────────────────────────────────────────
    CanonicalServer("Lone Wolf",  "KR", "Season of Discovery"),
    CanonicalServer("Wild Growth","KR", "Season of Discovery"),

    # ── TW Season of Discovery ────────────────────────────────────────────────
    CanonicalServer("Crusader Strike","TW", "Season of Discovery"),
    CanonicalServer("Living Flame",   "TW", "Season of Discovery"),
    CanonicalServer("Lone Wolf",      "TW", "Season of Discovery"),
    CanonicalServer("Wild Growth",    "TW", "Season of Discovery"),

    # ═══════════════════════════════════════════════════════════════════════════
    # CLASSIC ERA (Permanent vanilla servers)
    # ═══════════════════════════════════════════════════════════════════════════

    # ── EU Classic Era — Normal ───────────────────────────────────────────────
    CanonicalServer("Bloodfang",          "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Dreadmist",          "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Flamelash",          "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Gandling",           "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Gehennas",           "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Golemagg",           "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Judgement",          "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Mograine",           "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Noggenfogger",       "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Razorgore",          "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Skullflame",         "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Stonespine",         "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Ten Storms",         "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Zandalar Tribe",     "EU", "Classic Era", notes="RP-PvP"),
    CanonicalServer("Dragonfang",         "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Firemaw",            "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Ashbringer",         "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Earthshaker",        "EU", "Classic Era", notes="PvP"),
    CanonicalServer("Mirage Raceway",     "EU", "Classic Era", notes="PvE"),
    CanonicalServer("Pyrewood Village",   "EU", "Classic Era", notes="PvE"),
    CanonicalServer("Nethergarde Keep",   "EU", "Classic Era", notes="PvE"),
    CanonicalServer("Hydraxian Waterlords","EU","Classic Era", notes="RP"),
    CanonicalServer("Venoxis",            "EU", "Classic Era", notes="DE PvP"),
    CanonicalServer("Razorfen",           "EU", "Classic Era", notes="DE PvP"),
    CanonicalServer("Patchwerk",          "EU", "Classic Era", notes="DE PvP"),
    CanonicalServer("Everlook",           "EU", "Classic Era", notes="DE PvE"),
    CanonicalServer("Lakeshire",          "EU", "Classic Era", notes="DE PvE"),
    CanonicalServer("Transcendence",      "EU", "Classic Era", notes="DE PvE"),
    CanonicalServer("Sulfuron",           "EU", "Classic Era", notes="FR PvP"),
    CanonicalServer("Amnennar",           "EU", "Classic Era", notes="FR PvP"),
    CanonicalServer("Auberdine",          "EU", "Classic Era", notes="FR PvE"),
    CanonicalServer("Mandokir",           "EU", "Classic Era", notes="ES"),
    CanonicalServer("Giantstalker",       "EU", "Classic Era"),
    CanonicalServer("Jin'do",             "EU", "Classic Era"),
    CanonicalServer("Thekal",             "EU", "Classic Era"),

    # ── EU Classic Era — Hardcore ─────────────────────────────────────────────
    CanonicalServer(
        "Stitches", "EU", "Classic Era", realm_type="Hardcore",
        aliases=("Stitches [EU - Hardcore]", "Stitches [EU - Classic]",
                 "Stitches [EU - Classic Era]"),
        notes="EU Classic Era Hardcore (Aug 2023); G2G labels vary",
    ),
    CanonicalServer(
        "Nek'Rosh", "EU", "Classic Era", realm_type="Hardcore",
        aliases=("Nek'Rosh [EU - Hardcore]", "Nek'Rosh [EU - Classic]",
                 "Nek'Rosh [EU - Classic Era]"),
        notes="EU Classic Era Hardcore (Aug 2023)",
    ),

    # ── EU Classic Era — RU ───────────────────────────────────────────────────
    CanonicalServer("Chromie",           "RU", "Classic Era", notes="Хроми"),
    CanonicalServer("Rhok'delar",        "RU", "Classic Era", notes="Рок'далар"),
    CanonicalServer("Wyrmthalak",        "RU", "Classic Era", notes="Вирмталак"),
    CanonicalServer("Flamegor",          "RU", "Classic Era", notes="Пламегор"),
    CanonicalServer("Harbinger of Doom", "RU", "Classic Era", notes="Предвестник Судьбы"),

    # ── US Classic Era — Normal ───────────────────────────────────────────────
    CanonicalServer("Whitemane",            "US", "Classic Era", notes="West PvP"),
    CanonicalServer("Angerforge",           "US", "Classic Era", notes="West PvP"),
    CanonicalServer("Skyfury",              "US", "Classic Era", notes="West PvP"),
    CanonicalServer("Atiesh",              "US", "Classic Era", notes="West PvE"),
    CanonicalServer("Azuresong",            "US", "Classic Era", notes="West PvE"),
    CanonicalServer("Old Blanchy",          "US", "Classic Era", notes="West PvE"),
    CanonicalServer("Myzrael",              "US", "Classic Era", notes="West PvE"),
    CanonicalServer("Grobbulus",            "US", "Classic Era", notes="West RP-PvP"),
    CanonicalServer("Benediction",          "US", "Classic Era", notes="East PvP"),
    CanonicalServer("Faerlina",             "US", "Classic Era", notes="East PvP"),
    CanonicalServer("Mankrik",              "US", "Classic Era", notes="East PvE"),
    CanonicalServer("Ashkandi",             "US", "Classic Era", notes="East PvE"),
    CanonicalServer("Pagle",                "US", "Classic Era", notes="East PvE"),
    CanonicalServer("Westfall",             "US", "Classic Era", notes="East PvE"),
    CanonicalServer("Windseeker",           "US", "Classic Era", notes="East PvE"),
    CanonicalServer("Earthfury",            "US", "Classic Era", notes="East PvE"),
    CanonicalServer("Bloodsail Buccaneers", "US", "Classic Era", notes="East RP"),
    CanonicalServer("Sulfuras",             "US", "Classic Era"),
    CanonicalServer("Thunderfury",          "US", "Classic Era"),
    CanonicalServer("Rattlegore",           "US", "Classic Era"),
    CanonicalServer("Blaumeux",             "US", "Classic Era"),
    CanonicalServer("Kurinnaxx",            "US", "Classic Era"),
    CanonicalServer("Fairbanks",            "US", "Classic Era"),
    CanonicalServer("Anathema",             "US", "Classic Era"),
    CanonicalServer("Smolderweb",           "US", "Classic Era"),
    CanonicalServer("Bigglesworth",         "US", "Classic Era"),
    CanonicalServer("Arcanite Reaper",      "US", "Classic Era"),
    CanonicalServer("Deviate Delight",      "US", "Classic Era"),
    CanonicalServer("Maladath",             "US", "Classic Era",
                    notes="US Classic Era (different from AU Anniversary Maladath)"),

    # ── US Classic Era — Hardcore ─────────────────────────────────────────────
    CanonicalServer(
        "Skull Rock", "US", "Classic Era", realm_type="Hardcore",
        aliases=("Skull Rock [US - Hardcore]", "Skull Rock [US - Classic]",
                 "Skull Rock [US - Classic Era]"),
        notes="US Classic Era Hardcore (Aug 2023)",
    ),
    CanonicalServer(
        "Defias Pillager", "US", "Classic Era", realm_type="Hardcore",
        aliases=("Defias Pillager [US - Hardcore]", "Defias Pillager [US - Classic]",
                 "Defias Pillager [US - Classic Era]"),
        notes="US Classic Era Hardcore (Aug 2023)",
    ),

    # ── OCE Classic Era ───────────────────────────────────────────────────────
    CanonicalServer("Arugal",   "OCE", "Classic Era"),
    CanonicalServer("Remulos",  "OCE", "Classic Era"),
    CanonicalServer("Yojamba",  "OCE", "Classic Era"),
    CanonicalServer("Felstriker","OCE","Classic Era"),

    # ── KR Classic Era ────────────────────────────────────────────────────────
    CanonicalServer("Frostmourne",     "KR", "Classic Era"),
    CanonicalServer("Iceblood",        "KR", "Classic Era"),
    CanonicalServer("Lokholar",        "KR", "Classic Era"),
    CanonicalServer("Ragnaros",        "KR", "Classic Era"),
    CanonicalServer("Shimmering Flats","KR", "Classic Era"),

    # ── TW Classic Era ────────────────────────────────────────────────────────
    CanonicalServer("Arathi Basin", "TW", "Classic Era"),
    CanonicalServer("Golemagg",     "TW", "Classic Era"),
    CanonicalServer("Murloc",       "TW", "Classic Era"),
    CanonicalServer("Windseeker",   "TW", "Classic Era"),
    CanonicalServer("Zeliek",       "TW", "Classic Era"),
    CanonicalServer("Ivus",         "TW", "Classic Era"),
    CanonicalServer("Maraudon",     "TW", "Classic Era"),
    CanonicalServer("Wushoolay",    "TW", "Classic Era"),

    # ═══════════════════════════════════════════════════════════════════════════
    # SEASON OF MASTERY — DEPRECATED (is_active=False)
    # Realms closed March 2022. Offers quarantined: reason="deprecated_version".
    # ═══════════════════════════════════════════════════════════════════════════
    CanonicalServer("Jom Gabbar",    "US", "Season of Mastery", is_active=False),
    CanonicalServer("Risen Spirits", "US", "Season of Mastery", is_active=False),
    CanonicalServer("Tesladin",      "US", "Season of Mastery", is_active=False),
    CanonicalServer("Dreadnaught",   "US", "Season of Mastery", is_active=False),
    CanonicalServer("Shadowstrike",  "EU", "Season of Mastery", is_active=False,
                    notes="Different from AU Shadowstrike (SoD); SoM closed Mar 2022"),
)


# ── Derived lookups (build at import time) ────────────────────────────────────

def _build_alias_to_server_map() -> dict[str, CanonicalServer]:
    """
    Build a lookup: alias_fragment (lowercase) → CanonicalServer.

    Used by test suites to validate alias uniqueness.
    At runtime the DB server_aliases table is the authoritative source.
    """
    mapping: dict[str, CanonicalServer] = {}
    conflicts: list[str] = []

    for server in CANONICAL_SERVERS:
        for alias in server.aliases:
            key = alias.lower()
            if key in mapping:
                conflicts.append(
                    f"Alias conflict: {alias!r} → {mapping[key].name!r} "
                    f"AND {server.name!r}"
                )
            else:
                mapping[key] = server

    if conflicts:
        import warnings
        for msg in conflicts:
            warnings.warn(msg, stacklevel=2)

    return mapping


ALIAS_TO_SERVER: dict[str, CanonicalServer] = _build_alias_to_server_map()

# Name + region → list of versions (used for uniqueness checks in tests)
def get_server_versions(name: str, region: str) -> list[CanonicalServer]:
    """Return all canonical entries for a given realm name + region."""
    key = (name.lower(), region.upper())
    return [
        s for s in CANONICAL_SERVERS
        if s.name.lower() == key[0] and s.region.upper() == key[1]
    ]


def get_active_servers() -> list[CanonicalServer]:
    """Return only servers that are active (is_active=True)."""
    return [s for s in CANONICAL_SERVERS if s.is_active]


def get_servers_by_version(version: str) -> list[CanonicalServer]:
    """Return all canonical servers for a given version string."""
    return [s for s in CANONICAL_SERVERS if s.version == version]

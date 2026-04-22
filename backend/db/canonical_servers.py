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
    "MoP Classic",      # Mists of Pandaria Classic (2025)
    "Retail",
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

    # ═══════════════════════════════════════════════════════════════════════
    # MISTS OF PANDARIA CLASSIC (launched 2025)
    # G2G: seo_term=wow-classic-gold, brand_id=lgc_game_29076
    # FunPay: chip 145 (RU), chip 146 (EU/DE/ES/FR), chip 147 (US/OCE)
    # NOTE: many server names collide with Classic Era — they are distinct
    # servers disambiguated at resolution time via game_version from parser config.
    # G2G region labels DE/ES/FR → canonical region EU.
    # ═══════════════════════════════════════════════════════════════════════

    # ── EU MoP Classic ───────────────────────────────────────────────────
    CanonicalServer("Ashbringer",           "EU", "MoP Classic"),
    CanonicalServer("Earthshaker",          "EU", "MoP Classic"),
    CanonicalServer("Firemaw",              "EU", "MoP Classic"),
    CanonicalServer("Garalon",              "EU", "MoP Classic"),
    CanonicalServer("Gehennas",             "EU", "MoP Classic"),
    CanonicalServer("Giantstalker",         "EU", "MoP Classic"),
    CanonicalServer("Golemagg",             "EU", "MoP Classic"),
    CanonicalServer("Hoptallus",            "EU", "MoP Classic"),
    CanonicalServer("Hydraxian Waterlords", "EU", "MoP Classic"),
    CanonicalServer("Jin'do",               "EU", "MoP Classic"),
    CanonicalServer("Mirage Raceway",       "EU", "MoP Classic"),
    CanonicalServer("Mograine",             "EU", "MoP Classic"),
    CanonicalServer("Nethergarde Keep",     "EU", "MoP Classic"),
    CanonicalServer("Norushen",             "EU", "MoP Classic"),
    CanonicalServer("Ook Ook",              "EU", "MoP Classic"),
    CanonicalServer("Pyrewood Village",     "EU", "MoP Classic"),
    CanonicalServer("Shek'zeer",            "EU", "MoP Classic"),
    CanonicalServer("Thekal",               "EU", "MoP Classic"),
    # DE-localised realms — G2G label [DE], FunPay chip 146
    CanonicalServer("Everlook",      "EU", "MoP Classic", notes="DE"),
    CanonicalServer("Lakeshire",     "EU", "MoP Classic", notes="DE"),
    CanonicalServer("Patchwerk",     "EU", "MoP Classic", notes="DE"),
    CanonicalServer("Razorfen",      "EU", "MoP Classic", notes="DE"),
    CanonicalServer("Transcendence", "EU", "MoP Classic", notes="DE"),
    CanonicalServer("Venoxis",       "EU", "MoP Classic", notes="DE"),
    # ES/FR-localised realms — G2G labels [ES]/[FR], FunPay chip 146
    CanonicalServer("Mandokir", "EU", "MoP Classic", notes="ES"),
    CanonicalServer("Amnennar",  "EU", "MoP Classic", notes="FR"),
    CanonicalServer("Auberdine", "EU", "MoP Classic", notes="FR"),
    CanonicalServer("Sulfuron",  "EU", "MoP Classic", notes="FR"),

    # ── US MoP Classic ───────────────────────────────────────────────────
    CanonicalServer("Angerforge",           "US", "MoP Classic"),
    CanonicalServer("Ashkandi",             "US", "MoP Classic"),
    CanonicalServer("Atiesh",               "US", "MoP Classic"),
    CanonicalServer("Azuresong",            "US", "MoP Classic"),
    CanonicalServer("Benediction",          "US", "MoP Classic"),
    CanonicalServer("Bloodsail Buccaneers", "US", "MoP Classic"),
    CanonicalServer("Earthfury",            "US", "MoP Classic"),
    CanonicalServer("Eranikus",             "US", "MoP Classic"),
    CanonicalServer("Faerlina",             "US", "MoP Classic"),
    CanonicalServer("Galakras",             "US", "MoP Classic"),
    CanonicalServer("Grobbulus",            "US", "MoP Classic"),
    CanonicalServer("Immerseus",            "US", "MoP Classic"),
    CanonicalServer("Lei Shen",             "US", "MoP Classic"),
    CanonicalServer("Maladath",             "US", "MoP Classic",
                    notes="US MoP (distinct from AU Anniversary Maladath)"),
    CanonicalServer("Mankrik",              "US", "MoP Classic"),
    CanonicalServer("Myzrael",              "US", "MoP Classic"),
    CanonicalServer("Nazgrim",              "US", "MoP Classic"),
    CanonicalServer("Old Blanchy",          "US", "MoP Classic"),
    CanonicalServer("Pagle",                "US", "MoP Classic"),
    CanonicalServer("Ra-den",               "US", "MoP Classic"),
    CanonicalServer("Skyfury",              "US", "MoP Classic"),
    CanonicalServer("Sulfuras",             "US", "MoP Classic"),
    CanonicalServer("Westfall",             "US", "MoP Classic"),
    CanonicalServer("Whitemane",            "US", "MoP Classic"),
    CanonicalServer("Windseeker",           "US", "MoP Classic"),

    # ── OCE MoP Classic (G2G: [OCE], FunPay chip 147) ────────────────────
    CanonicalServer("Arugal",  "OCE", "MoP Classic"),
    CanonicalServer("Remulos", "OCE", "MoP Classic"),
    CanonicalServer("Yojamba", "OCE", "MoP Classic"),

    # ── RU MoP Classic (FunPay chip 145 only) ────────────────────────────
    CanonicalServer("Chromie",  "RU", "MoP Classic"),
    CanonicalServer("Flamegor", "RU", "MoP Classic"),

    # ═══════════════════════════════════════════════════════════════════════
    # RETAIL (World of Warcraft: Midnight / The War Within)
    # G2G: seo_term=wow-gold, brand_id=lgc_game_2299
    # FunPay: chip 2 (EU + RU servers in Latin script), chip 25 (US + 3 OCE)
    # G2G sub-region brackets [FR/DE/ES/IT] → canonical region EU
    # G2G sub-region brackets [BR/LATAM] → canonical region US
    # RU servers appear on FunPay chip/2 in Latin script (e.g. "Gordunni")
    # OCE servers Aman'Thul/Barthilas/Frostmourne appear on FunPay chip/25
    # ═══════════════════════════════════════════════════════════════════════

    # ── EU Retail (248 servers) ──
    CanonicalServer("Aegwynn", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Aerie Peak", "EU", "Retail"),
    CanonicalServer("Agamaggan", "EU", "Retail"),
    CanonicalServer("Aggra", "EU", "Retail"),
    CanonicalServer("Aggramar", "EU", "Retail"),
    CanonicalServer("Ahn'Qiraj", "EU", "Retail"),
    CanonicalServer("Al'Akir", "EU", "Retail"),
    CanonicalServer("Alexstrasza", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Alleria", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Alonsus", "EU", "Retail"),
    CanonicalServer("Aman'Thul", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Ambossar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Anachronos", "EU", "Retail"),
    CanonicalServer("Anetheron", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Antonidas", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Anub'arak", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Arak-arahm", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Arathi", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Arathor", "EU", "Retail"),
    CanonicalServer("Archimonde", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Area 52", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Argent Dawn", "EU", "Retail"),
    CanonicalServer("Arthas", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Arygos", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Aszune", "EU", "Retail"),
    CanonicalServer("Auchindoun", "EU", "Retail"),
    CanonicalServer("Azjol-Nerub", "EU", "Retail"),
    CanonicalServer("Azshara", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Azuremyst", "EU", "Retail"),
    CanonicalServer("Baelgun", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Balnazzar", "EU", "Retail"),
    CanonicalServer("Blackhand", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Blackmoore", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Blackrock", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Blade's Edge", "EU", "Retail"),
    CanonicalServer("Bladefist", "EU", "Retail"),
    CanonicalServer("Bloodfeather", "EU", "Retail"),
    CanonicalServer("Bloodhoof", "EU", "Retail"),
    CanonicalServer("Bloodscalp", "EU", "Retail"),
    CanonicalServer("Blutkessel", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Boulderfist", "EU", "Retail"),
    CanonicalServer("Bronze Dragonflight", "EU", "Retail"),
    CanonicalServer("Bronzebeard", "EU", "Retail"),
    CanonicalServer("Burning Blade", "EU", "Retail"),
    CanonicalServer("Burning Legion", "EU", "Retail"),
    CanonicalServer("Burning Steppes", "EU", "Retail"),
    CanonicalServer("C'Thun", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Chamber of Aspects", "EU", "Retail"),
    CanonicalServer("Chants éternels", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Cho'gall", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Chromaggus", "EU", "Retail"),
    CanonicalServer("Colinas Pardas", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Confrérie du Thorium", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Conseil des Ombres", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Crushridge", "EU", "Retail"),
    CanonicalServer("Cultedela Rivenoire", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Daggerspine", "EU", "Retail"),
    CanonicalServer("Dalaran", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Dalvengyr", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Darkmoon Faire", "EU", "Retail"),
    CanonicalServer("Darksorrow", "EU", "Retail"),
    CanonicalServer("Darkspear", "EU", "Retail"),
    CanonicalServer("Das Konsortium", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Das Syndikat", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Deathwing", "EU", "Retail"),
    CanonicalServer("Defias Brotherhood", "EU", "Retail"),
    CanonicalServer("Dentarg", "EU", "Retail"),
    CanonicalServer("Der abyssische Rat", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Der Mithrilorden", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Der Rat von Dalaran", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Destromath", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Dethecus", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Die Aldor", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Die Arguswacht", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Die ewige Wacht", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Die Nachtwache", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Die Silberne Hand", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Die Todeskrallen", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Doomhammer", "EU", "Retail"),
    CanonicalServer("Draenor", "EU", "Retail"),
    CanonicalServer("Dragonblight", "EU", "Retail"),
    CanonicalServer("Dragonmaw", "EU", "Retail"),
    CanonicalServer("Drak'thul", "EU", "Retail"),
    CanonicalServer("Drek'Thar", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Dun Modr", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Dun Morogh", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Dunemaul", "EU", "Retail"),
    CanonicalServer("Durotan", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Earthen Ring", "EU", "Retail"),
    CanonicalServer("Echsenkessel", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Eitrigg", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Eldre'Thalas", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Elune", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Emerald Dream", "EU", "Retail"),
    CanonicalServer("Emeriss", "EU", "Retail"),
    CanonicalServer("Eonar", "EU", "Retail"),
    CanonicalServer("Eredar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Executus", "EU", "Retail"),
    CanonicalServer("Exodar", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Festung der Stürme", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Forscherliga", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Frostmane", "EU", "Retail"),
    CanonicalServer("Frostmourne", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Frostwhisper", "EU", "Retail"),
    CanonicalServer("Frostwolf", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Garona", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Garrosh", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Genjuros", "EU", "Retail"),
    CanonicalServer("Ghostlands", "EU", "Retail"),
    CanonicalServer("Gilneas", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Gorgonnash", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Grim Batol", "EU", "Retail"),
    CanonicalServer("Gul'dan", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Hakkar", "EU", "Retail"),
    CanonicalServer("Haomarush", "EU", "Retail"),
    CanonicalServer("Hellfire", "EU", "Retail"),
    CanonicalServer("Hellscream", "EU", "Retail"),
    CanonicalServer("Hyjal", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Illidan", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Jaedenar", "EU", "Retail"),
    CanonicalServer("Kael'Thas", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Karazhan", "EU", "Retail"),
    CanonicalServer("Kargath", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Kazzak", "EU", "Retail"),
    CanonicalServer("Kel'Thuzad", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Khadgar", "EU", "Retail"),
    CanonicalServer("Khaz Modan", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Khaz'goroth", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Kil'Jaeden", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Kilrogg", "EU", "Retail"),
    CanonicalServer("Kirin Tor", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Kor'gall", "EU", "Retail"),
    CanonicalServer("Krag'jin", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Krasus", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Kul Tiras", "EU", "Retail"),
    CanonicalServer("Kult  der Verdammten", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("La Croisade écarlate", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Laughing Skull", "EU", "Retail"),
    CanonicalServer("Les Clairvoyants", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Les Sentinelles", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Lightbringer", "EU", "Retail"),
    CanonicalServer("Lightning's Blade", "EU", "Retail"),
    CanonicalServer("Lordaeron", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Los Errantes", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Lothar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Madmortem", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Magtheridon", "EU", "Retail"),
    CanonicalServer("Mal'Ganis", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Malfurion", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Malorne", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Malygos", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Mannoroth", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Marécagede Zangar", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Mazrigos", "EU", "Retail"),
    CanonicalServer("Medivh", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Minahonda", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Moonglade", "EU", "Retail"),
    CanonicalServer("Mug'thol", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Nagrand", "EU", "Retail"),
    CanonicalServer("Nathrezim", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Naxxramas", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Nazjatar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Nefarian", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Nemesis", "EU", "Retail", notes="IT-localised"),
    CanonicalServer("Neptulon", "EU", "Retail"),
    CanonicalServer("Ner'zhul", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Nera'thor", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Nethersturm", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Nordrassil", "EU", "Retail"),
    CanonicalServer("Norgannon", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Nozdormu", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Onyxia", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Outland", "EU", "Retail"),
    CanonicalServer("Perenolde", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Pozzo dell'Eternità", "EU", "Retail", notes="IT-localised"),
    CanonicalServer("Proudmoore", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Quel'Thalas", "EU", "Retail"),
    CanonicalServer("Ragnaros", "EU", "Retail"),
    CanonicalServer("Rajaxx", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Rashgarroth", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Ravencrest", "EU", "Retail"),
    CanonicalServer("Ravenholdt", "EU", "Retail"),
    CanonicalServer("Rexxar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Runetotem", "EU", "Retail"),
    CanonicalServer("Sanguino", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Sargeras", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Saurfang", "EU", "Retail"),
    CanonicalServer("Scarshield Legion", "EU", "Retail"),
    CanonicalServer("Sen'jin", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Shadowsong", "EU", "Retail"),
    CanonicalServer("Shattered Halls", "EU", "Retail"),
    CanonicalServer("Shattered Hand", "EU", "Retail"),
    CanonicalServer("Shattrath", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Shen'dralar", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Silvermoon", "EU", "Retail"),
    CanonicalServer("Sinstralis", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Skullcrusher", "EU", "Retail"),
    CanonicalServer("Spinebreaker", "EU", "Retail"),
    CanonicalServer("Sporeggar", "EU", "Retail"),
    CanonicalServer("Steamwheedle Cartel", "EU", "Retail"),
    CanonicalServer("Stormrage", "EU", "Retail"),
    CanonicalServer("Stormreaver", "EU", "Retail"),
    CanonicalServer("Stormscale", "EU", "Retail"),
    CanonicalServer("Sunstrider", "EU", "Retail"),
    CanonicalServer("Suramar", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Sylvanas", "EU", "Retail"),
    CanonicalServer("Taerar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Talnivarr", "EU", "Retail"),
    CanonicalServer("Tarren Mill", "EU", "Retail"),
    CanonicalServer("Teldrassil", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Templenoir", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Terenas", "EU", "Retail"),
    CanonicalServer("Terokkar", "EU", "Retail"),
    CanonicalServer("Terrordar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("The Maelstrom", "EU", "Retail"),
    CanonicalServer("The Sha'tar", "EU", "Retail"),
    CanonicalServer("The Venture Co.", "EU", "Retail"),
    CanonicalServer("Theradras", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Thrall", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Throk'Feroth", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Thunderhorn", "EU", "Retail"),
    CanonicalServer("Tichondrius", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Tirion", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Todeswache", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Trollbane", "EU", "Retail"),
    CanonicalServer("Turalyon", "EU", "Retail"),
    CanonicalServer("Twilight's Hammer", "EU", "Retail"),
    CanonicalServer("Twisting Nether", "EU", "Retail"),
    CanonicalServer("Tyrande", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Uldaman", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Ulduar", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Uldum", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Un'Goro", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Varimathras", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Vashj", "EU", "Retail"),
    CanonicalServer("Vek'lor", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Vek'nilash", "EU", "Retail"),
    CanonicalServer("Vol'jin", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Well of Eternity", "EU", "Retail", notes="IT-localised"),
    CanonicalServer("Wildhammer", "EU", "Retail"),
    CanonicalServer("Wrathbringer", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Xavius", "EU", "Retail"),
    CanonicalServer("Ysera", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Ysondre", "EU", "Retail", notes="FR-localised"),
    CanonicalServer("Zenedar", "EU", "Retail"),
    CanonicalServer("Zirkel des Cenarius", "EU", "Retail", notes="DE-localised"),
    CanonicalServer("Zul'Jin", "EU", "Retail", notes="ES-localised"),
    CanonicalServer("Zuluhed", "EU", "Retail", notes="DE-localised"),

    # ── US Retail (234 servers) ──
    CanonicalServer("Aegwynn", "US", "Retail"),
    CanonicalServer("Aerie Peak", "US", "Retail"),
    CanonicalServer("Agamaggan", "US", "Retail"),
    CanonicalServer("Aggramar", "US", "Retail"),
    CanonicalServer("Akama", "US", "Retail"),
    CanonicalServer("Alexstrasza", "US", "Retail"),
    CanonicalServer("Alleria", "US", "Retail"),
    CanonicalServer("Altar of Storms", "US", "Retail"),
    CanonicalServer("Alterac Mountains", "US", "Retail"),
    CanonicalServer("Andorhal", "US", "Retail"),
    CanonicalServer("Anetheron", "US", "Retail"),
    CanonicalServer("Antonidas", "US", "Retail"),
    CanonicalServer("Anub'arak", "US", "Retail"),
    CanonicalServer("Anvilmar", "US", "Retail"),
    CanonicalServer("Arathor", "US", "Retail"),
    CanonicalServer("Archimonde", "US", "Retail"),
    CanonicalServer("Area 52", "US", "Retail"),
    CanonicalServer("Argent Dawn", "US", "Retail"),
    CanonicalServer("Arthas", "US", "Retail"),
    CanonicalServer("Arygos", "US", "Retail"),
    CanonicalServer("Auchindoun", "US", "Retail"),
    CanonicalServer("Azgalor", "US", "Retail"),
    CanonicalServer("Azjol-Nerub", "US", "Retail"),
    CanonicalServer("Azralon", "US", "Retail", notes="BR-localised"),
    CanonicalServer("Azshara", "US", "Retail"),
    CanonicalServer("Azuremyst", "US", "Retail"),
    CanonicalServer("Baelgun", "US", "Retail"),
    CanonicalServer("Balnazzar", "US", "Retail"),
    CanonicalServer("Black Dragonflight", "US", "Retail"),
    CanonicalServer("Blackhand", "US", "Retail"),
    CanonicalServer("Blackrock", "US", "Retail"),
    CanonicalServer("Blackwater Raiders", "US", "Retail"),
    CanonicalServer("Blackwing Lair", "US", "Retail"),
    CanonicalServer("Blade's Edge", "US", "Retail"),
    CanonicalServer("Bladefist", "US", "Retail"),
    CanonicalServer("Bleeding Hollow", "US", "Retail"),
    CanonicalServer("Blood Furnace", "US", "Retail"),
    CanonicalServer("Bloodhoof", "US", "Retail"),
    CanonicalServer("Bloodscalp", "US", "Retail"),
    CanonicalServer("Bonechewer", "US", "Retail"),
    CanonicalServer("Borean Tundra", "US", "Retail"),
    CanonicalServer("Boulderfist", "US", "Retail"),
    CanonicalServer("Bronzebeard", "US", "Retail"),
    CanonicalServer("Burning Blade", "US", "Retail"),
    CanonicalServer("Burning Legion", "US", "Retail"),
    CanonicalServer("Cairne", "US", "Retail"),
    CanonicalServer("Cenarion Circle", "US", "Retail"),
    CanonicalServer("Cenarius", "US", "Retail"),
    CanonicalServer("Cho'gall", "US", "Retail"),
    CanonicalServer("Chromaggus", "US", "Retail"),
    CanonicalServer("Coilfang", "US", "Retail"),
    CanonicalServer("Crushridge", "US", "Retail"),
    CanonicalServer("Daggerspine", "US", "Retail"),
    CanonicalServer("Dalaran", "US", "Retail"),
    CanonicalServer("Dalvengyr", "US", "Retail"),
    CanonicalServer("Dark Iron", "US", "Retail"),
    CanonicalServer("Darkspear", "US", "Retail"),
    CanonicalServer("Darrowmere", "US", "Retail"),
    CanonicalServer("Dawnbringer", "US", "Retail"),
    CanonicalServer("Deathwing", "US", "Retail"),
    CanonicalServer("Demon Soul", "US", "Retail"),
    CanonicalServer("Dentarg", "US", "Retail"),
    CanonicalServer("Destromath", "US", "Retail"),
    CanonicalServer("Dethecus", "US", "Retail"),
    CanonicalServer("Detheroc", "US", "Retail"),
    CanonicalServer("Doomhammer", "US", "Retail"),
    CanonicalServer("Draenor", "US", "Retail"),
    CanonicalServer("Dragonblight", "US", "Retail"),
    CanonicalServer("Dragonmaw", "US", "Retail"),
    CanonicalServer("Drak'Tharon", "US", "Retail"),
    CanonicalServer("Drak'thul", "US", "Retail"),
    CanonicalServer("Draka", "US", "Retail"),
    CanonicalServer("Drakkari", "US", "Retail", notes="LATAM-localised"),
    CanonicalServer("Drenden", "US", "Retail"),
    CanonicalServer("Dunemaul", "US", "Retail"),
    CanonicalServer("Durotan", "US", "Retail"),
    CanonicalServer("Duskwood", "US", "Retail"),
    CanonicalServer("Earthen Ring", "US", "Retail"),
    CanonicalServer("Echo Isles", "US", "Retail"),
    CanonicalServer("Eitrigg", "US", "Retail"),
    CanonicalServer("Eldre'Thalas", "US", "Retail"),
    CanonicalServer("Elune", "US", "Retail"),
    CanonicalServer("Emerald Dream", "US", "Retail"),
    CanonicalServer("Eonar", "US", "Retail"),
    CanonicalServer("Eredar", "US", "Retail"),
    CanonicalServer("Executus", "US", "Retail"),
    CanonicalServer("Exodar", "US", "Retail"),
    CanonicalServer("Farstriders", "US", "Retail"),
    CanonicalServer("Feathermoon", "US", "Retail"),
    CanonicalServer("Fenris", "US", "Retail"),
    CanonicalServer("Firetree", "US", "Retail"),
    CanonicalServer("Fizzcrank", "US", "Retail"),
    CanonicalServer("Frostmane", "US", "Retail"),
    CanonicalServer("Frostwolf", "US", "Retail"),
    CanonicalServer("Galakrond", "US", "Retail"),
    CanonicalServer("Gallywix", "US", "Retail", notes="BR-localised"),
    CanonicalServer("Garithos", "US", "Retail"),
    CanonicalServer("Garona", "US", "Retail"),
    CanonicalServer("Garrosh", "US", "Retail"),
    CanonicalServer("Ghostlands", "US", "Retail"),
    CanonicalServer("Gilneas", "US", "Retail"),
    CanonicalServer("Gnomeregan", "US", "Retail"),
    CanonicalServer("Goldrinn", "US", "Retail", notes="BR-localised"),
    CanonicalServer("Gorefiend", "US", "Retail"),
    CanonicalServer("Gorgonnash", "US", "Retail"),
    CanonicalServer("Greymane", "US", "Retail"),
    CanonicalServer("Grizzly Hills", "US", "Retail"),
    CanonicalServer("Gul'dan", "US", "Retail"),
    CanonicalServer("Gurubashi", "US", "Retail"),
    CanonicalServer("Hakkar", "US", "Retail"),
    CanonicalServer("Haomarush", "US", "Retail"),
    CanonicalServer("Hellscream", "US", "Retail"),
    CanonicalServer("Hydraxis", "US", "Retail"),
    CanonicalServer("Hyjal", "US", "Retail"),
    CanonicalServer("Icecrown", "US", "Retail"),
    CanonicalServer("Illidan", "US", "Retail"),
    CanonicalServer("Jaedenar", "US", "Retail"),
    CanonicalServer("Kael'thas", "US", "Retail"),
    CanonicalServer("Kalecgos", "US", "Retail"),
    CanonicalServer("Kargath", "US", "Retail"),
    CanonicalServer("Kel'Thuzad", "US", "Retail"),
    CanonicalServer("Khadgar", "US", "Retail"),
    CanonicalServer("Khaz Modan", "US", "Retail"),
    CanonicalServer("Kil'Jaeden", "US", "Retail"),
    CanonicalServer("Kilrogg", "US", "Retail"),
    CanonicalServer("Kirin Tor", "US", "Retail"),
    CanonicalServer("Korgath", "US", "Retail"),
    CanonicalServer("Korialstrasz", "US", "Retail"),
    CanonicalServer("KulTiras", "US", "Retail"),
    CanonicalServer("Laughing Skull", "US", "Retail"),
    CanonicalServer("Lethon", "US", "Retail"),
    CanonicalServer("Lightbringer", "US", "Retail"),
    CanonicalServer("Lightning's Blade", "US", "Retail"),
    CanonicalServer("Lightninghoof", "US", "Retail"),
    CanonicalServer("Llane", "US", "Retail"),
    CanonicalServer("Lothar", "US", "Retail"),
    CanonicalServer("Madoran", "US", "Retail"),
    CanonicalServer("Maelstrom", "US", "Retail"),
    CanonicalServer("Magtheridon", "US", "Retail"),
    CanonicalServer("Maiev", "US", "Retail"),
    CanonicalServer("Mal'Ganis", "US", "Retail"),
    CanonicalServer("Malfurion", "US", "Retail"),
    CanonicalServer("Malorne", "US", "Retail"),
    CanonicalServer("Malygos", "US", "Retail"),
    CanonicalServer("Mannoroth", "US", "Retail"),
    CanonicalServer("Medivh", "US", "Retail"),
    CanonicalServer("Misha", "US", "Retail"),
    CanonicalServer("Mok'Nathal", "US", "Retail"),
    CanonicalServer("Moon Guard", "US", "Retail"),
    CanonicalServer("Moonrunner", "US", "Retail"),
    CanonicalServer("Mug'thol", "US", "Retail"),
    CanonicalServer("Muradin", "US", "Retail"),
    CanonicalServer("Nathrezim", "US", "Retail"),
    CanonicalServer("Nazgrel", "US", "Retail"),
    CanonicalServer("Nazjatar", "US", "Retail"),
    CanonicalServer("Nemesis", "US", "Retail", notes="BR-localised"),
    CanonicalServer("Ner'zhul", "US", "Retail"),
    CanonicalServer("Nesingwary", "US", "Retail"),
    CanonicalServer("Nordrassil", "US", "Retail"),
    CanonicalServer("Norgannon", "US", "Retail"),
    CanonicalServer("Onyxia", "US", "Retail"),
    CanonicalServer("Perenolde", "US", "Retail"),
    CanonicalServer("Proudmoore", "US", "Retail"),
    CanonicalServer("Quel'dorei", "US", "Retail"),
    CanonicalServer("Quel’Thalas", "US", "Retail", notes="LATAM-localised"),
    CanonicalServer("Ragnaros", "US", "Retail", notes="LATAM-localised"),
    CanonicalServer("Ravencrest", "US", "Retail"),
    CanonicalServer("Ravenholdt", "US", "Retail"),
    CanonicalServer("Rexxar", "US", "Retail"),
    CanonicalServer("Rivendare", "US", "Retail"),
    CanonicalServer("Runetotem", "US", "Retail"),
    CanonicalServer("Sargeras", "US", "Retail"),
    CanonicalServer("Scarlet Crusade", "US", "Retail"),
    CanonicalServer("Scilla", "US", "Retail"),
    CanonicalServer("Sen'Jin", "US", "Retail"),
    CanonicalServer("Sentinels", "US", "Retail"),
    CanonicalServer("Shadow Council", "US", "Retail"),
    CanonicalServer("Shadowmoon", "US", "Retail"),
    CanonicalServer("Shadowsong", "US", "Retail"),
    CanonicalServer("Shandris", "US", "Retail"),
    CanonicalServer("Shattered Halls", "US", "Retail"),
    CanonicalServer("Shattered Hand", "US", "Retail"),
    CanonicalServer("Shu'halo", "US", "Retail"),
    CanonicalServer("Silver Hand", "US", "Retail"),
    CanonicalServer("Silvermoon", "US", "Retail"),
    CanonicalServer("Sisters of Elune", "US", "Retail"),
    CanonicalServer("Skullcrusher", "US", "Retail"),
    CanonicalServer("Skywall", "US", "Retail"),
    CanonicalServer("Smolderthorn", "US", "Retail"),
    CanonicalServer("Spinebreaker", "US", "Retail"),
    CanonicalServer("Spirestone", "US", "Retail"),
    CanonicalServer("Staghelm", "US", "Retail"),
    CanonicalServer("Steamwheedle Cartel", "US", "Retail"),
    CanonicalServer("Stonemaul", "US", "Retail"),
    CanonicalServer("Stormrage", "US", "Retail"),
    CanonicalServer("Stormreaver", "US", "Retail"),
    CanonicalServer("Stormscale", "US", "Retail"),
    CanonicalServer("Suramar", "US", "Retail"),
    CanonicalServer("Tanaris", "US", "Retail"),
    CanonicalServer("Terenas", "US", "Retail"),
    CanonicalServer("Terokkar", "US", "Retail"),
    CanonicalServer("The Forgotten Coast", "US", "Retail"),
    CanonicalServer("The Scryers", "US", "Retail"),
    CanonicalServer("The Underbog", "US", "Retail"),
    CanonicalServer("The Venture Co", "US", "Retail"),
    CanonicalServer("Thorium Brotherhood", "US", "Retail"),
    CanonicalServer("Thrall", "US", "Retail"),
    CanonicalServer("Thunderhorn", "US", "Retail"),
    CanonicalServer("Thunderlord", "US", "Retail"),
    CanonicalServer("Tichondrius", "US", "Retail"),
    CanonicalServer("Tol Barad", "US", "Retail", notes="BR-localised"),
    CanonicalServer("Tortheldrin", "US", "Retail"),
    CanonicalServer("Trollbane", "US", "Retail"),
    CanonicalServer("Turalyon", "US", "Retail"),
    CanonicalServer("Twisting Nether", "US", "Retail"),
    CanonicalServer("Uldaman", "US", "Retail"),
    CanonicalServer("Uldum", "US", "Retail"),
    CanonicalServer("Undermine", "US", "Retail"),
    CanonicalServer("Ursin", "US", "Retail"),
    CanonicalServer("Uther", "US", "Retail"),
    CanonicalServer("Vashj", "US", "Retail"),
    CanonicalServer("Vek'nilash", "US", "Retail"),
    CanonicalServer("Velen", "US", "Retail"),
    CanonicalServer("Warsong", "US", "Retail"),
    CanonicalServer("Whisperwind", "US", "Retail"),
    CanonicalServer("WildHammer", "US", "Retail"),
    CanonicalServer("Windrunner", "US", "Retail"),
    CanonicalServer("Winterhoof", "US", "Retail"),
    CanonicalServer("Wyrmrest Accord", "US", "Retail"),
    CanonicalServer("Ysera", "US", "Retail"),
    CanonicalServer("Ysondre", "US", "Retail"),
    CanonicalServer("Zangarmarsh", "US", "Retail"),
    CanonicalServer("Zul'jin", "US", "Retail"),
    CanonicalServer("Zuluhed", "US", "Retail"),

    # ── OCE Retail (12 servers) ──
    # G2G: bracket [OCE]. FunPay: Aman'Thul/Barthilas/Frostmourne on chip/25.
    # canonical region = "OCE" even though FunPay chip/25 stamps "(US) Retail"
    CanonicalServer("Aman'Thul", "OCE", "Retail"),
    CanonicalServer("Barthilas", "OCE", "Retail"),
    CanonicalServer("Caelestrasz", "OCE", "Retail"),
    CanonicalServer("Dath'Remar", "OCE", "Retail"),
    CanonicalServer("Dreadmaul", "OCE", "Retail"),
    CanonicalServer("Frostmourne", "OCE", "Retail"),
    CanonicalServer("Gundrak", "OCE", "Retail"),
    CanonicalServer("Jubei'Thos", "OCE", "Retail"),
    CanonicalServer("Khaz'goroth", "OCE", "Retail"),
    CanonicalServer("Nagrand", "OCE", "Retail"),
    CanonicalServer("Saurfang", "OCE", "Retail"),
    CanonicalServer("Thaurissan", "OCE", "Retail"),

    # ── RU Retail (20 servers) ──
    # G2G: Cyrillic title with English name in parens, e.g. "Гордунни (Gordunni)"
    # FunPay: Latin script on chip/2 (EU chip), e.g. "Gordunni"
    # canonical region = "RU"; FunPay chip/2 stamps "(EU) Retail" → cross-region
    # aliases handled in migration 019
    CanonicalServer("Ashenvale", "RU", "Retail"),
    CanonicalServer("Azuregos", "RU", "Retail"),
    CanonicalServer("Blackscar", "RU", "Retail"),
    CanonicalServer("Booty Bay", "RU", "Retail"),
    CanonicalServer("Borean Tundra", "RU", "Retail"),
    CanonicalServer("Deathguard", "RU", "Retail"),
    CanonicalServer("Deathweaver", "RU", "Retail"),
    CanonicalServer("Deephome", "RU", "Retail"),
    CanonicalServer("Eversong", "RU", "Retail"),
    CanonicalServer("Fordragon", "RU", "Retail"),
    CanonicalServer("Galakrond", "RU", "Retail"),
    CanonicalServer("Goldrinn", "RU", "Retail"),
    CanonicalServer("Gordunni", "RU", "Retail"),
    CanonicalServer("Greymane", "RU", "Retail"),
    CanonicalServer("Grom", "RU", "Retail"),
    CanonicalServer("Howling Fjord", "RU", "Retail"),
    CanonicalServer("Lich King", "RU", "Retail"),
    CanonicalServer("Razuvious", "RU", "Retail"),
    CanonicalServer("Soulflayer", "RU", "Retail"),
    CanonicalServer("Thermaplugg", "RU", "Retail"),
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

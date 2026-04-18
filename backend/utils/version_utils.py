from __future__ import annotations

# ── DEPRECATED: REALM_REGION_OVERRIDE ────────────────────────────────────────
# Previously used in g2g_parser._parse_title() to correct AU realms misfiled
# by G2G under EU/US region buckets.
#
# SUPERSEDED by canonical server registry (migration 010 + db/canonical_servers.py):
#   • Penance    → canonical region "AU" in servers table
#   • Shadowstrike → canonical region "AU" in servers table
#   • Maladath   → canonical region "AU" in servers table
#
# Region overrides now happen in normalize_pipeline._apply_canonical() by
# reading the canonical servers table. The raw source region is ignored.
# Wrong-region events are logged as "wrong_region_overridden" (informational).
#
# This dict is retained here only for reference and backward compatibility.
# DO NOT import or use in parsers — parsers must not make region decisions.
REALM_REGION_OVERRIDE: dict[str, tuple[str, str]] = {
    # DEPRECATED — kept for historical reference only
    "penance":      ("AU", "Season of Discovery"),
    "shadowstrike": ("AU", "Season of Discovery"),
}

_VERSION_ALIASES: dict[str, str] = {
    # Season of Discovery variants
    "seasonal":            "Season of Discovery",
    "season of discovery": "Season of Discovery",
    "sod":                 "Season of Discovery",
    # Anniversary variants (Task 3D)
    "anniversary":         "Anniversary",
    "classic anniversary": "Anniversary",
    "anniversary gold":    "Anniversary",
    # Hardcore
    "hardcore":            "Hardcore",
    # Classic Era variants → canonical "Classic"
    "classic era":         "Classic",
    "vanilla":             "Classic",
    "era":                 "Classic",
    # Plain "Classic" (G2G uses this in title brackets for Era servers)
    "classic":             "Classic",
    # TBC Classic variants
    "tbc classic":         "TBC Classic",
    "tbc":                 "TBC Classic",
    "burning crusade":     "TBC Classic",
}


def _canonicalize_version(version: str) -> str:
    return _VERSION_ALIASES.get((version or "").lower().strip(), version)

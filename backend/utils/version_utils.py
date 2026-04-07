from __future__ import annotations

# Realms whose region in the G2G API response does not match their actual region.
# G2G places certain AU realms under EU/US region buckets; this table corrects that.
# Key: realm name (lowercase). Value: (correct_region, correct_version)
REALM_REGION_OVERRIDE: dict[str, tuple[str, str]] = {
    "penance":      ("AU", "Season of Discovery"),
    "shadowstrike": ("AU", "Season of Discovery"),
}

_VERSION_ALIASES: dict[str, str] = {
    "seasonal": "Season of Discovery",
    "season of discovery": "Season of Discovery",
    "sod": "Season of Discovery",
    "hardcore": "Hardcore",
    "anniversary": "Anniversary",
    "classic era": "Classic Era",
    "classic": "Classic",
}


def _canonicalize_version(version: str) -> str:
    return _VERSION_ALIASES.get((version or "").lower().strip(), version)

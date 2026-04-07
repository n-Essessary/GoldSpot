from __future__ import annotations

_VERSION_ALIASES: dict[str, str] = {
    "seasonal": "Season of Discovery",
    "season of discovery": "Season of Discovery",
    "sod": "Season of Discovery",
    "anniversary": "Anniversary",
    "classic era": "Classic Era",
    "classic": "Classic",
}


def _canonicalize_version(version: str) -> str:
    return _VERSION_ALIASES.get((version or "").lower().strip(), version)

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from db import server_resolver as sr


SAMPLE_TITLES = [
    "Remulos [OCE - Classic] - Alliance",
    "Felstriker [OCE - Classic] - Horde",
    "Arugal [OCE - Classic] - Alliance",
    "Chromie [RU - Classic] - Horde",
    "Rhok'delar [RU - Classic] - Alliance",
    "Wyrmthalak [RU - Classic] - Horde",
    "Shadowstrike [RU - Season of Discovery] - Alliance",
    "Penance [RU - Season of Discovery] - Horde",
]


def _extract_servers_from_migrations() -> set[tuple[str, str, str]]:
    versions_dir = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"
    servers: set[tuple[str, str, str]] = set()
    for p in sorted(versions_dir.glob("*.py")):
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            target_name = None
            value_node = None
            if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
                value_node = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target_name = node.target.id
                value_node = node.value
            if target_name == "_SERVERS" and value_node is not None and isinstance(value_node, (ast.List, ast.Tuple)):
                for row in ast.literal_eval(value_node):
                    servers.add((row[0], row[1], row[2]))
    return servers


@pytest.mark.asyncio
async def test_batch3_servers_resolve_without_warning(monkeypatch, caplog):
    servers = _extract_servers_from_migrations()
    alias_map: dict[str, int] = {}
    sid = 3000
    for name, region, version in sorted(servers):
        for faction in ("Alliance", "Horde"):
            alias_map[f"{name} [{region} - {version}] - {faction}".lower()] = sid
        sid += 1

    monkeypatch.setattr(sr, "_alias_cache", alias_map)
    monkeypatch.setattr(sr, "_cache_loaded_at", 10**9)

    caplog.clear()
    caplog.set_level("WARNING")

    for title in SAMPLE_TITLES:
        resolved = await sr.resolve_server(title, "g2g", pool=object())
        assert resolved is not None

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []


from __future__ import annotations

import ast
from pathlib import Path

import pytest

from db import server_resolver as sr


KNOWN_TITLES = [
    "Flamelash [EU - Classic] - Alliance",
    "Stonespine [EU - Classic] - Alliance",
    "Ten Storms [EU - Classic] - Horde",
    "Razorgore [EU - Classic] - Alliance",
    "Judgement [EU - Classic] - Alliance",
    "Judgement [EU - Classic] - Horde",
    "Flamegor [RU - Classic] - Alliance",
    "Flamegor [RU - Classic] - Horde",
    "Harbinger of Doom [RU - Classic] - Horde",
]


def _extract_aliases_from_migrations() -> dict[str, int]:
    versions_dir = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"
    alias_map: dict[str, int] = {}
    sid = 1000
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

            if (
                target_name == "_ALIASES"
                and value_node is not None
                and isinstance(value_node, (ast.List, ast.Tuple))
            ):
                aliases = ast.literal_eval(value_node)
                for row in aliases:
                    alias = row[0]
                    if alias.lower() not in alias_map:
                        alias_map[alias.lower()] = sid
                        sid += 1
    return alias_map


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
            if target_name == "_SERVERS" and value_node is not None:
                for row in ast.literal_eval(value_node):
                    servers.add((row[0], row[1], row[2]))
    return servers


def test_ru_servers_present_in_canonical_servers_seed():
    servers = _extract_servers_from_migrations()
    assert ("Flamegor", "RU", "Classic") in servers
    assert ("Harbinger of Doom", "RU", "Classic") in servers


@pytest.mark.asyncio
async def test_known_servers_resolve_without_warning(monkeypatch, caplog):
    aliases = _extract_aliases_from_migrations()
    monkeypatch.setattr(sr, "_alias_cache", aliases)
    monkeypatch.setattr(sr, "_cache_loaded_at", 10**9)

    caplog.clear()
    caplog.set_level("WARNING")

    for title in KNOWN_TITLES:
        sid = await sr.resolve_server(title, "g2g", pool=object())
        assert sid is not None

    warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert not warning_msgs

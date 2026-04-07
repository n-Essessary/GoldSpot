from __future__ import annotations

import ast
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "backend/alembic/versions/002_seed_servers.py"
)


def _extract_seed_data():
    src = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(src)
    servers = []
    aliases = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "_SERVERS":
                servers = ast.literal_eval(node.value)
            elif name == "_ALIASES":
                aliases = ast.literal_eval(node.value)
    return servers, aliases


def test_servers_have_unique_name_region_version():
    servers, _ = _extract_seed_data()
    assert len(servers) == len(set(servers))


def test_servers_have_region_and_version():
    servers, _ = _extract_seed_data()
    for name, region, version in servers:
        assert name.strip()
        assert region.strip()
        assert version.strip()


def test_alias_points_to_single_canonical_server():
    _, aliases = _extract_seed_data()
    target_by_alias = {}
    for alias, name, region, version, _source in aliases:
        target = (name, region, version)
        prev = target_by_alias.setdefault(alias.lower(), target)
        assert prev == target

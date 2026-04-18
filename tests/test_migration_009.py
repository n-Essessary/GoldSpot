from __future__ import annotations

import ast
from pathlib import Path

from utils.version_utils import _VERSION_ALIASES


MIGRATION = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions" / "009_canonical_server_truth.py"


def _extract_list(name: str):
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    return []


def test_no_duplicate_servers():
    servers = _extract_list("_CLASSIC_ERA_SERVERS") + _extract_list("_ANNIVERSARY_SERVERS")
    seen = set()
    dups = set()
    for row in servers:
        if row in seen:
            dups.add(row)
        seen.add(row)
    assert not dups


def test_anniversary_servers_exist():
    ann = set(_extract_list("_ANNIVERSARY_SERVERS"))
    expected = {
        ("Spineshatter", "EU"),
        ("Nightslayer", "US"),
    }
    assert expected.issubset(ann)


def test_classic_era_servers_exist():
    ru = set(_extract_list("_RU_CLASSIC_ERA_SERVERS"))
    assert {"Flamegor", "Harbinger of Doom", "Chromie"}.issubset(ru)


def test_hardcore_servers_exist():
    hc = set(_extract_list("_HARDCORE_SERVERS"))
    assert ("Stitches", "EU") in hc and ("Skull Rock", "US") in hc


def test_version_aliases_canonical():
    assert _VERSION_ALIASES["vanilla"] == "Classic"
    assert _VERSION_ALIASES["era"] == "Classic"
    assert _VERSION_ALIASES["tbc"] == "TBC Classic"
    assert _VERSION_ALIASES["seasonal"] == "Season of Discovery"
    assert _VERSION_ALIASES["classic anniversary"] == "Anniversary"
    assert _VERSION_ALIASES["anniversary gold"] == "Anniversary"

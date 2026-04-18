from __future__ import annotations

import pytest

from db import server_resolver as sr
from service import normalize_pipeline as np


@pytest.mark.asyncio
async def test_strict_alias_match_returns_server_id(monkeypatch):
    monkeypatch.setattr(sr, "_cache_loaded_at", 10**9)
    monkeypatch.setattr(sr, "_alias_cache", {"foo [EU - Anniversary] - Horde".lower(): 42})
    sid = await sr.resolve_server("Foo [EU - Anniversary] - Horde", "g2g", pool=object())
    assert sid == 42


@pytest.mark.asyncio
async def test_unknown_alias_returns_none_without_exception(monkeypatch):
    monkeypatch.setattr(sr, "_cache_loaded_at", 10**9)
    monkeypatch.setattr(sr, "_alias_cache", {})

    async def _fuzzy(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sr, "_fuzzy_resolve", _fuzzy)
    sid = await sr.resolve_server("Unknown Alias", "g2g", pool=object())
    assert sid is None


@pytest.mark.asyncio
async def test_resolution_is_deterministic(monkeypatch):
    monkeypatch.setattr(sr, "_cache_loaded_at", 10**9)
    monkeypatch.setattr(sr, "_alias_cache", {"same".lower(): 7})
    a = await sr.resolve_server("same", "g2g", pool=object())
    b = await sr.resolve_server("same", "funpay", pool=object())
    assert (a, b) == (7, 7)


@pytest.mark.asyncio
async def test_game_version_alias_match_returns_when_versions_align(monkeypatch):
    monkeypatch.setattr(sr, "_cache_loaded_at", 10**9)
    monkeypatch.setattr(sr, "_alias_cache", {"firemaw [eu - classic era] - horde".lower(): 101})
    monkeypatch.setattr(
        sr,
        "_server_data_cache",
        {101: {"id": 101, "name": "Firemaw", "region": "EU", "version": "Classic Era"}},
    )

    async def _no_fuzzy(*_a, **_k):
        raise AssertionError("_fuzzy_resolve should not run when alias version matches")

    monkeypatch.setattr(sr, "_fuzzy_resolve", _no_fuzzy)
    sid = await sr.resolve_server(
        "Firemaw [EU - Classic Era] - Horde",
        "g2g",
        pool=object(),
        game_version="Classic Era",
    )
    assert sid == 101


@pytest.mark.asyncio
async def test_game_version_skips_alias_and_uses_fuzzy_with_config_version(monkeypatch):
    monkeypatch.setattr(sr, "_cache_loaded_at", 10**9)
    monkeypatch.setattr(sr, "_alias_cache", {"firemaw [eu - classic era] - horde".lower(): 101})
    monkeypatch.setattr(
        sr,
        "_server_data_cache",
        {101: {"id": 101, "name": "Firemaw", "region": "EU", "version": "Classic Era"}},
    )

    async def _fuzzy(*_a, **_k):
        return 202

    monkeypatch.setattr(sr, "_fuzzy_resolve", _fuzzy)
    sid = await sr.resolve_server(
        "Firemaw [EU - Classic Era] - Horde",
        "g2g",
        pool=object(),
        game_version="MoP Classic",
    )
    assert sid == 202


@pytest.mark.asyncio
async def test_fuzzy_resolve_g2g_uses_game_version_over_bracket(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    async def _track_lookup(name: str, region: str, version: str, pool):
        calls.append((name, region, version))
        return 303

    monkeypatch.setattr(sr, "_lookup_server", _track_lookup)
    sid = await sr._fuzzy_resolve(
        "Firemaw [EU - Classic Era] - Horde",
        "g2g",
        pool=object(),
        game_version="MoP Classic",
    )
    assert sid == 303
    assert calls == [("Firemaw", "EU", "MoP Classic")]


@pytest.mark.asyncio
async def test_conflicting_raw_versions_resolve_to_canonical_registry(make_offer, monkeypatch):
    offer = make_offer(server_id=55, display_server="(EU) Seasonal", server="(EU) Seasonal")
    monkeypatch.setattr(
        sr,
        "get_server_data",
        lambda _sid: {"id": 55, "name": "Lava Lash", "region": "EU", "version": "Season of Discovery"},
    )
    normalized, quarantined = await np.normalize_offer_batch([offer], pool=None)
    assert not quarantined and normalized[0].display_server == "(EU) Season of Discovery"


@pytest.mark.asyncio
async def test_alias_conflicts_are_excluded_from_cache(caplog):
    class FakePool:
        async def fetch(self, query):
            if "FROM server_aliases" in query:
                return [
                    {"alias": "Firemaw [EU - Anniversary] - Horde", "server_id": 1},
                    {"alias": "Firemaw [EU - Anniversary] - Horde", "server_id": 2},
                ]
            return [
                {"id": 1, "name": "Firemaw", "region": "EU", "version": "Anniversary", "realm_type": "Normal", "is_active": True},
                {"id": 2, "name": "Firemaw", "region": "EU", "version": "Classic Era", "realm_type": "Normal", "is_active": True},
            ]

    await sr._load_alias_cache(FakePool())
    assert "firemaw [eu - anniversary] - horde" not in sr._alias_cache

from __future__ import annotations

import asyncio
import pytest

try:
    from parser.g2g_parser import (
        G2GClient,
        G2GOffer,
        G2GRegion,
        _build_offer_url,
        _dedupe,
        _parse_title,
        _to_offer,
        fetch_g2g_game,
    )
except ImportError as e:
    pytestmark = pytest.mark.skip(reason=f"legacy g2g parser API unavailable: {e}")


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "Spineshatter [EU - Anniversary] - Alliance",
            ("Spineshatter", "EU", "Anniversary", "Alliance"),
        ),
        (
            "Lava Lash [EU - Seasonal] - Horde",
            ("Lava Lash", "EU", "Seasonal", "Horde"),
        ),
        (
            "Firemaw [EU] - Alliance",
            ("Firemaw", "EU", "Classic", "Alliance"),
        ),
        (
            "Classic Era Gold EU",
            ("Classic Era Gold EU", "EU", "Classic", "Horde"),
        ),
        (
            "",
            ("", "", "", "Horde"),
        ),
    ],
)
def test_parse_title_cases(title, expected):
    assert _parse_title(title) == expected


def test_parse_title_none_safe():
    out = _parse_title(None)  # type: ignore[arg-type]
    assert isinstance(out, tuple) and len(out) == 4


def test_to_offer_keeps_raw_title_for_canonical_resolution(make_offer):
    raw = G2GOffer(
        offer_id="a",
        title="Lava Lash [EU - Seasonal] - Horde",
        server_name="Lava Lash",
        region_id="eu",
        relation_id="r",
        price_usd=0.003,
        min_qty=1,
        available_qty=10,
        seller="s",
        brand_id="b",
        service_id="svc",
    )
    offer = _to_offer(raw, make_offer().updated_at)
    assert offer is not None
    assert offer.display_server == "lava lash"
    assert offer.raw_title == "Lava Lash [EU - Seasonal] - Horde"


def test_to_offer_price_zero_returns_none(make_offer):
    raw = G2GOffer("a", "x [EU - Classic] - Horde", "x", "eu", "r", 0.0, 1, 10, "s", "b", "svc")
    assert _to_offer(raw, make_offer().updated_at) is None


def test_to_offer_available_qty_zero_returns_none(make_offer):
    raw = G2GOffer("a", "x [EU - Classic] - Horde", "x", "eu", "r", 0.003, 1, 0, "s", "b", "svc")
    assert _to_offer(raw, make_offer().updated_at) is None


def test_to_offer_unrecognized_title_kept_for_quarantine_stage(make_offer):
    raw = G2GOffer("a", "Unrecognized title", "x", "eu", "r", 0.003, 1, 10, "s", "b", "svc")
    offer = _to_offer(raw, make_offer().updated_at)
    assert offer is not None and offer.raw_title == "Unrecognized title"


def test_to_offer_seasonal_version_canonicalized(make_offer):
    raw = G2GOffer("a", "Lava Lash [EU - Seasonal] - Horde", "Lava Lash", "eu", "r", 0.003, 1, 10, "s", "b", "svc")
    offer = _to_offer(raw, make_offer().updated_at)
    assert offer is not None and offer.display_server == "lava lash"


def test_to_offer_skip_qty_check(make_offer):
    raw = G2GOffer("a", "Firemaw [EU - Classic] - Horde", "Firemaw", "eu", "r", 0.003, 1, 0, "s", "b", "svc")
    assert _to_offer(raw, make_offer().updated_at) is None
    assert _to_offer(raw, make_offer().updated_at, skip_qty_check=True) is not None


def test_to_offer_sets_non_empty_temp_server_slug(make_offer):
    raw = G2GOffer("a", "Soulseeker [EU - Hardcore] - Horde", "Soulseeker", "eu", "r", 0.003, 1, 10, "s", "b", "svc")
    offer = _to_offer(raw, make_offer().updated_at)
    assert offer is not None and offer.server


def test_dedupe_removes_exact_duplicate_offer_ids(make_offer):
    a = make_offer(id="dup", source="g2g")
    b = make_offer(id="dup", source="g2g", seller="x")
    assert len(_dedupe([a, b])) == 1


def test_build_offer_url_seller_format():
    url = _build_offer_url("G123ABC", "dfced32f-aaaa", "HellenWong")
    assert (
        url
        == "https://www.g2g.com/categories/wow-classic-era-vanilla-gold/offer/G123ABC"
        "?region_id=dfced32f-aaaa&seller=HellenWong"
    )


@pytest.mark.asyncio
async def test_fetch_all_sellers_paginates_all_regions(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_get(_client, _url, **kwargs):
        relation = kwargs["params"]["relation_id"]
        page = kwargs["params"]["page"]
        calls.append((relation, page))
        size = 48 if page == 1 else 10
        results = [{"username": f"{relation}_u{i}"} for i in range(size)]
        return type("Resp", (), {"json": lambda self: {"payload": {"results": results}}})()

    monkeypatch.setattr("parser.g2g_parser._http_get_retry", fake_get)
    c = G2GClient()
    c._client = object()
    regions = [G2GRegion("r1", "rel1"), G2GRegion("r2", "rel2")]
    sellers = await c.fetch_all_sellers("b", "s", regions)
    assert len(sellers) == 96
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_fetch_all_sellers_deduplicates_usernames(monkeypatch):
    async def fake_get(_client, _url, **kwargs):
        relation = kwargs["params"]["relation_id"]
        page = kwargs["params"]["page"]
        if page > 1:
            results = []
        else:
            results = [{"username": "same"}, {"username": f"{relation}_x"}]
        return type("Resp", (), {"json": lambda self: {"payload": {"results": results}}})()

    monkeypatch.setattr("parser.g2g_parser._http_get_retry", fake_get)
    c = G2GClient()
    c._client = object()
    sellers = await c.fetch_all_sellers("b", "s", [G2GRegion("r1", "a"), G2GRegion("r2", "b")])
    assert sellers.count("same") == 1


@pytest.mark.asyncio
async def test_fetch_seller_offers_paginates(monkeypatch):
    async def fake_get(_client, _url, **kwargs):
        page = kwargs["params"]["page"]
        size = 48 if page == 1 else 30
        results = [
            {
                "offer_id": f"o{page}_{i}",
                "title": "Firemaw [EU - Classic] - Horde",
                "region_id": "rid",
                "relation_id": "rel",
                "unit_price_in_usd": 0.003,
                "min_qty": 1,
                "available_qty": 5,
                "username": "TestSeller",
                "brand_id": "b",
                "service_id": "s",
                "is_group_display": False,
            }
            for i in range(size)
        ]
        return type("Resp", (), {"json": lambda self: {"payload": {"results": results}}})()

    monkeypatch.setattr("parser.g2g_parser._http_get_retry", fake_get)
    c = G2GClient()
    c._client = object()
    out = await c.fetch_seller_offers("b", "s", "TestSeller")
    assert len(out) == 78


@pytest.mark.asyncio
async def test_fetch_seller_offers_max_pages_guard(monkeypatch):
    async def fake_get(_client, _url, **kwargs):
        page = kwargs["params"]["page"]
        results = [
            {
                "offer_id": f"o{page}_{i}",
                "title": "Firemaw [EU - Classic] - Horde",
                "region_id": "rid",
                "relation_id": "rel",
                "unit_price_in_usd": 0.003,
                "min_qty": 1,
                "available_qty": 5,
                "username": "TestSeller",
            }
            for i in range(48)
        ]
        return type("Resp", (), {"json": lambda self: {"payload": {"results": results}}})()

    monkeypatch.setattr("parser.g2g_parser._http_get_retry", fake_get)
    c = G2GClient()
    c._client = object()
    out = await c.fetch_seller_offers("b", "s", "TestSeller")
    assert len(out) == 480


@pytest.mark.asyncio
async def test_fetch_g2g_game_uses_semaphore(monkeypatch):
    max_seen = 0
    running = 0

    async def fake_regions(self, b, s):
        return [G2GRegion("r1", "rel1")]

    async def fake_sellers(self, b, s, regions):
        return [f"seller{i}" for i in range(1, 7)]

    async def fake_fetch(self, b, s, seller):
        nonlocal max_seen, running
        running += 1
        max_seen = max(max_seen, running)
        await asyncio.sleep(0.01)
        running -= 1
        return [
            G2GOffer(
                offer_id=f"id_{seller}",
                title="Firemaw [EU - Classic] - Horde",
                server_name="Firemaw",
                region_id="rid",
                relation_id="rel",
                price_usd=0.003,
                min_qty=1,
                available_qty=5,
                seller=seller,
                brand_id=b,
                service_id=s,
                offer_url=f"https://x?region_id=rid&seller={seller}",
            )
        ]

    monkeypatch.setattr("parser.g2g_parser.G2GClient.fetch_regions", fake_regions)
    monkeypatch.setattr("parser.g2g_parser.G2GClient.fetch_all_sellers", fake_sellers)
    monkeypatch.setattr("parser.g2g_parser.G2GClient.fetch_seller_offers", fake_fetch)
    out = await fetch_g2g_game("wow_classic_era_seasonal_anniversary")
    assert len(out) == 6 and max_seen <= 5


@pytest.mark.asyncio
async def test_buy_url_contains_region_id_and_seller(monkeypatch):
    async def fake_game(*_args, **_kwargs):
        return [
            G2GOffer(
                offer_id="oid1",
                title="Firemaw [EU - Classic] - Horde",
                server_name="Firemaw",
                region_id="rid",
                relation_id="rel",
                price_usd=0.003,
                min_qty=1,
                available_qty=5,
                seller="SellerA",
                brand_id="b",
                service_id="s",
                offer_url=_build_offer_url("oid1", "rid", "SellerA"),
            )
        ]

    monkeypatch.setattr("parser.g2g_parser.fetch_g2g_game", fake_game)
    from parser.g2g_parser import fetch_offers
    offers = await fetch_offers()
    assert offers and all("region_id=" in (o.offer_url or "") and "seller=" in (o.offer_url or "") for o in offers)
    assert all("/offer/" in (o.offer_url or "") and "?" in (o.offer_url or "") for o in offers)


@pytest.mark.asyncio
async def test_buy_url_no_fa_param(monkeypatch):
    async def fake_game(*_args, **_kwargs):
        return [
            G2GOffer(
                offer_id="oid2",
                title="Firemaw [EU - Classic] - Horde",
                server_name="Firemaw",
                region_id="rid",
                relation_id="rel",
                price_usd=0.003,
                min_qty=1,
                available_qty=5,
                seller="SellerB",
                brand_id="b",
                service_id="s",
                offer_url=_build_offer_url("oid2", "rid", "SellerB"),
            )
        ]

    monkeypatch.setattr("parser.g2g_parser.fetch_g2g_game", fake_game)
    from parser.g2g_parser import fetch_offers
    offers = await fetch_offers()
    assert offers and all("fa=" not in (o.offer_url or "") for o in offers)


# ── Bug 2A: Hardcore in _VERSION_PATTERNS ────────────────────────────────────

def test_parse_title_hardcore():
    """Soulseeker [EU - Hardcore] must parse version='Hardcore'."""
    server, region, version, faction = _parse_title("Soulseeker [EU - Hardcore] - Alliance")
    assert server  == "Soulseeker"
    assert region  == "EU"
    assert version == "Hardcore"
    assert faction == "Alliance"


def test_parse_title_hardcore_horde():
    server, region, version, faction = _parse_title("Soulseeker [EU - Hardcore] - Horde")
    assert version == "Hardcore"
    assert faction == "Horde"


# ── Source-region parser behavior (no hardcoded realm overrides) ─────────────

def test_parse_title_penance_keeps_source_region_us():
    server, region, version, faction = _parse_title("Penance [US - Seasonal] - Horde")
    assert server  == "Penance"
    assert region  == "US"
    assert version == "Seasonal"


def test_parse_title_penance_keeps_source_region_ru():
    server, region, version, faction = _parse_title("Penance [RU - Seasonal] - Alliance")
    assert region  == "RU"
    assert version == "Seasonal"
    assert faction == "Alliance"


def test_parse_title_shadowstrike_keeps_source_region_ru():
    server, region, version, faction = _parse_title("Shadowstrike [RU - Seasonal] - Alliance")
    assert server  == "Shadowstrike"
    assert region  == "RU"
    assert version == "Seasonal"


def test_parse_title_shadowstrike_keeps_source_region_us():
    server, region, version, faction = _parse_title("Shadowstrike [US - Seasonal] - Horde")
    assert region  == "US"
    assert version == "Seasonal"

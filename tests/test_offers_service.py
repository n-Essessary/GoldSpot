from __future__ import annotations

from service import offers_service as osvc


def test_compute_index_price_empty_none():
    assert osvc.compute_index_price([]) is None


def test_compute_index_price_single_offer_none(make_offer):
    assert osvc.compute_index_price([make_offer()]) is None


def test_compute_index_price_single_top_pick_returns_none(make_offer):
    """One offer → one (source, faction) pair → thin market → None."""
    offers = [make_offer(source="funpay", faction="Horde", raw_price=0.0015)]
    assert osvc.compute_index_price(offers) is None


def test_compute_index_price_two_pairs_returns_index(make_offer):
    """Two distinct (source, faction) pairs → should return a valid IndexPrice."""
    offers = [
        make_offer(id="o1", source="funpay", faction="Horde",    raw_price=0.0015),
        make_offer(id="o2", source="g2g",    faction="Alliance", raw_price=0.002),
    ]
    result = osvc.compute_index_price(offers)
    assert result is not None
    assert result.offer_count == 2
    assert abs(result.index_price - 1.75) < 1e-4  # mean(1.5, 2.0)
    assert abs(result.best_ask - 1.5) < 1e-6


def test_normalize_funpay_offer_extracts_realm(make_offer):
    o = make_offer(source="funpay", display_server="(EU) Season of Discovery - Firemaw", server="(EU) Season of Discovery - Firemaw")
    n = osvc._normalize_funpay_offer(o)
    assert n.display_server == "(EU) Season of Discovery" and n.server_name == "Firemaw"


def test_normalize_g2g_offer_seasonal_alias(make_offer):
    o = make_offer(source="g2g", display_server="(EU) Seasonal", server="(EU) Seasonal")
    n = osvc._normalize_g2g_offer(o)
    assert n.display_server == "(EU) Season of Discovery"


def test_normalize_g2g_offer_syncs_server_slug(make_offer):
    o = make_offer(source="g2g", display_server="(EU) Seasonal", server="(eu) seasonal")
    n = osvc._normalize_g2g_offer(o)
    assert n.display_server == "(EU) Season of Discovery" and n.server == "(eu) season of discovery"


def test_get_offers_faction_filter(make_offer):
    osvc._cache["funpay"] = [make_offer(id="a", faction="Horde")]
    osvc._cache["g2g"] = [make_offer(id="b", faction="Alliance")]
    out = osvc.get_offers(faction="Horde")
    assert [x.id for x in out] == ["a"]


def test_version_rank_anniversary_before_classic():
    assert osvc._version_rank("(EU) Anniversary") < osvc._version_rank("(EU) Classic")


def test_version_rank_hardcore_after_classic():
    """Hardcore should sort after Classic (rank 4 > rank 3)."""
    assert osvc._version_rank("(EU) Hardcore") > osvc._version_rank("(EU) Classic")


def test_get_servers_single_source_only_g2g(make_offer):
    osvc._cache["g2g"] = [
        make_offer(
            id="g1",
            source="g2g",
            display_server="(EU) Classic",
            server="(eu) classic",
            server_name="Firemaw",
            raw_price=0.014,
        ),
        make_offer(
            id="g2",
            source="g2g",
            display_server="(EU) Classic",
            server="(eu) classic",
            server_name="Gehennas",
            raw_price=0.015,
        ),
    ]
    osvc._cache["funpay"] = []
    groups = osvc.get_servers()
    eu_classic = next((g for g in groups if g.display_server == "(EU) Classic"), None)
    assert eu_classic is not None and eu_classic.min_price > 0


def test_get_servers_single_source_only_funpay(make_offer):
    osvc._cache["funpay"] = [
        make_offer(
            id="f1",
            source="funpay",
            display_server="(EU) Classic",
            server="(eu) classic",
            server_name="",
            raw_price=0.0139,
        ),
    ]
    osvc._cache["g2g"] = []
    groups = osvc.get_servers()
    eu_classic = next((g for g in groups if g.display_server == "(EU) Classic"), None)
    assert eu_classic is not None and eu_classic.min_price > 0


# ── Bug 2D: AU-only realms filtered from non-AU groups ────────────────────────

def test_get_servers_keeps_realm_in_source_group_ru(make_offer):
    """Without parser-side overrides, realm stays in source display_server group."""
    osvc._cache["g2g"] = [
        make_offer(
            id="p1",
            source="g2g",
            display_server="(RU) Season of Discovery",
            server="(ru) season of discovery",
            server_name="Penance",
            raw_price=0.003,
        )
    ]
    osvc._cache["funpay"] = []

    servers = osvc.get_servers()
    ru_sod = next((s for s in servers if s.display_server == "(RU) Season of Discovery"), None)
    assert ru_sod is not None and "Penance" in ru_sod.realms

    # Clean up
    osvc._cache["g2g"] = []


def test_get_servers_penance_in_au_group_when_source_is_au(make_offer):
    """Penance appears in AU group when offer display_server is AU."""
    osvc._cache["g2g"] = [
        make_offer(
            id="p2",
            source="g2g",
            display_server="(AU) Season of Discovery",
            server="(au) season of discovery",
            server_name="Penance",
            raw_price=0.003,
        )
    ]
    osvc._cache["funpay"] = []

    servers = osvc.get_servers()
    au_sod = next((s for s in servers if s.display_server == "(AU) Season of Discovery"), None)
    assert au_sod is not None, "(AU) Season of Discovery group not found"
    assert "Penance" in au_sod.realms, "Penance must appear under (AU) Season of Discovery"

    # Clean up
    osvc._cache["g2g"] = []

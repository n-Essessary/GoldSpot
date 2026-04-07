from __future__ import annotations

from service import offers_service as osvc


def test_compute_index_price_empty_none():
    assert osvc.compute_index_price([]) is None


def test_compute_index_price_single_offer_none(make_offer):
    assert osvc.compute_index_price([make_offer()]) is None


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

from __future__ import annotations

from service import offers_service as osvc


def test_region_filter_does_not_mix(make_offer):
    osvc._cache["g2g"] = [
        make_offer(id="eu", display_server="(EU) Anniversary", server="(EU) Anniversary"),
        make_offer(id="us", display_server="(US) Anniversary", server="(US) Anniversary"),
    ]
    osvc._cache["funpay"] = []
    out = osvc.get_offers(server="(EU) Anniversary")
    assert {o.id for o in out} == {"eu"}


def test_version_filter_does_not_mix(make_offer):
    osvc._cache["g2g"] = [
        make_offer(id="era", display_server="(EU) Classic", server="(EU) Classic"),
        make_offer(id="sod", display_server="(EU) Season of Discovery", server="(EU) Season of Discovery"),
    ]
    osvc._cache["funpay"] = []
    out = osvc.get_offers(server="(EU) Classic")
    assert {o.id for o in out} == {"era"}


def test_combined_filters_intersection(make_offer):
    osvc._cache["g2g"] = [
        make_offer(id="a", display_server="(EU) Anniversary", server_name="Firemaw", faction="Alliance"),
        make_offer(id="b", display_server="(EU) Anniversary", server_name="Firemaw", faction="Horde"),
        make_offer(id="c", display_server="(EU) Anniversary", server_name="Gehennas", faction="Alliance"),
    ]
    osvc._cache["funpay"] = []
    out = osvc.get_offers(server="(EU) Anniversary", server_name="Firemaw", faction="Alliance")
    assert [o.id for o in out] == ["a"]

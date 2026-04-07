from __future__ import annotations

from fastapi.testclient import TestClient

from api import router as api_router
from main import app


def test_offers_api_returns_count_and_offers(monkeypatch, make_offer):
    monkeypatch.setattr(api_router, "get_offers", lambda *_args, **_kwargs: [make_offer(id="1"), make_offer(id="2")])
    client = TestClient(app)
    resp = client.get("/offers")
    body = resp.json()
    assert resp.status_code == 200 and body["count"] == len(body["offers"]) == 2


def test_offers_api_empty_result_not_500(monkeypatch):
    monkeypatch.setattr(api_router, "get_offers", lambda *_args, **_kwargs: [])
    client = TestClient(app)
    resp = client.get("/offers")
    assert resp.status_code == 200 and resp.json() == {"count": 0, "offers": [], "price_unit": "per_1k"}


def test_offers_api_offer_fields_present(monkeypatch, make_offer):
    monkeypatch.setattr(api_router, "get_offers", lambda *_args, **_kwargs: [make_offer(id="x")])
    client = TestClient(app)
    offer = client.get("/offers").json()["offers"][0]
    must_have = {"id", "source", "faction", "price_per_1k", "amount_gold", "seller", "offer_url", "updated_at"}
    assert must_have.issubset(set(offer))

from fastapi import APIRouter, Query

from api.schemas import OffersResponse, PriceHistoryResponse
from service.offers_service import get_offers, get_price_history

router = APIRouter()


@router.get("/offers", response_model=OffersResponse)
async def get_offers_handler(
    server: str | None = Query(None),
    faction: str | None = Query(None),
    sort_by: str = Query("price", pattern="^(price|amount)$"),
    limit: int = Query(20, ge=1, le=100),
):
    offers = get_offers(server, faction, sort_by, limit)
    return OffersResponse(count=len(offers), offers=offers)


@router.get("/price-history")
async def get_price_history_handler(
    server: str = Query("all"),
    faction: str = Query("all"),
    last: int = Query(50, ge=1, le=200),
):
    points = get_price_history(server, faction, last)
    return {
        "count": len(points),
        "points": points,
    }



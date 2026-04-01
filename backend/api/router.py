from fastapi import APIRouter, Query

from api.schemas import MetaResponse, OfferRow, OffersResponse, PriceHistoryResponse, ServersResponse
from service.offers_service import get_meta, get_offers, get_price_history, get_servers

router = APIRouter()


@router.get("/meta", response_model=MetaResponse)
async def get_meta_handler():
    """Версия данных. Frontend опрашивает раз в ~10 сек.
    Если last_update изменился — клиент перезапрашивает /offers и /price-history.
    """
    return MetaResponse(last_update=get_meta())


@router.get("/servers", response_model=ServersResponse)
async def get_servers_handler():
    """
    Возвращает список уникальных серверов, отсортированных по
    количеству доступных онлайн-офферов (самые популярные — первыми).
    """
    servers = get_servers()
    return ServersResponse(count=len(servers), servers=servers)


@router.get("/offers", response_model=OffersResponse)
async def get_offers_handler(
    server: str | None = Query(None),
    server_name: str | None = Query(None),
    faction: str | None = Query(None),
    sort_by: str = Query("price", pattern="^(price|amount)$"),
):
    offers = get_offers(server, faction, sort_by, server_name)
    # Конвертируем Offer → OfferRow: убираем display_server / server_name / server
    # из ответа — пользователь уже выбрал их в левой панели.
    rows = [OfferRow.model_validate(o) for o in offers]
    return OffersResponse(count=len(rows), offers=rows)


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



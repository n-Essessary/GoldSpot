from fastapi import APIRouter, Query

from api.schemas import MetaResponse, OfferRow, OffersResponse, PriceHistoryResponse, ServersResponse
from service.offers_service import get_meta, get_offers, get_parser_status, get_price_history, get_servers

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
    Возвращает иерархический список групп серверов.

    Каждая группа содержит:
      - display_server: читаемое название группы, напр. "(EU) Anniversary"
      - realms: список реалмов внутри группы (только G2G), напр. ["Firemaw", "Spineshatter"]
                Пустой список — у группы нет реалмов (FunPay-офферы)
      - min_price: минимальная цена по всем офферам группы ($/1k)

    Фронтенд строит двухуровневое дерево:
      (EU) Anniversary
        └── Firemaw
        └── Spineshatter
    """
    groups = get_servers()
    return ServersResponse(count=len(groups), servers=groups)


@router.get("/offers", response_model=OffersResponse)
async def get_offers_handler(
    server: str | None = Query(None),
    server_name: str | None = Query(None),
    faction: str | None = Query(None),
    sort_by: str = Query("price", pattern="^(price|amount)$"),
):
    offers = get_offers(server, faction, sort_by, server_name)
    rows = [OfferRow.model_validate(o) for o in offers]
    return OffersResponse(count=len(rows), offers=rows)


@router.get("/parser-status")
async def parser_status_handler():
    """
    Диагностический эндпоинт: состояние каждого парсера.

    Пример ответа:
      {
        "funpay": {"offers": 142, "last_update": "2024-...", "running": false},
        "g2g":    {"offers":  87, "last_update": "2024-...", "running": true}
      }
    """
    return get_parser_status()


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



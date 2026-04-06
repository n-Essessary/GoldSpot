from fastapi import APIRouter, HTTPException, Query

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
      - min_price: best_ask из IndexPrice (реальная цена покупки прямо сейчас)
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
        "funpay": {"offers": 142, "last_update": "2024-...", "running": false, "version": 5},
        "g2g":    {"offers":  87, "last_update": "2024-...", "running": true,  "version": 3}
      }
    """
    return get_parser_status()


@router.get("/price-history")
async def get_price_history_handler(
    server: str = Query("all"),
    faction: str = Query("all"),
    last: int = Query(50, ge=1, le=200),
):
    """Текущий снимок из in-memory кэша — для обратной совместимости."""
    points = get_price_history(server, faction, last)
    return {
        "count": len(points),
        "points": points,
    }


@router.get("/price-history/ohlc")
async def price_history_ohlc(
    server:     str = Query(..., description="display_server, напр. '(EU) Anniversary'"),
    faction:    str = Query("all", description="'all' | 'Alliance' | 'Horde'"),
    last_hours: int = Query(168, ge=1, le=8760),
    max_points: int = Query(500, ge=50, le=2000),
):
    """
    OHLC + VWAP + best_ask из PostgreSQL (таблица price_index_snapshots).
    Адаптивный bucket = max(5, last_hours*60 / max_points) минут.
    Возвращает [] если DATABASE_URL не задан или БД недоступна — не ломает фронтенд.
    """
    from db.writer import query_index_history
    points = await query_index_history(server, faction, last_hours, max_points)
    return {
        "count":  len(points),
        "points": points,
        "meta": {
            "server":         server,
            "faction":        faction,
            "last_hours":     last_hours,
            "bucket_minutes": max(5, (last_hours * 60) // max_points),
        },
    }


@router.get("/index/{server:path}")
async def get_index_price(
    server:  str,
    faction: str = Query("All"),
):
    """
    Текущий IndexPrice из in-memory кэша (< 1ms).
    Содержит index_price, vwap, best_ask, price_min/max, offer_count, sources.
    """
    from service.offers_service import _index_cache
    key = f"{server}::{faction}"
    idx = _index_cache.get(key)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"No index data for {server!r} / {faction!r}")
    return {
        "server":      server,
        "faction":     faction,
        "index_price": idx.index_price,
        "vwap":        idx.vwap,
        "best_ask":    idx.best_ask,
        "price_min":   idx.price_min,
        "price_max":   idx.price_max,
        "offer_count": idx.offer_count,
        "total_volume": idx.total_volume,
        "sources":     idx.sources,
    }

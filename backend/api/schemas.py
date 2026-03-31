from datetime import datetime, timezone

from pydantic import BaseModel, field_serializer, field_validator, model_validator


class Offer(BaseModel):
    id: str
    source: str
    server: str          # slug, всегда lowercase: "(eu) anniversary"
    display_server: str = ""  # группа: "(EU) Anniversary"; заполняется парсером
    server_name: str = ""     # сервер внутри группы: "Spineshatter" (G2G); "" для FunPay
    faction: str
    price_per_1k: float
    amount_gold: int
    seller: str
    offer_url: str | None = None
    updated_at: datetime
    fetched_at: datetime

    @model_validator(mode="after")
    def _normalise_server(self) -> "Offer":
        # server — всегда slug (lowercase)
        self.server = self.server.lower()
        # display_server — fallback на server если парсер не задал
        if not self.display_server:
            self.display_server = self.server
        return self

    @field_validator("updated_at", "fetched_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("datetime должен быть timezone-aware (UTC)")
        return v

    @field_validator("price_per_1k")
    @classmethod
    def _positive_price(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("price_per_1k должна быть > 0")
        return v

    @field_validator("amount_gold")
    @classmethod
    def _positive_amount(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("amount_gold должен быть > 0")
        return v

    @field_serializer("updated_at", "fetched_at")
    def _serialize_dt(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OffersResponse(BaseModel):
    count: int
    offers: list[Offer]


class ServersResponse(BaseModel):
    count: int
    servers: list[str]


class PriceHistoryPoint(BaseModel):
    timestamp: datetime
    price: float | None = None
    min: float | None = None
    max: float | None = None
    count: int = 0

    @field_serializer("timestamp")
    def serialize_ts(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PriceHistoryResponse(BaseModel):
    count: int
    points: list[PriceHistoryPoint]


class MetaResponse(BaseModel):
    """Версия данных: ISO 8601 UTC-время последнего обновления кэша.

    Frontend опрашивает этот endpoint каждые ~10 сек и перезапрашивает
    /offers + /price-history только если last_update изменился.
    Null — кэш ещё не заполнен (сервер только запустился).
    """
    last_update: datetime | None = None

    @field_serializer("last_update")
    def _serialize_dt(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

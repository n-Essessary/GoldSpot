from datetime import datetime, timezone

from pydantic import BaseModel, field_serializer, field_validator


class Offer(BaseModel):
    id: str
    source: str
    server: str
    faction: str
    price_per_1k: float
    amount_gold: int
    seller: str
    offer_url: str | None = None
    updated_at: datetime
    fetched_at: datetime

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


class PriceHistoryPoint(BaseModel):
    timestamp: datetime
    avg_price: float
    min_price: float
    offer_count: int

    @field_serializer("timestamp")
    def _serialize_ts(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PriceHistoryResponse(BaseModel):
    count: int
    points: list[PriceHistoryPoint]

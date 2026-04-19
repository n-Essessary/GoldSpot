"""
api/schemas.py — Pydantic models for GoldSpot API.

Design contract:
  • Offer       — internal model (parsers → service → DB)
  • OfferRow    — API response row (serialised from Offer, respects price_unit)
  • price_per_1k is ALWAYS derived at read-time from raw_price (Task 2)
  • NEVER persisted in DB — only raw_price is stored
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator, model_validator


# ── Constants ─────────────────────────────────────────────────────────────────

PriceUnit = Literal["per_1k", "per_1"]   # display toggle: per 1000 gold vs per 1 gold


# ── Core offer model ──────────────────────────────────────────────────────────

class Offer(BaseModel):
    """Internal offer representation — passed between parsers, service, DB writer.

    Raw price contract (Task 1 & 2):
      G2G:    raw_price = unit_price_in_usd (price per 1 gold)
              raw_price_unit = 'per_unit'   lot_size = 1
      FunPay: raw_price = lot price (price for the whole lot in USD)
              raw_price_unit = 'per_lot'    lot_size = amount_gold

    price_per_1k is derived in model_validator:
      per_unit → raw_price * 1000
      per_lot  → (raw_price / lot_size) * 1000

    Backward-compat (migration period):
      Legacy parsers may set price_per_1k directly (raw_price stays 0).
      model_validator then back-fills raw_price = price_per_1k / 1000
      so DB writers always have a raw value to store.

    Canonical server fields (set by normalize_pipeline after resolution):
      server_id   — FK to servers.id; None until resolved
      realm_type  — "Normal" | "Hardcore"; always from canonical registry
      raw_title   — original source title (G2G only); used as alias lookup key
                    so the parser never needs to guess version from the title.
                    Not exposed in the API (not in OfferRow).
    """
    id: str
    # TODO(I6): keep strict source enum for all parsers; expand only with explicit new source rollout.
    source: Literal["funpay", "g2g"]
    server: str           # slug, always lowercase: "(eu) anniversary"
    display_server: str = ""    # group: "(EU) Anniversary"; set by parser
    server_name: str = ""       # realm inside group: "Spineshatter" (G2G)
    server_id: Optional[int] = None  # FK → servers.id; None during migration

    # ── Canonical realm type (from server registry) ───────────────────────────
    # "Normal" for all standard realms; "Hardcore" for permadeath realms.
    # Hardcore is NOT a version — it is a property of the realm alongside version.
    realm_type: str = "Normal"

    # ── Raw source title (G2G only) ───────────────────────────────────────────
    # Stored verbatim from the G2G API response for use as alias lookup key.
    # Parsers MUST NOT guess version from this; canonical registry owns that.
    # Not included in OfferRow / OffersResponse (internal pipeline field only).
    raw_title: str = ""
    game_version: str = ""  # "Classic Era" | "MoP Classic" | set by parser

    faction: str

    # ── Raw price (source of truth) ───────────────────────────────────────────
    raw_price: float = 0.0           # exact price as received from source
    raw_price_unit: str = "per_unit" # 'per_unit' | 'per_lot'
    lot_size: int = 1                # gold in lot (FunPay); always 1 for G2G

    # ── price_per_1k — derived by model_validator, kept as field for compat ──
    # Parsers using the new path should set raw_price instead.
    # Legacy parsers may still set this directly (back-fill will apply).
    price_per_1k: float = 0.0

    amount_gold: int
    seller: str
    is_suspicious: bool = False
    offer_url: str | None = None
    updated_at: datetime
    fetched_at: datetime

    @model_validator(mode="after")
    def _normalise(self) -> "Offer":
        # ── Normalise server fields ───────────────────────────────────────────
        self.server = self.server.lower()
        if not self.display_server:
            self.display_server = self.server

        # ── Derive price_per_1k from raw_price ────────────────────────────────
        if self.raw_price > 0:
            # New path: parsers set raw_price
            lot_sz = max(self.lot_size, 1)
            if self.raw_price_unit == "per_lot":
                self.price_per_1k = round(self.raw_price / lot_sz * 1000.0, 6)
            else:  # 'per_unit' (G2G) or default
                self.price_per_1k = round(self.raw_price * 1000.0, 6)

        elif self.price_per_1k > 0:
            # Legacy path: parser set price_per_1k directly → back-fill raw_price
            self.raw_price = round(self.price_per_1k / 1000.0, 8)
            self.raw_price_unit = "per_unit"
            self.lot_size = 1

        # ── Final validation ──────────────────────────────────────────────────
        if self.price_per_1k <= 0:
            raise ValueError(
                f"price_per_1k must be > 0 (offer_id={self.id!r}, "
                f"raw_price={self.raw_price}, raw_price_unit={self.raw_price_unit!r})"
            )
        if self.amount_gold <= 0:
            raise ValueError("amount_gold must be > 0")

        return self

    @field_validator("updated_at", "fetched_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return v

    @field_serializer("updated_at", "fetched_at")
    def _serialize_dt(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── API response row ──────────────────────────────────────────────────────────

class OfferRow(BaseModel):
    """Single row in the /offers response.

    price_per_1k   — backward-compat: always price per 1000 gold (USD)
    price_display  — display price, controlled by price_unit query param:
                       'per_1k'  → same as price_per_1k  (default)
                       'per_1'   → price per 1 gold = price_per_1k / 1000
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    server_name: str = ""
    server_id: Optional[int] = None
    faction: str
    price_per_1k: float          # backward compat — always per 1000 gold
    price_display: float         # respects price_unit
    amount_gold: int
    seller: str
    game_version: str = ""
    is_suspicious: bool = False
    offer_url: str | None = None
    updated_at: datetime
    fetched_at: datetime

    @field_serializer("updated_at", "fetched_at")
    def _serialize_dt(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def from_offer(cls, offer: Offer, price_unit: PriceUnit = "per_1k") -> "OfferRow":
        """Build OfferRow from Offer, applying price_unit to price_display."""
        p1k = offer.price_per_1k
        if price_unit == "per_1":
            price_display = round(p1k / 1000.0, 8)
        else:
            price_display = round(p1k, 4)
        return cls(
            id=offer.id,
            source=offer.source,
            server_name=offer.server_name,
            server_id=offer.server_id,
            faction=offer.faction,
            game_version=offer.game_version,
            price_per_1k=round(p1k, 4),
            price_display=price_display,
            amount_gold=offer.amount_gold,
            seller=offer.seller,
            is_suspicious=offer.is_suspicious,
            offer_url=offer.offer_url,
            updated_at=offer.updated_at,
            fetched_at=offer.fetched_at,
        )


class OffersResponse(BaseModel):
    count: int
    offers: list[OfferRow]
    price_unit: PriceUnit = "per_1k"  # echoed back so frontend knows the unit


class ServerGroup(BaseModel):
    """Server group shown in the sidebar.

    display_server  — group label: "(EU) Anniversary"
    realms          — individual realm names (G2G only; empty for FunPay-only)
    min_price       — best_ask from IndexPrice (realistic buy price), per 1k gold
    game_version    — "Classic Era" | "MoP Classic" | etc., derived from display_server
    """
    display_server: str
    realms: list[str]
    realm_sources: dict[str, list[str]] = {}
    min_price: float
    game_version: str = ""


class ServersResponse(BaseModel):
    count: int
    servers: list[ServerGroup]


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
    """Data version: ISO 8601 UTC time of last cache update.

    Frontend polls this every ~10 s and re-fetches /offers + /price-history
    only when last_update changes. None means cache is empty.
    """
    last_update: datetime | None = None

    @field_serializer("last_update")
    def _serialize_dt(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Per-server price index (Task 4) ──────────────────────────────────────────

class ServerPriceIndexEntry(BaseModel):
    """Current price index for a specific server+faction."""
    server_name: str
    region: str
    version: str
    faction: str
    index_price: float          # mean of top-10 cheapest, price per unit (per 1 gold)
    index_price_per_1k: float   # index_price * 1000 — convenience field
    sample_size: int
    min_price: float
    max_price: float
    computed_at: datetime

    @field_serializer("computed_at")
    def _serialize_dt(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PriceIndexResponse(BaseModel):
    count: int
    entries: list[ServerPriceIndexEntry]


class ServerHistoryPoint(BaseModel):
    """Single point in per-server price history (Task 4)."""
    recorded_at: datetime
    index_price: float          # price per unit
    index_price_per_1k: float   # price per 1000 gold
    sample_size: int | None = None

    @field_serializer("recorded_at")
    def _serialize_dt(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ServerHistoryResponse(BaseModel):
    server: str
    region: str
    version: str
    faction: str
    count: int
    points: list[ServerHistoryPoint]

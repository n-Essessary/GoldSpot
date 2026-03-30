from __future__ import annotations

"""
Нормализация сырых имён серверов → (slug, display_server).

Единый модуль для всех парсеров — любые изменения форматирования
вносятся здесь и автоматически применяются везде.

Примеры:
    "(EU) Flamegor"                    → slug="flamegor",      display="Flamegor (EU)"
    "(EU) #Anniversary - Spineshatter" → slug="spineshatter",  display="Spineshatter (EU)"
    "(EU-PVP) Gehennas"                → slug="gehennas",      display="Gehennas (EU-PVP)"
    "Firemaw"                          → slug="firemaw",       display="Firemaw"
    ""                                 → slug="unknown",       display="unknown"
"""

import re
from typing import NamedTuple


class ServerInfo(NamedTuple):
    slug: str     # URL-safe lowercase: "flamegor"
    display: str  # Человекочитаемое: "Flamegor (EU)"


def normalize_server(raw: str | None) -> ServerInfo:
    """
    Разбирает сырую строку сервера и возвращает (slug, display).

    Алгоритм:
    1. Извлекаем регион из «(EU)» / «(EU-PVP)» в начале строки.
    2. Убираем префикс региона.
    3. Если остаётся конструкция «… - Имя», берём часть после последнего « - ».
    4. Убираем теги вида «#Anniversary».
    5. slug = name.lower(), display = «Name (REGION)» или просто «Name».
    """
    if not raw or not raw.strip():
        return ServerInfo("unknown", "unknown")

    raw = raw.strip()

    # Шаг 1: регион — необязателен
    region_m = re.match(r"^\(([^)]+)\)\s*", raw)
    region = region_m.group(1).strip() if region_m else None

    # Шаг 2: убираем «(REGION) »
    name = re.sub(r"^\([^)]+\)\s*", "", raw).strip()

    # Шаг 3: берём часть после последнего « - »
    if " - " in name:
        name = name.rsplit(" - ", 1)[-1].strip()

    # Шаг 4: убираем event-теги «#Word»
    name = re.sub(r"#\S+\s*", "", name).strip()

    # Защита: если после всех шагов name пуст — возвращаем raw как есть
    name = name or raw

    slug    = name.lower()
    display = f"{name} ({region})" if region else name

    return ServerInfo(slug=slug, display=display)

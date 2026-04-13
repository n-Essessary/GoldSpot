import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from service.offers_service import start_background_parsers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глушим httpx INFO-спам, оставляем WARNING+
for _httpx_logger_name in ("httpx", "httpcore"):
    logging.getLogger(_httpx_logger_name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Ограничиваем пул потоков для asyncio.to_thread (парсеры HTML и т.п.).
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=4))

    # Запускаем независимые фоновые циклы FunPay и G2G.
    # Первые данные появятся через ~5-30 сек (G2G быстрее, FunPay дольше).
    await start_background_parsers()

    # 4-tier rolling snapshot storage (snapshots_1m / 5m / 1h / 1d).
    # Writes every 60 s, downsamples every 5 min, cleans up every 6 h.
    from service.tiered_snapshot_loop import start_tiered_snapshot_loop
    asyncio.create_task(start_tiered_snapshot_loop())

    # Ежесуточная очистка снимков старше 1 года.
    # Безопасен: если DATABASE_URL не задан — просто спит.
    from db.writer import cleanup_old_snapshots
    asyncio.create_task(cleanup_old_snapshots())

    yield


_origins_raw = os.getenv("ALLOWED_ORIGINS", "https://gold-spot.vercel.app")
_allowed_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]

app = FastAPI(title="WoW Gold Market Analytics", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.router import router  # noqa: E402

app.include_router(router)

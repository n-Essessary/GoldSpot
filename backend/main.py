import asyncio
import contextlib
import logging
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from service import offers_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 60


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async def refresh_loop() -> None:
        consecutive_errors = 0
        while True:
            try:
                await offers_service.refresh()
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                backoff = min(2 ** consecutive_errors, _MAX_BACKOFF_SECONDS)
                logger.exception(
                    "Фоновое обновление кэша не удалось (попытка %d), retry через %d сек.",
                    consecutive_errors, backoff,
                )
                await asyncio.sleep(backoff)
                continue

            interval = random.uniform(50, 70)
            logger.info("Next refresh in %.1fs", interval)
            await asyncio.sleep(interval)

    task = asyncio.create_task(refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="WoW Gold Market Analytics", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # временно
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from api.router import router

app.include_router(router)

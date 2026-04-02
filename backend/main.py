import logging
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
    # Запускаем независимые фоновые циклы FunPay и G2G.
    # create_task внутри start_background_parsers — не блокирует старт сервера.
    # Первые данные появятся через ~5-30 сек (G2G быстрее, FunPay дольше).
    await start_background_parsers()
    yield


app = FastAPI(title="WoW Gold Market Analytics", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # временно
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.router import router  # noqa: E402

app.include_router(router)

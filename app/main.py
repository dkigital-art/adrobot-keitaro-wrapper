from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import campaigns, offers, streams
from app.api.campaigns import build_meta
from app.config import get_settings
from app.db import upgrade_database
from app.schemas import MetaOut
from app.services.keitaro import close_shared_clients, warm_up_caches

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    upgrade_database()
    if get_settings().keitaro_api_key:
        warm_up_caches()
    yield
    close_shared_clients()


app = FastAPI(
    title="AdRobot",
    description="Обёртка над Keitaro Admin API (тестовое задание AdRobot ТЗ)",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(campaigns.router)
app.include_router(offers.router)
app.include_router(streams.router)


@app.get("/api/meta", response_model=MetaOut, tags=["meta"])
def get_meta():
    return build_meta()


@app.get("/api/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "keitaro_url": settings.keitaro_url,
        "api_key_configured": bool(settings.keitaro_api_key),
    }


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI не найден")
    return FileResponse(index_path)


@app.get("/campaigns/{campaign_id}")
def campaign_page(campaign_id: int):
    page = STATIC_DIR / "campaign.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="UI не найден")
    return FileResponse(page)

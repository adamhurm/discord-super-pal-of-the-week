from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from superpal.cards.db import DB_PATH, init_db
from superpal.webapp.routes import router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Bringus Card Game", docs_url=None, redoc_url=None, lifespan=_lifespan)
    app.include_router(router)
    images_dir = Path(DB_PATH).parent / "images"
    images_dir.mkdir(exist_ok=True)
    app.mount("/static/avatars", StaticFiles(directory=str(images_dir)), name="avatars")
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app

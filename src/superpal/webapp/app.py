from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from superpal.cards.db import DB_PATH
from superpal.webapp.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Bringus Card Game", docs_url=None, redoc_url=None)
    app.include_router(router)
    images_dir = Path(DB_PATH).parent / "images"
    images_dir.mkdir(exist_ok=True)
    app.mount("/static/avatars", StaticFiles(directory=str(images_dir)), name="avatars")
    return app

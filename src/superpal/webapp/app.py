from fastapi import FastAPI
from superpal.webapp.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Bringus Card Game", docs_url=None, redoc_url=None)
    app.include_router(router)
    return app

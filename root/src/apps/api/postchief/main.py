from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from postchief.config import Settings
from postchief.db import make_engine


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    app = FastAPI(title="Post Chief", version="0.1.0")
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessions = sessionmaker(app.state.engine, expire_on_commit=False)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"])

    @app.get("/api/health")
    def health():
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "application": "Post Chief"}

    return app

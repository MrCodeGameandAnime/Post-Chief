from contextlib import asynccontextmanager
from fastapi import FastAPI
from postchief.auth import router as auth_router, bootstrap_owner
from postchief.github.routes import router as github_router, webhooks
from postchief.github.service import GitHubError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from postchief.config import Settings
from postchief.db import make_engine


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        bootstrap_owner(app)
        yield
        app.state.engine.dispose()
    app = FastAPI(title="Post Chief", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessions = sessionmaker(app.state.engine, expire_on_commit=False)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"])
    app.include_router(auth_router, prefix="/api")
    app.include_router(github_router, prefix="/api")
    app.include_router(webhooks, prefix="/api")

    @app.exception_handler(GitHubError)
    async def github_error(request, error):
        return JSONResponse(status_code=error.status, content={"detail":error.message})

    @app.get("/api/health")
    def health():
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "application": "Post Chief"}

    return app

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI
from postchief.auth import router as auth_router, bootstrap_owner
from postchief.github.routes import router as github_router, webhooks
from postchief.github.service import GitHubError
from fastapi.responses import JSONResponse
from postchief.campaigns.routes import router as campaigns_router
from postchief.campaigns.handoff import router as handoff_router
from postchief.assets import router as assets_router
from sqlalchemy.exc import IntegrityError
from postchief.providers.routes import router as providers_router
from postchief.providers.oauth import router as oauth_router
from postchief.providers.linkedin_routes import router as linkedin_router
from postchief.providers.pinterest_routes import router as pinterest_router
from postchief.publishing.routes import router as publishing_router
from postchief.publishing.media import router as media_router
from postchief.analytics.routes import router as analytics_router
from postchief.agents.routes import router as agents_router
from postchief.feedback.routes import router as feedback_router
from provider_contracts import ProviderError, ErrorReason
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
    @app.middleware('http')
    async def response_security(request, call_next):
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        return response
    app.include_router(auth_router, prefix="/api")
    app.include_router(github_router, prefix="/api")
    app.include_router(webhooks, prefix="/api")
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(handoff_router, prefix="/api")
    app.include_router(assets_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")
    app.include_router(oauth_router, prefix="/api")
    app.include_router(linkedin_router, prefix="/api")
    app.include_router(pinterest_router, prefix="/api")
    app.include_router(publishing_router, prefix="/api")
    app.include_router(media_router, prefix="/api")
    app.include_router(analytics_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(feedback_router, prefix="/api")

    @app.exception_handler(GitHubError)
    async def github_error(request, error):
        return JSONResponse(status_code=error.status, content={"detail":error.message})

    @app.exception_handler(IntegrityError)
    async def integrity_error(request,error):
        return JSONResponse(status_code=409,content={"detail":"A referenced record changed; reload and retry"})

    @app.exception_handler(ProviderError)
    async def provider_error(request,error):
        status = 429 if error.reason == ErrorReason.RATE_LIMITED else (503 if error.retryable else 422)
        return JSONResponse(status_code=status,content={"detail":error.message,"error":error.to_dict()})

    @app.get("/api/health")
    def health():
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "application": "Post Chief"}

    if Path(settings.web_dir).is_dir():
        app.mount('/',StaticFiles(directory=settings.web_dir,html=True),name='dashboard')
    return app

"""
LogMind — AI 智能日志分析平台

FastAPI Application Entry Point
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from logmind.core.config import get_settings
from logmind.core.exceptions import (
    AllProvidersFailedError,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    PipelineError,
    ProviderError,
    LogMindError,
    QuotaExceededError,
)
from logmind.core.logging import setup_logging
from logmind.core.middleware import RequestLoggingMiddleware, TenantMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    settings = get_settings()

    # Startup
    setup_logging(
        log_level="DEBUG" if settings.debug else "INFO",
        json_format=settings.app_env != "development",
    )

    # Import all models to register them with SQLAlchemy
    import logmind.domain.tenant.models  # noqa: F401
    import logmind.domain.provider.models  # noqa: F401
    import logmind.domain.prompt.models  # noqa: F401
    import logmind.domain.analysis.models  # noqa: F401
    import logmind.domain.alert.models  # noqa: F401
    import logmind.domain.rag.models  # noqa: F401

    # Import provider adapters to trigger registration
    import logmind.domain.provider.adapters  # noqa: F401

    # Initialize database
    from logmind.core.database import init_db
    await init_db()

    yield

    # Shutdown
    from logmind.core.database import close_db
    from logmind.core.elasticsearch import close_es
    from logmind.core.redis import close_redis
    from logmind.domain.provider.manager import ProviderManager

    await ProviderManager.clear_cache()
    await close_es()
    await close_redis()
    await close_db()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="LogMind — AI 智能日志分析平台",
        description=(
            "Enterprise-grade AI-powered log analysis platform. "
            "Supports multiple AI providers, RAG knowledge base, "
            "multi-tenant isolation, and configurable prompt templates."
        ),
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["https://logmind.internal"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(TenantMiddleware)

    # ── Exception Handlers ───────────────────────────────
    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"error": exc.message, "detail": exc.detail})

    @app.exception_handler(AuthenticationError)
    async def auth_handler(request: Request, exc: AuthenticationError):
        return JSONResponse(status_code=401, content={"error": exc.message})

    @app.exception_handler(AuthorizationError)
    async def authz_handler(request: Request, exc: AuthorizationError):
        return JSONResponse(status_code=403, content={"error": exc.message})

    @app.exception_handler(ProviderError)
    async def provider_handler(request: Request, exc: ProviderError):
        return JSONResponse(status_code=502, content={"error": exc.message, "detail": exc.detail})

    @app.exception_handler(AllProvidersFailedError)
    async def all_providers_handler(request: Request, exc: AllProvidersFailedError):
        return JSONResponse(status_code=503, content={"error": exc.message})

    @app.exception_handler(PipelineError)
    async def pipeline_handler(request: Request, exc: PipelineError):
        return JSONResponse(status_code=500, content={"error": exc.message, "detail": exc.detail})

    @app.exception_handler(QuotaExceededError)
    async def quota_handler(request: Request, exc: QuotaExceededError):
        return JSONResponse(status_code=429, content={"error": exc.message})

    @app.exception_handler(LogMindError)
    async def logmind_handler(request: Request, exc: LogMindError):
        return JSONResponse(status_code=400, content={"error": exc.message, "detail": exc.detail})

    # ── Routes ───────────────────────────────────────────
    _register_routes(app)

    return app


def _register_routes(app: FastAPI):
    """Register all domain routers under /api/v1 prefix."""
    from fastapi import APIRouter

    from logmind.core.elasticsearch import check_es_health

    api_router = APIRouter(prefix="/api/v1")

    # Health check (no auth required)
    @api_router.get("/health", tags=["System"])
    async def health_check():
        es_health = await check_es_health()
        return {
            "status": "ok",
            "version": "0.1.0",
            "elasticsearch": es_health,
        }

    # Domain routers
    from logmind.domain.tenant.router import auth_router, biz_router, router as tenant_router
    from logmind.domain.provider.router import router as provider_router
    from logmind.domain.prompt.router import router as prompt_router
    from logmind.domain.log.router import router as log_router
    from logmind.domain.analysis.router import router as analysis_router
    from logmind.domain.alert.router import router as alert_router
    from logmind.domain.dashboard.router import router as dashboard_router

    api_router.include_router(auth_router)
    api_router.include_router(tenant_router)
    api_router.include_router(biz_router)
    api_router.include_router(provider_router)
    api_router.include_router(prompt_router)
    api_router.include_router(log_router)
    api_router.include_router(analysis_router)
    api_router.include_router(alert_router)
    api_router.include_router(dashboard_router)

    app.include_router(api_router)


# Create the application instance
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("logmind.main:app", host="127.0.0.1", port=8000, reload=True)

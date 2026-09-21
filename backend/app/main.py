"""FarmTwin ASGI application composition."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI

from app.core.browser_policy import apply_browser_policy
from app.core.config import Settings
from app.core.database import check_readiness, create_engine, create_session_factory
from app.core.exceptions import install_exception_handlers
from app.core.lifecycle import RequestDrainMiddleware, ServiceLifecycleState
from app.core.logging import configure_logging
from app.core.request_context import RequestContextMiddleware
from app.core.security import set_identity_client


logger = logging.getLogger("farmtwin.lifecycle")
SHUTDOWN_DRAIN_SECONDS = 20.0
SHUTDOWN_CANCELLATION_SECONDS = 1.0
SHUTDOWN_CLEANUP_SECONDS = 9.0


async def _cleanup_resources(api: FastAPI) -> None:
    """Dispose lifespan resources inside the reserved cleanup budget."""
    engine = getattr(api.state, "engine", None)
    identity_client = getattr(api.state, "identity_client", None)

    async def cleanup() -> None:
        operations = []
        if identity_client is not None:
            operations.append(identity_client.aclose())
        if engine is not None:
            operations.append(engine.dispose())
        if operations:
            await asyncio.gather(*operations, return_exceptions=True)
        set_identity_client(None)
        for handler in logging.getLogger().handlers:
            handler.flush()

    try:
        async with asyncio.timeout(SHUTDOWN_CLEANUP_SECONDS):
            await cleanup()
    except TimeoutError:
        logger.error(
            "shutdown.cleanup_timeout",
            extra={"event": "shutdown.cleanup_timeout"},
        )
    finally:
        api.state.session_factory = None
        api.state.engine = None
        api.state.identity_client = None


def _lifespan(settings: Settings, lifecycle: ServiceLifecycleState):
    @asynccontextmanager
    async def lifespan(api: FastAPI) -> AsyncIterator[None]:
        # Settings have already passed fail-fast Pydantic validation. Configure
        # privacy-safe logging before constructing external resources.
        configure_logging(settings)
        engine = create_engine(settings)
        identity_client = httpx.AsyncClient(timeout=3.0)
        session_factory = create_session_factory(engine)
        api.state.engine = engine
        api.state.identity_client = identity_client
        api.state.session_factory = session_factory
        set_identity_client(identity_client)

        checks, ready = await check_readiness(session_factory, settings)
        if not ready:
            lifecycle.begin_shutdown()
            await _cleanup_resources(api)
            logger.error(
                "startup.incompatible",
                extra={"event": "startup.incompatible"},
            )
            raise RuntimeError("Startup dependency verification failed")

        lifecycle.mark_ready()
        logger.info("startup.complete", extra={"event": "startup.complete"})
        try:
            yield
        finally:
            lifecycle.begin_shutdown()
            logger.info("shutdown.draining", extra={"event": "shutdown.draining"})
            await lifecycle.drain(
                SHUTDOWN_DRAIN_SECONDS,
                SHUTDOWN_CANCELLATION_SECONDS,
            )
            await _cleanup_resources(api)
            logger.info("shutdown.complete", extra={"event": "shutdown.complete"})

    return lifespan


def custom_openapi(app: FastAPI):
    """
    Customize OpenAPI schema.
    
    Requirements: 7.3, 7.4
    
    - Sets API version to 1.0.0
    - Configures bearer security scheme
    - Documents only implemented operations
    """
    from fastapi.openapi.utils import get_openapi

    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="FarmTwin API",
        version="1.0.0",
        description="FarmTwin backend API with persistent farm boundary workflows",
        routes=app.routes,
    )

    # Add bearer security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT bearer token from identity provider",
        }
    }

    # Apply security to all routes except health/docs
    for path, path_item in openapi_schema["paths"].items():
        # Skip health and documentation routes
        if path in ["/health", "/ready", "/docs", "/openapi.json", "/redoc"]:
            continue

        # Apply bearer auth to all operations in this path
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation["security"] = [{"BearerAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


def create_app(settings: Settings | None = None) -> RequestContextMiddleware:
    """
    Build the HTTP app with request context outside CORS and errors.
    
    Requirements: 7.1, 7.2, 7.3, 7.4
    """

    settings = settings or Settings()
    configure_logging(settings)
    lifecycle = ServiceLifecycleState()

    api = FastAPI(
        title="FarmTwin API",
        version="1.0.0",
        description="FarmTwin backend API",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=_lifespan(settings, lifecycle),
    )
    api.state.settings = settings
    api.state.lifecycle = lifecycle
    api.state.engine = None
    api.state.identity_client = None
    api.state.session_factory = None

    # Install exception handlers
    install_exception_handlers(api)

    # Include health routes (explicit exceptions to /api/v1 pattern)
    from app.api.health import router as health_router

    api.include_router(health_router)

    # Include v1 routes
    from app.api.v1.routes import router as v1_router

    api.include_router(v1_router)

    # Set custom OpenAPI schema
    api.openapi = lambda: custom_openapi(api)

    draining_app = RequestDrainMiddleware(api, lifecycle)
    browser_policy = apply_browser_policy(draining_app, settings)
    return RequestContextMiddleware(browser_policy, settings)


app = create_app()

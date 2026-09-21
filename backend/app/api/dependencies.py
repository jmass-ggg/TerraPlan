"""
API dependencies for authentication and service injection.

Requirements: 4.1, 4.2, 4.6, 5.1, 6.8, 10.7
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.security import Principal, bearer_scheme, resolve_principal
from app.services.farm import FarmService


async def get_app_settings(request: Request) -> Settings:
    """Return the validated settings bound to this application instance."""
    return request.app.state.settings


async def get_app_session_factory(request: Request):
    """Return the session factory created by this application's lifespan."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise RuntimeError("Application database resources are not initialized")
    return factory


async def get_request_session(
    session_factory=Depends(get_app_session_factory),
):
    """Adapt the explicit session lifecycle helper for FastAPI injection."""
    async for session in get_session(session_factory):
        yield session


async def get_current_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
    settings: Settings = Depends(get_app_settings),
    session_factory=Depends(get_app_session_factory),
) -> Principal:
    """
    Resolve authenticated principal from bearer credentials.
    
    Requirements: 4.1, 4.2, 4.6
    
    Handles:
    - OIDC JWT verification and user resolution
    - Local demo principal with full isolation checks
    - Missing/invalid credentials (returns 401)
    - Dependency failures (returns 503, never switches to demo)
    
    This is the authentication boundary for all protected routes.
    """
    return await resolve_principal(credentials, settings, session_factory)


async def get_farm_service(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
    settings: Settings = Depends(get_app_settings),
) -> FarmService:
    """
    Create farm service for the authenticated principal.
    
    Requirements: 5.1, 6.8, 10.7
    
    Services are bound to:
    - Verified principal's internal user UUID (for ownership)
    - Request-scoped database session (for transaction isolation)
    
    Services own transaction boundaries.
    Session is closed after request completes (not committed by dependency).
    """
    return FarmService(session, principal.user_id, settings)

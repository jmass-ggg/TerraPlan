"""
Health and readiness endpoints.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6

These routes are explicit exceptions to the /api/v1 versioning pattern.
They provide public access for monitoring without authentication.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from app.api.dependencies import get_app_settings, get_app_session_factory
from app.core.config import Settings
from app.core.database import check_readiness

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    """Health check response"""

    status: str
    timestamp: datetime
    non_live: bool


class ReadyResponse(BaseModel):
    """Readiness check response"""

    status: str
    timestamp: datetime
    checks: dict[str, str]


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Process health check",
    description="Returns HTTP 200 when process is serving requests, "
    "independent of database availability.",
)
async def health(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    """
    Public process health check.
    
    Requirements: 3.1
    
    Always returns 200 if the process can handle requests.
    Does not check database or other dependencies.
    """
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(UTC),
        non_live=settings.data_mode.value != "live",
    )


@router.get(
    "/ready",
    response_model=ReadyResponse,
    status_code=status.HTTP_200_OK,
    responses={
        503: {
            "description": "Service not ready - dependency unavailable",
            "model": ReadyResponse,
        }
    },
    summary="Readiness check",
    description="Returns HTTP 200 when ready to serve requests with all "
    "dependencies available. Returns 503 when dependencies are unavailable.",
)
async def ready(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_app_settings),
    session_factory=Depends(get_app_session_factory),
) -> ReadyResponse:
    """
    Public readiness check with bounded dependency verification.
    
    Requirements: 3.2, 3.3, 3.4, 3.5, 3.6
    
    Checks:
    - Database connectivity
    - PostGIS availability
    - Alembic migration head
    - Installation marker/auth mode compatibility
    
    Completes within 5 seconds including acquisition, checks and cleanup.
    Returns 503 for unavailable, timed-out or incompatible dependencies.
    """
    lifecycle = request.app.state.lifecycle
    if lifecycle.shutting_down:
        checks, is_ready = {"lifecycle": "shutting_down"}, False
    else:
        checks, is_ready = await check_readiness(session_factory, settings)
    
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    
    return ReadyResponse(
        status="ready" if is_ready else "unavailable",
        timestamp=datetime.now(UTC),
        checks=checks,
    )

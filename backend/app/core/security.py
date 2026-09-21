"""
Authentication boundary and principal resolution.

Requirements: 1.6, 1.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 12.2
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import InstallationMetadata, InstallationMode, User

from .config import AuthMode, Settings
from .database import create_session_factory
from .exceptions import AuthenticationError, ServiceUnavailableError

logger = logging.getLogger(__name__)

# Fixed demo user identity
DEMO_ISSUER = "https://demo.farmtwin.local"
DEMO_SUBJECT = "demo-user"
# Fixed UUID for demo user (v4 UUID generated for this purpose)
DEMO_USER_UUID = UUID("00000000-0000-0000-0000-000000000001")

# JWKS cache
_jwks_cache: dict[str, Any] | None = None
_jwks_cache_expires: datetime | None = None
JWKS_CACHE_DURATION = timedelta(hours=1)

# Key refresh lock to prevent thundering herd on unknown kid
_key_refresh_lock = asyncio.Lock()
_identity_client: httpx.AsyncClient | None = None


def set_identity_client(client: httpx.AsyncClient | None) -> None:
    """Bind the lifespan-owned identity client used for JWKS requests."""
    global _identity_client
    _identity_client = client


@dataclass(frozen=True)
class Principal:
    """
    Immutable authenticated principal.
    
    Requirements: 4.1, 4.3
    
    Contains:
    - Internal user_id (UUID) for ownership and database operations
    - Trusted issuer and subject from verified credentials
    - Granted permissions from trusted policy/claims
    
    The raw external subject string is NOT used as a database key.
    """

    user_id: UUID
    issuer: str
    subject: str
    permissions: frozenset[str]
    is_demo: bool = False


# Bearer token scheme with auto_error=False for deliberate handling
bearer_scheme = HTTPBearer(auto_error=False)


async def _fetch_jwks(settings: Settings) -> dict[str, Any]:
    """
    Fetch JWKS from configured URL with bounded timeout.
    
    Requirements: 4.4, 4.7
    
    Returns cached keys if available and not expired.
    Network timeout is bounded to prevent hanging verification.
    """
    global _jwks_cache, _jwks_cache_expires

    now = datetime.now(UTC)

    # Return cached keys if valid
    if _jwks_cache is not None and _jwks_cache_expires is not None:
        if now < _jwks_cache_expires:
            return _jwks_cache

    # Fetch with bounded timeout (3 seconds)
    try:
        if _identity_client is not None:
            response = await _identity_client.get(str(settings.auth.jwks_url))
            response.raise_for_status()
            jwks = response.json()
        else:
            # Unit-level calls outside an application lifespan retain bounded,
            # self-contained behavior.
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(str(settings.auth.jwks_url))
                response.raise_for_status()
                jwks = response.json()

        # Cache for configured duration
        _jwks_cache = jwks
        _jwks_cache_expires = now + JWKS_CACHE_DURATION

        return jwks

    except httpx.TimeoutException:
        logger.warning("auth.jwks_timeout", extra={"event": "auth.jwks_timeout"})
        raise
    except httpx.HTTPError as e:
        logger.warning(
            "auth.jwks_failed",
            extra={"event": "auth.jwks_failed", "exception_type": type(e).__name__},
        )
        raise


async def _get_signing_key(kid: str | None, settings: Settings, allow_refresh: bool = True) -> str:
    """
    Get signing key for token verification.
    
    Requirements: 4.4, 4.7
    
    - Uses cached JWKS if available
    - Performs ONE bounded refresh for unknown kid if allowed
    - Returns sanitized 503 when keys unavailable and no cache exists
    - Never falls back to demo mode on verification failure
    """
    try:
        jwks = await _fetch_jwks(settings)
        keys = jwks.get("keys", [])

        # If no kid in token, try first key
        if kid is None:
            if keys:
                return keys[0]
            raise ServiceUnavailableError("No signing keys available")

        # Find key by kid
        for key in keys:
            if key.get("kid") == kid:
                return key

        # Unknown kid - try ONE refresh if allowed
        if allow_refresh:
            async with _key_refresh_lock:
                # Double-check after acquiring lock
                jwks = await _fetch_jwks(settings)
                keys = jwks.get("keys", [])
                for key in keys:
                    if key.get("kid") == kid:
                        return key

        # Key still not found after refresh
        raise AuthenticationError("Invalid signing key", "AUTH_INVALID_TOKEN")

    except (httpx.TimeoutException, httpx.HTTPError):
        # If we have cached keys, allow verification to proceed
        if _jwks_cache is not None:
            keys = _jwks_cache.get("keys", [])
            if kid is None and keys:
                return keys[0]
            for key in keys:
                if key.get("kid") == kid:
                    return key

        # No cache and network failed - fail closed with 503
        raise ServiceUnavailableError(
            "Identity verification service temporarily unavailable"
        )


async def _verify_oidc_token(
    token: str, settings: Settings
) -> tuple[str, str, dict[str, Any]]:
    """
    Verify OIDC JWT token with fail-closed behavior.
    
    Requirements: 1.7, 4.4, 4.5, 4.7
    
    Validates:
    - Signature with configured algorithm allowlist
    - Issuer matches configured issuer
    - Audience matches configured audience
    - Token is not expired (with 30s clock skew)
    - not-before is honored when present
    - Subject is non-empty
    
    Returns (issuer, subject, claims) on success.
    
    Raises AuthenticationError on invalid token.
    Raises ServiceUnavailableError when verification infrastructure unavailable.
    Never falls back to demo mode.
    """
    try:
        # Decode header to get kid (key ID)
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # Get signing key
        signing_key = await _get_signing_key(kid, settings)

        # Pydantic's HttpUrl serializes a host-only URL with a trailing slash,
        # while OIDC issuer claims commonly omit it. Normalize only that
        # synthetic root path; non-root issuer paths remain exact.
        configured_issuer = str(settings.auth.issuer)
        if settings.auth.issuer.path == "/":
            configured_issuer = configured_issuer[:-1]

        # Verify token
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=settings.auth.algorithms,
            issuer=configured_issuer,
            audience=settings.auth.audience,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": False,  # Not required
                "verify_aud": True,
                "verify_iss": True,
                "require_exp": True,
                "require_iat": False,
                "require_nbf": False,
                # python-jose accepts clock skew in the options mapping.
                "leeway": 30,
            },
        )

        # Validate required claims
        issuer = claims.get("iss")
        subject = claims.get("sub")

        if not issuer:
            raise AuthenticationError("Missing issuer claim", "AUTH_INVALID_TOKEN")

        if not subject or not subject.strip():
            raise AuthenticationError(
                "Missing or empty subject claim", "AUTH_INVALID_TOKEN"
            )

        return issuer, subject, claims

    except JWTError as e:
        # Convert JWT errors to standardized authentication error
        error_msg = str(e)
        if "expired" in error_msg.lower():
            raise AuthenticationError("Token expired", "AUTH_INVALID_TOKEN")
        elif "signature" in error_msg.lower():
            raise AuthenticationError("Invalid signature", "AUTH_INVALID_TOKEN")
        elif "audience" in error_msg.lower():
            raise AuthenticationError("Invalid audience", "AUTH_INVALID_TOKEN")
        elif "issuer" in error_msg.lower():
            raise AuthenticationError("Invalid issuer", "AUTH_INVALID_TOKEN")
        else:
            raise AuthenticationError("Invalid token", "AUTH_INVALID_TOKEN")


async def _resolve_or_create_user(
    issuer: str, subject: str, session_factory
) -> UUID:
    """
    Resolve (issuer, subject) to internal user UUID.
    
    Requirements: 4.1
    
    Creates user on first login. Handles concurrent first-login races
    through database uniqueness constraint.
    
    Uses a separate committed transaction to avoid conflicts with
    subsequent request transactions.
    """
    async with session_factory() as session:
        try:
            # Try to find existing user
            result = await session.execute(
                select(User).where(
                    User.issuer == issuer,
                    User.subject == subject,
                )
            )
            user = result.scalar_one_or_none()

            if user:
                return user.id

            # Create new user
            new_user = User(
                issuer=issuer,
                subject=subject,
            )
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)

            logger.info("auth.user_created", extra={"event": "auth.user_created"})

            return new_user.id

        except IntegrityError:
            # Concurrent first-login race - retry read
            await session.rollback()

            result = await session.execute(
                select(User).where(
                    User.issuer == issuer,
                    User.subject == subject,
                )
            )
            user = result.scalar_one_or_none()

            if user:
                return user.id

            # Should not happen - uniqueness violation but user not found
            logger.error(
                "auth.user_creation_race",
                extra={"event": "auth.user_creation_race"},
            )
            raise ServiceUnavailableError("User identity resolution failed")


async def _check_demo_isolation(session: AsyncSession, settings: Settings) -> None:
    """
    Verify demo database marker matches auth configuration.
    
    Requirements: 1.6, 4.3, 12.2
    
    Checks installation_metadata singleton to ensure database mode
    matches the configured auth mode. Mismatches indicate:
    - Demo credentials being used against real user database
    - Real credentials being used against demo database
    
    Both are dangerous configuration errors that must prevent startup/authentication.
    """
    result = await session.execute(select(InstallationMetadata))
    metadata = result.scalar_one_or_none()

    if metadata is None:
        raise ServiceUnavailableError(
            "Installation metadata not found - database not initialized"
        )

    # Validate marker matches auth mode
    if settings.auth.mode == AuthMode.LOCAL_DEMO:
        if metadata.mode != InstallationMode.LOCAL_DEMO:
            raise ServiceUnavailableError(
                "Database marker/auth mode mismatch: "
                "local_demo auth requires local_demo database"
            )
    else:  # OIDC
        if metadata.mode != InstallationMode.AUTHENTICATED:
            raise ServiceUnavailableError(
                "Database marker/auth mode mismatch: "
                "OIDC auth requires authenticated database"
            )


async def _get_demo_principal(session: AsyncSession, settings: Settings) -> Principal:
    """
    Create demo principal with full isolation checks.
    
    Requirements: 1.6, 4.3, 12.2
    
    Validates complete demo policy:
    - Development environment
    - Explicit data mode, independent from the local authentication method
    - Local-only deployment
    - Isolated demo database
    - Correct installation marker
    
    Returns fixed demo principal on success.
    Never created from dependency failures or unverified input.
    """
    # Check database marker matches
    await _check_demo_isolation(session, settings)

    # All validation in Settings ensures demo policy if we get here
    # Return fixed demo principal
    return Principal(
        user_id=DEMO_USER_UUID,
        issuer=DEMO_ISSUER,
        subject=DEMO_SUBJECT,
        permissions=frozenset(),  # No special permissions in demo
        is_demo=True,
    )


async def resolve_principal(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
    session_factory,
) -> Principal:
    """
    Resolve authenticated principal from bearer credentials.
    
    Requirements: 4.1, 4.2, 4.3, 4.6, 4.7
    
    Handles three paths:
    
    1. OIDC mode with credentials:
       - Verify JWT signature, claims and expiry
       - Resolve to internal user UUID
       - Derive permissions from trusted claims
    
    2. OIDC mode without credentials:
       - Return 401 with bearer challenge
    
    3. Local demo mode:
       - Validate complete isolation policy
       - Return fixed demo principal
       - Credentials are ignored (local demo has no tokens)
    
    Never switches to demo on verification failure.
    Rejects client-supplied user IDs in headers/query/body (checked by routes).
    """
    # Local demo mode
    if settings.auth.mode == AuthMode.LOCAL_DEMO:
        # Get session for marker check
        async with session_factory() as session:
            return await _get_demo_principal(session, settings)

    # OIDC mode - credentials required
    if credentials is None:
        raise AuthenticationError(
            "Missing authorization credentials", "AUTH_TOKEN_MISSING"
        )

    # Verify token
    issuer, subject, claims = await _verify_oidc_token(
        credentials.credentials, settings
    )

    # Resolve to internal user
    user_id = await _resolve_or_create_user(issuer, subject, session_factory)

    # Derive permissions from trusted claims
    # TODO: Implement permission derivation from claims/policy
    # For Phase 1, all authenticated users have same permissions
    permissions: frozenset[str] = frozenset()

    return Principal(
        user_id=user_id,
        issuer=issuer,
        subject=subject,
        permissions=permissions,
        is_demo=False,
    )

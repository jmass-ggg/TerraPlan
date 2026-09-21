"""
User profile endpoints.

Requirements: 7.5, 7.6, 7.7, 2.1
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_principal, get_request_session
from app.api.schemas import ReadBaseSchema, TimestampMixin, persisted_datetime_to_utc
from app.core.security import Principal
from app.models.user import User

router = APIRouter(prefix="/me", tags=["Profile"])


class UpdateProfileRequest(BaseModel):
    """
    Request body for updating user profile.

    Requirements: 2.1
    """

    display_name: str | None = Field(
        None,
        max_length=255,
        description="Display name to show in the UI. Pass null to clear.",
    )


class UserProfileResponse(ReadBaseSchema, TimestampMixin):
    """
    User profile response.
    
    Requirements: 7.5, 7.7
    """

    id: UUID = Field(description="Internal user UUID")
    issuer: str = Field(description="Identity provider issuer")
    subject: str = Field(description="User subject from identity provider")
    email: str | None = Field(None, description="Optional email address")
    display_name: str | None = Field(None, description="Optional display name")
    preferences: dict = Field(
        default_factory=dict, description="User preferences"
    )


@router.get(
    "",
    response_model=UserProfileResponse,
    summary="Get current user profile",
    description="Returns the authenticated user's profile information.",
)
async def get_profile(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> UserProfileResponse:
    """
    Get current user profile.
    
    Requirements: 7.5, 7.6, 7.7
    
    Returns profile information for the authenticated principal.
    This endpoint verifies authentication works and provides user context.
    """
    result = await session.execute(select(User).where(User.id == principal.user_id))
    user = result.scalar_one()
    return UserProfileResponse(
        id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        email=user.email,
        display_name=user.display_name,
        preferences=user.preferences,
        created_at=persisted_datetime_to_utc(user.created_at),
        updated_at=persisted_datetime_to_utc(user.updated_at),
    )


@router.patch(
    "",
    response_model=UserProfileResponse,
    summary="Update current user profile",
    description="Updates the authenticated user's profile fields. Only supplied fields are changed.",
)
async def update_profile(
    body: UpdateProfileRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> UserProfileResponse:
    """
    Update current user profile.

    Requirements: 2.1

    Accepts display_name update and returns the updated profile.
    Only provided fields are modified; omitted fields remain unchanged.
    """
    result = await session.execute(select(User).where(User.id == principal.user_id))
    user = result.scalar_one()

    # Only update fields that were explicitly provided in the request
    if body.display_name is not None:
        user.display_name = body.display_name
    elif "display_name" in body.model_fields_set:
        # Explicitly set to null — clear the field
        user.display_name = None

    await session.commit()
    await session.refresh(user)

    return UserProfileResponse(
        id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        email=user.email,
        display_name=user.display_name,
        preferences=user.preferences,
        created_at=persisted_datetime_to_utc(user.created_at),
        updated_at=persisted_datetime_to_utc(user.updated_at),
    )

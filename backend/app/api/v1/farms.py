"""
Farm persistence endpoints.

Requirements: 5.1, 5.2, 7.5, 7.6, 7.7, 8.1, 8.2, 8.3, 8.6

Phase 4 extends the ownership-scoped reads with create, edit and delete.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from geoalchemy2.shape import to_shape
from pydantic import BaseModel, Field
from shapely.geometry import mapping

from app.api.dependencies import get_current_principal, get_farm_service
from app.api.schemas import (
    ErrorResponse,
    PaginatedResponse,
    PaginationParams,
    ReadBaseSchema,
    TimestampMixin,
    persisted_datetime_to_utc,
)
from app.core.security import Principal
from app.services.farm import FarmService
from app.api.v1.farm_schemas import FarmCreate, FarmUpdate

router = APIRouter(prefix="/farms", tags=["Farms"])


async def get_pagination_params(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginationParams:
    """Build validated pagination without a synchronous class dependency."""
    return PaginationParams(limit=limit, offset=offset)


class GeometryRevisionResponse(ReadBaseSchema):
    """
    Farm geometry revision response.
    
    Requirements: 7.5, 8.6
    """

    id: UUID = Field(description="Revision UUID")
    revision: int = Field(description="Sequential revision number", ge=1)
    geometry: dict = Field(
        description="GeoJSON polygon geometry in WGS84 (SRID 4326)"
    )
    centroid: dict = Field(description="GeoJSON centroid point")
    label_point: dict = Field(description="GeoJSON interior label point")
    hectares: float = Field(description="Farm area in hectares", gt=0)
    created_at: datetime = Field(description="Revision creation timestamp")


class FarmSummaryResponse(ReadBaseSchema, TimestampMixin):
    """
    Farm summary for list responses.
    
    Requirements: 7.5, 7.7
    """

    id: UUID = Field(description="Farm UUID")
    name: str = Field(description="Farm name")
    current_geometry_revision: int = Field(
        description="Current geometry revision number", ge=1
    )
    hectares: float = Field(
        description="Current farm area in hectares", gt=0
    )


class FarmDetailResponse(ReadBaseSchema, TimestampMixin):
    """
    Detailed farm response with current geometry.
    
    Requirements: 7.5, 7.7, 8.6
    """

    id: UUID = Field(description="Farm UUID")
    name: str = Field(description="Farm name")
    current_geometry_revision: int = Field(
        description="Current geometry revision number", ge=1
    )
    current_geometry: GeometryRevisionResponse = Field(
        description="Current saved geometry revision"
    )
    analysis_job_id: UUID | None = Field(
        default=None,
        description="Analysis job ID enqueued for the latest geometry revision (Requirements 1.2)",
    )


class FarmListResponse(PaginatedResponse):
    """
    Paginated farm list response.
    
    Requirements: 7.5, 8.1, 8.2, 8.3, 8.6
    """

    items: list[FarmSummaryResponse] = Field(
        description="Farms in this page"
    )


def _geometry_to_geojson(wkb_element) -> dict:
    """
    Convert PostGIS geometry to GeoJSON dict.
    
    Requirements: 7.5, 8.6
    """
    # Convert WKBElement to Shapely geometry
    shape = to_shape(wkb_element)
    # Convert Shapely geometry to GeoJSON dict
    return mapping(shape)


def _farm_to_detail(farm, analysis_job_id: UUID | None = None) -> FarmDetailResponse:
    current_geo = farm.current_geometry
    return FarmDetailResponse(
        id=farm.id,
        name=farm.name,
        current_geometry_revision=farm.current_geometry_revision,
        current_geometry=GeometryRevisionResponse(
            id=current_geo.id,
            revision=current_geo.revision,
            geometry=_geometry_to_geojson(current_geo.geometry),
            centroid=_geometry_to_geojson(current_geo.centroid),
            label_point=_geometry_to_geojson(current_geo.label_point),
            hectares=current_geo.hectares,
            created_at=persisted_datetime_to_utc(current_geo.created_at),
        ),
        created_at=persisted_datetime_to_utc(farm.created_at),
        updated_at=persisted_datetime_to_utc(farm.updated_at),
        analysis_job_id=analysis_job_id,
    )


@router.post(
    "",
    response_model=FarmDetailResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Create a farm",
)
async def create_farm(
    data: FarmCreate,
    response: Response,
    idempotency_key: UUID | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="Client-generated UUID used to make retries safe",
    ),
    principal: Principal = Depends(get_current_principal),
    farm_service: FarmService = Depends(get_farm_service),
) -> FarmDetailResponse:
    result = await farm_service.create_farm_request(data, idempotency_key)
    response.headers["Location"] = f"/api/v1/farms/{result.farm.id}"
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _farm_to_detail(result.farm, analysis_job_id=result.analysis_job_id)


@router.get(
    "",
    response_model=FarmListResponse,
    summary="List farms",
    description="Returns paginated list of farms owned by the authenticated user.",
)
async def list_farms(
    principal: Principal = Depends(get_current_principal),
    pagination: PaginationParams = Depends(get_pagination_params),
    farm_service: FarmService = Depends(get_farm_service),
) -> FarmListResponse:
    """
    List farms owned by the authenticated user.
    
    Requirements: 5.1, 5.2, 7.5, 7.6, 7.7, 8.1, 8.2, 8.3, 8.6
    
    Returns ownership-scoped paginated farm list with:
    - Stable deterministic ordering (by creation time, then ID)
    - Current farm metadata including area from current geometry
    - Scoped total count
    
    May return empty list if user has no farms.
    """
    # Get farms and count from service
    farms, total = await farm_service.list_farms(
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by="created_at",
    )

    # Convert to response models
    items = [
        FarmSummaryResponse(
            id=farm.id,
            name=farm.name,
            current_geometry_revision=farm.current_geometry_revision,
            hectares=farm.current_geometry.hectares,
            created_at=persisted_datetime_to_utc(farm.created_at),
            updated_at=persisted_datetime_to_utc(farm.updated_at),
        )
        for farm in farms
    ]

    return FarmListResponse(
        items=items,
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{farm_id}",
    response_model=FarmDetailResponse,
    summary="Get farm details",
    description="Returns detailed farm information with current geometry "
    "for the specified farm owned by the authenticated user.",
    responses={
        404: {
            "description": "Farm not found or not owned by user",
            "model": ErrorResponse,
        }
    },
)
async def get_farm(
    farm_id: UUID,
    principal: Principal = Depends(get_current_principal),
    farm_service: FarmService = Depends(get_farm_service),
) -> FarmDetailResponse:
    """
    Get detailed farm information.
    
    Requirements: 5.1, 5.2, 7.5, 7.6, 7.7, 8.6
    
    Returns:
    - Farm metadata
    - Current geometry revision with full geometry data
    
    Returns 404 for both:
    - Nonexistent farms
    - Farms owned by other users
    
    This prevents cross-user existence disclosure.
    """
    farm = await farm_service.get_farm(farm_id)
    return _farm_to_detail(farm)


@router.patch(
    "/{farm_id}",
    response_model=FarmDetailResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Update a farm",
)
async def update_farm(
    farm_id: UUID,
    data: FarmUpdate,
    principal: Principal = Depends(get_current_principal),
    farm_service: FarmService = Depends(get_farm_service),
) -> FarmDetailResponse:
    farm = await farm_service.update_farm_request(farm_id, data)
    return _farm_to_detail(farm)


@router.delete(
    "/{farm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Delete a farm",
)
async def delete_farm(
    farm_id: UUID,
    principal: Principal = Depends(get_current_principal),
    farm_service: FarmService = Depends(get_farm_service),
) -> Response:
    await farm_service.delete_farm(farm_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

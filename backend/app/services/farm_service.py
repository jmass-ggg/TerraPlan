"""Transactional farm creation, editing, retrieval and deletion workflows."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import uuid

from geoalchemy2.shape import to_shape
from shapely import wkb
from shapely.geometry import Point, shape
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.farm_schemas import FarmCreate, FarmUpdate
from app.core.exceptions import FarmValidationError, IdempotencyConflict
from app.core.security import Principal
from app.domain.geometry import ValidationResult, validate_polygon
from app.repositories.farm import FarmRepository
from app.services.base import BaseService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FarmCreationResult:
    farm: object
    created: bool
    analysis_job_id: uuid.UUID | None = None


def _validated_wkb(
    geometry: dict,
) -> tuple[bytes, bytes, bytes, float, ValidationResult]:
    result = validate_polygon(geometry)
    if not result.valid:
        raise FarmValidationError(
            "body.geometry",
            result.error_code or "GEOMETRY_INVALID",
            result.error_detail or "Farm boundary is invalid",
        )

    polygon = shape(geometry)
    centroid = Point(result.centroid_lon, result.centroid_lat)
    label_point = Point(result.label_point_lon, result.label_point_lat)
    return (
        wkb.dumps(polygon, srid=4326),
        wkb.dumps(centroid, srid=4326),
        wkb.dumps(label_point, srid=4326),
        float(result.area_ha),
        result,
    )


class FarmService(BaseService):
    """Request-scoped service bound to one verified internal owner UUID."""

    def __init__(
        self,
        session: AsyncSession,
        owner_id: uuid.UUID,
        settings=None,
    ):
        super().__init__(session)
        self.owner_id = owner_id
        self.settings = settings
        self.repository = FarmRepository(session, owner_id)

    async def get_farm(self, farm_id: uuid.UUID):
        return await self.repository.get_by_id(farm_id)

    async def list_farms(
        self,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "created_at",
    ):
        farms = await self.repository.list_page(limit, offset, sort_by)
        return farms, await self.repository.count()

    async def create_farm_request(
        self,
        data: FarmCreate,
        idempotency_key: uuid.UUID | None = None,
    ) -> FarmCreationResult:
        # Import here to avoid circular imports
        from app.services.snapshot_service import enqueue_analysis_job

        async def operation() -> FarmCreationResult:
            if idempotency_key is not None:
                existing = await self.repository.get_by_idempotency_key(
                    idempotency_key
                )
                if existing is not None:
                    incoming = shape(data.geometry)
                    persisted = to_shape(existing.current_geometry.geometry)
                    if existing.name != data.name or not persisted.equals_exact(
                        incoming, 0.0
                    ):
                        raise IdempotencyConflict()
                    return FarmCreationResult(existing, created=False)

            geometry_wkb, centroid_wkb, label_wkb, hectares, _ = _validated_wkb(
                data.geometry
            )
            farm = await self.repository.create(
                name=data.name,
                geometry_wkb=geometry_wkb,
                centroid_wkb=centroid_wkb,
                label_point_wkb=label_wkb,
                hectares=hectares,
                idempotency_key=idempotency_key,
            )

            # Enqueue analysis job for the new farm (Requirements 1.1, 1.2)
            analysis_job_id: uuid.UUID | None = None
            try:
                job = await enqueue_analysis_job(
                    farm_id=farm.id,
                    geometry_revision=farm.current_geometry_revision,
                    session=self.session,
                    settings=self.settings,
                )
                analysis_job_id = job.id
            except Exception as exc:
                logger.warning(
                    "Failed to enqueue analysis job for farm %s: %s. "
                    "Farm is saved; job was not enqueued.",
                    farm.id,
                    exc,
                )

            return FarmCreationResult(farm, created=True, analysis_job_id=analysis_job_id)

        return await self.execute_write_transaction(operation, materialize=False)

    async def create_farm(
        self,
        name: str,
        geometry_wkb: bytes,
        centroid_wkb: bytes,
        label_point_wkb: bytes,
        hectares: float,
        idempotency_key: uuid.UUID | None = None,
    ):
        """Compatibility entry point retained for established fixture callers."""
        async def operation():
            return await self.repository.create(
                name=name,
                geometry_wkb=geometry_wkb,
                centroid_wkb=centroid_wkb,
                label_point_wkb=label_point_wkb,
                hectares=hectares,
                idempotency_key=idempotency_key,
            )

        return await self.execute_write_transaction(operation, materialize=False)

    async def update_farm_request(
        self, farm_id: uuid.UUID, data: FarmUpdate
    ):
        # Import here to avoid circular imports
        from app.services.snapshot_service import enqueue_analysis_job

        spatial = _validated_wkb(data.geometry) if data.geometry is not None else None

        async def operation():
            farm = None
            if spatial is not None:
                geometry_wkb, centroid_wkb, label_wkb, hectares, _ = spatial
                farm = await self.repository.update_geometry(
                    farm_id=farm_id,
                    expected_revision=int(data.expected_revision),
                    geometry_wkb=geometry_wkb,
                    centroid_wkb=centroid_wkb,
                    label_point_wkb=label_wkb,
                    hectares=hectares,
                )
            if data.name is not None:
                farm = await self.repository.update_name(farm_id, data.name)

            # Enqueue analysis job when geometry was updated (Requirements 1.1, 1.2)
            if spatial is not None and farm is not None:
                try:
                    await enqueue_analysis_job(
                        farm_id=farm.id,
                        geometry_revision=farm.current_geometry_revision,
                        session=self.session,
                        settings=self.settings,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to enqueue analysis job for farm %s after geometry update: %s. "
                        "Farm is saved; job was not enqueued.",
                        farm.id,
                        exc,
                    )

            return farm

        return await self.execute_write_transaction(operation, materialize=False)

    async def update_farm(
        self, farm_id: uuid.UUID, name: str | None = None
    ):
        """Compatibility entry point for Phase 1 name-only service tests."""
        async def operation():
            return await self.repository.update(farm_id, name=name)

        return await self.execute_write_transaction(operation, materialize=False)

    async def delete_farm(self, farm_id: uuid.UUID) -> None:
        async def operation() -> None:
            await self.repository.delete(farm_id)

        await self.execute_write_transaction(operation, materialize=False)

    async def get_geometry_revision(
        self, farm_id: uuid.UUID, revision: int
    ):
        return await self.repository.get_geometry_revision(farm_id, revision)


async def create_farm(
    session: AsyncSession,
    principal: Principal,
    data: FarmCreate,
    idempotency_key: uuid.UUID | None = None,
) -> FarmCreationResult:
    return await FarmService(session, principal.user_id).create_farm_request(
        data, idempotency_key
    )


async def update_farm(
    session: AsyncSession,
    principal: Principal,
    farm_id: uuid.UUID,
    data: FarmUpdate,
):
    return await FarmService(session, principal.user_id).update_farm_request(
        farm_id, data
    )


async def delete_farm(
    session: AsyncSession,
    principal: Principal,
    farm_id: uuid.UUID,
) -> None:
    await FarmService(session, principal.user_id).delete_farm(farm_id)

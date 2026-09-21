"""Ownership-scoped persistence for farms and immutable geometry revisions."""

from __future__ import annotations

import uuid

from geoalchemy2.elements import WKBElement
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import StaleRevisionError
from app.models.farm import Farm, FarmGeometryRevision
from app.models.snapshot import AnalysisJob, AnalysisSnapshot
from app.repositories.base import NotFoundError, OwnedRepository


class FarmRepository(OwnedRepository[Farm]):
    """Farm data access with ownership embedded in every read and mutation."""

    SORTABLE_COLUMNS = {"created_at", "name", "id"}

    def __init__(self, session: AsyncSession, owner_id: uuid.UUID):
        super().__init__(session, owner_id)

    async def get_by_id(self, farm_id: uuid.UUID) -> Farm:
        result = await self.session.execute(
            select(Farm)
            .where(Farm.id == farm_id, Farm.user_id == self.owner_id)
            .options(selectinload(Farm.current_geometry))
            .execution_options(populate_existing=True)
        )
        farm = result.scalar_one_or_none()
        if farm is None:
            raise NotFoundError(f"Farm {farm_id} not found")
        return farm

    async def get_by_idempotency_key(
        self, idempotency_key: uuid.UUID
    ) -> Farm | None:
        result = await self.session.execute(
            select(Farm)
            .where(
                Farm.user_id == self.owner_id,
                Farm.idempotency_key == idempotency_key,
            )
            .options(selectinload(Farm.current_geometry))
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_page(
        self,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "created_at",
    ) -> list[Farm]:
        self._validate_sortable_column(sort_by, self.SORTABLE_COLUMNS)
        result = await self.session.execute(
            select(Farm)
            .where(Farm.user_id == self.owner_id)
            .options(selectinload(Farm.current_geometry))
            .order_by(getattr(Farm, sort_by), Farm.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Farm).where(Farm.user_id == self.owner_id)
        )
        return result.scalar_one()

    async def create(
        self,
        name: str,
        geometry_wkb: bytes,
        centroid_wkb: bytes,
        label_point_wkb: bytes,
        hectares: float,
        idempotency_key: uuid.UUID | None = None,
    ) -> Farm:
        farm_id = uuid.uuid4()
        farm = Farm(
            id=farm_id,
            user_id=self.owner_id,
            name=name,
            idempotency_key=idempotency_key,
            current_geometry_revision=1,
        )
        revision = FarmGeometryRevision(
            id=uuid.uuid4(),
            farm_id=farm_id,
            revision=1,
            geometry=WKBElement(geometry_wkb, srid=4326),
            centroid=WKBElement(centroid_wkb, srid=4326),
            label_point=WKBElement(label_point_wkb, srid=4326),
            hectares=hectares,
        )
        self.session.add_all((farm, revision))
        await self.flush()
        return await self.get_by_id(farm_id)

    async def update_geometry(
        self,
        farm_id: uuid.UUID,
        expected_revision: int,
        geometry_wkb: bytes,
        centroid_wkb: bytes,
        label_point_wkb: bytes,
        hectares: float,
    ) -> Farm:
        """Atomically claim the next revision, then insert its immutable row."""
        next_revision = expected_revision + 1
        claimed = await self.session.execute(
            update(Farm)
            .where(
                Farm.id == farm_id,
                Farm.user_id == self.owner_id,
                Farm.current_geometry_revision == expected_revision,
            )
            .values(current_geometry_revision=next_revision)
            .returning(Farm.id)
        )
        if claimed.scalar_one_or_none() is None:
            owned = await self.session.scalar(
                select(Farm.id).where(
                    Farm.id == farm_id, Farm.user_id == self.owner_id
                )
            )
            if owned is None:
                raise NotFoundError(f"Farm {farm_id} not found")
            raise StaleRevisionError()

        self.session.add(
            FarmGeometryRevision(
                id=uuid.uuid4(),
                farm_id=farm_id,
                revision=next_revision,
                geometry=WKBElement(geometry_wkb, srid=4326),
                centroid=WKBElement(centroid_wkb, srid=4326),
                label_point=WKBElement(label_point_wkb, srid=4326),
                hectares=hectares,
            )
        )
        await self.flush()
        return await self.get_by_id(farm_id)

    async def update_name(self, farm_id: uuid.UUID, name: str) -> Farm:
        result = await self.session.execute(
            update(Farm)
            .where(Farm.id == farm_id, Farm.user_id == self.owner_id)
            .values(name=name)
            .returning(Farm.id)
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError(f"Farm {farm_id} not found")
        await self.flush()
        return await self.get_by_id(farm_id)

    async def update(
        self, farm_id: uuid.UUID, name: str | None = None
    ) -> Farm:
        """Compatibility wrapper for the Phase 1 repository interface."""
        if name is None:
            return await self.get_by_id(farm_id)
        return await self.update_name(farm_id, name)

    async def delete(self, farm_id: uuid.UUID) -> None:
        """Delete a farm and all farm-owned derived data in FK-safe order.

        Deletion order:
        1. analysis_snapshots (removes the RESTRICT FK that would block job deletion)
        2. analysis_jobs cascade-delete automatically once snapshots are gone
        3. saved_scenarios CASCADE-delete automatically with the farm
        4. farm_geometry_revisions CASCADE-delete with the farm
        5. Delete the farm row itself

        The "Delete permanently" action is confirmed by the user; this is the
        intended behavior.
        """
        await self.get_by_id(farm_id)

        # 1. Delete all analysis snapshots for this farm.
        #    AnalysisSnapshot.farm_id has ondelete=RESTRICT, so we must delete
        #    snapshots explicitly before deleting the farm or its jobs.
        await self.session.execute(
            sql_delete(AnalysisSnapshot).where(AnalysisSnapshot.farm_id == farm_id)
        )

        # 2. Delete all analysis jobs for this farm.
        #    AnalysisJob.farm_id has ondelete=CASCADE but we do this explicitly
        #    to ensure they are gone before we delete the farm row (avoids
        #    any trigger/constraint ordering issues across sessions).
        await self.session.execute(
            sql_delete(AnalysisJob).where(AnalysisJob.farm_id == farm_id)
        )

        # 3. Delete the farm. SavedScenarios and FarmGeometryRevisions will
        #    cascade-delete automatically (both have ondelete=CASCADE).
        deleted = await self.session.execute(
            sql_delete(Farm)
            .where(Farm.id == farm_id, Farm.user_id == self.owner_id)
            .returning(Farm.id)
        )
        if deleted.scalar_one_or_none() is None:
            raise NotFoundError(f"Farm {farm_id} not found")
        await self.flush()

    async def get_geometry_revision(
        self, farm_id: uuid.UUID, revision: int
    ) -> FarmGeometryRevision:
        await self.get_by_id(farm_id)
        result = await self.session.execute(
            select(FarmGeometryRevision).where(
                FarmGeometryRevision.farm_id == farm_id,
                FarmGeometryRevision.revision == revision,
            )
        )
        geometry_revision = result.scalar_one_or_none()
        if geometry_revision is None:
            raise NotFoundError(
                f"Geometry revision {revision} not found for farm {farm_id}"
            )
        return geometry_revision

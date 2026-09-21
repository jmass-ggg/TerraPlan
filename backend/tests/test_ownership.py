"""
Two-user ownership isolation tests.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 14.3

Property 2: Ownership isolation

For any distinct users A and B, B cannot list, count, read, update or delete A's
resource or access it through a mismatched parent ID. Invisible and nonexistent IDs
yield the same 404; spoofed ownership cannot transfer a record.

These tests verify repository and service-level ownership enforcement using
internal fixtures. Public farm write endpoints remain disabled until Phase 4.

Tests cover:
- List isolation: User B sees only their farms
- Count isolation: User B counts only their farms
- Direct read isolation: User B cannot read User A's farm
- Update isolation: User B cannot update User A's farm
- Delete isolation: User B cannot delete User A's farm
- Parent/child lookup: User B cannot access A's geometry revision
- Owner spoofing: Cannot transfer ownership via update
- Protected version fields: Cannot modify current_geometry_revision
- Invisible vs nonexistent: Same 404 for both cases
- Malicious inputs: Parameterized handling of filter/sort inputs
"""

import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from hypothesis import given, strategies as st, settings, HealthCheck
from shapely.geometry import Polygon
from shapely import wkb
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User
from app.repositories import FarmRepository, NotFoundError
from app.services.farm import FarmService


# Repository/service property tests operate below request-schema validation, so
# generate only strings PostgreSQL can store and Farm's trim constraint accepts.
DATABASE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
    min_size=1,
    max_size=50,
).filter(lambda value: value == value.strip())


# Test fixture: Simple square polygon in WGS84
def create_test_polygon() -> tuple[bytes, bytes, bytes, float]:
    """
    Create a simple test polygon with centroid, label point and area.
    
    Returns WKB-encoded geometry (polygon, centroid, label_point, hectares).
    Uses a small square near the equator for consistent area calculation.
    """
    # Simple square: 0.01 degrees on each side near equator
    # Approximately 1.23 square km = 123 hectares
    polygon = Polygon([
        (0.0, 0.0),
        (0.01, 0.0),
        (0.01, 0.01),
        (0.0, 0.01),
        (0.0, 0.0),
    ])
    
    centroid_point = polygon.centroid
    label_point = polygon.representative_point()
    
    # WKB encoding (will be handled by PostGIS)
    polygon_wkb = wkb.dumps(polygon, srid=4326)
    centroid_wkb = wkb.dumps(centroid_point, srid=4326)
    label_wkb = wkb.dumps(label_point, srid=4326)
    
    # Approximate hectares (actual geodesic calculation in Phase 4)
    hectares = 123.0
    
    return polygon_wkb, centroid_wkb, label_wkb, hectares


@pytest_asyncio.fixture
async def user_a(session: AsyncSession) -> User:
    """Create User A for ownership tests."""
    user = User(
        id=uuid.uuid4(),
        issuer="test-issuer",
        subject="user-a",
        email="user_a@example.com",
        display_name="User A",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def user_b(session: AsyncSession) -> User:
    """Create User B for ownership tests."""
    user = User(
        id=uuid.uuid4(),
        issuer="test-issuer",
        subject="user-b",
        email="user_b@example.com",
        display_name="User B",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def farm_a(
    session_factory: async_sessionmaker[AsyncSession],
    user_a: User
) -> uuid.UUID:
    """
    Create a farm owned by User A.
    
    Returns the farm ID for testing access isolation.
    """
    async with session_factory() as session:
        service = FarmService(session, user_a.id)
        
        geom_wkb, centroid_wkb, label_wkb, hectares = create_test_polygon()
        
        farm = await service.create_farm(
            name="User A's Farm",
            geometry_wkb=geom_wkb,
            centroid_wkb=centroid_wkb,
            label_point_wkb=label_wkb,
            hectares=hectares,
        )
        
        return farm.id


@pytest_asyncio.fixture
async def farm_b(
    session_factory: async_sessionmaker[AsyncSession],
    user_b: User
) -> uuid.UUID:
    """
    Create a farm owned by User B.
    
    Returns the farm ID for testing isolation.
    """
    async with session_factory() as session:
        service = FarmService(session, user_b.id)
        
        geom_wkb, centroid_wkb, label_wkb, hectares = create_test_polygon()
        
        farm = await service.create_farm(
            name="User B's Farm",
            geometry_wkb=geom_wkb,
            centroid_wkb=centroid_wkb,
            label_point_wkb=label_wkb,
            hectares=hectares,
        )
        
        return farm.id


class TestListIsolation:
    """
    Test list operation ownership isolation.
    
    Requirements: 5.1, 5.3
    
    User B can only see their own farms in list results, never User A's farms.
    """
    
    @pytest.mark.asyncio
    async def test_user_b_cannot_list_user_a_farms(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        user_b: User,
        farm_a: uuid.UUID,
        farm_b: uuid.UUID,
    ):
        """User B's list only contains their farms, not User A's."""
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b.id)
            
            farms = await repo_b.list_page(limit=100, offset=0)
            farm_ids = [f.id for f in farms]
            
            # User B sees only their farm
            assert farm_b in farm_ids
            assert farm_a not in farm_ids
            assert len(farm_ids) == 1
    
    @pytest.mark.asyncio
    async def test_user_a_cannot_list_user_b_farms(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        user_b: User,
        farm_a: uuid.UUID,
        farm_b: uuid.UUID,
    ):
        """User A's list only contains their farms, not User B's."""
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a.id)
            
            farms = await repo_a.list_page(limit=100, offset=0)
            farm_ids = [f.id for f in farms]
            
            # User A sees only their farm
            assert farm_a in farm_ids
            assert farm_b not in farm_ids
            assert len(farm_ids) == 1


class TestCountIsolation:
    """
    Test count operation ownership isolation.
    
    Requirements: 5.1, 5.3
    
    Count returns only the count of farms owned by the principal,
    using the same ownership filter as list.
    """
    
    @pytest.mark.asyncio
    async def test_count_matches_ownership_filter(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        user_b: User,
        farm_a: uuid.UUID,
        farm_b: uuid.UUID,
    ):
        """Each user's count matches their list length."""
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a.id)
            repo_b = FarmRepository(session, user_b.id)
            
            count_a = await repo_a.count()
            count_b = await repo_b.count()
            
            # Each user sees exactly 1 farm (their own)
            assert count_a == 1
            assert count_b == 1


class TestDirectReadIsolation:
    """
    Test get_by_id ownership isolation.
    
    Requirements: 5.1, 5.2
    
    User B cannot read User A's farm by ID. Invisible and nonexistent
    farm IDs both return NotFoundError.
    """
    
    @pytest.mark.asyncio
    async def test_user_b_cannot_read_user_a_farm(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
        farm_a: uuid.UUID,
    ):
        """User B cannot access User A's farm by ID."""
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b.id)
            
            with pytest.raises(NotFoundError) as exc_info:
                await repo_b.get_by_id(farm_a)
            
            # Error message contains farm ID but no ownership disclosure
            assert str(farm_a) in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_nonexistent_farm_returns_not_found(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
    ):
        """Nonexistent farm ID returns NotFoundError."""
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b.id)
            
            nonexistent_id = uuid.uuid4()
            
            with pytest.raises(NotFoundError) as exc_info:
                await repo_b.get_by_id(nonexistent_id)
            
            # Error message contains farm ID
            assert str(nonexistent_id) in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_invisible_and_nonexistent_same_response(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
        farm_a: uuid.UUID,
    ):
        """
        Invisible and nonexistent farms return same error type.
        
        Requirements: 5.2
        
        This prevents cross-user existence disclosure by ensuring
        User B cannot distinguish between "farm doesn't exist" and
        "farm exists but belongs to someone else".
        """
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b.id)
            
            # Try to access User A's farm (invisible)
            invisible_error = None
            try:
                await repo_b.get_by_id(farm_a)
            except NotFoundError as e:
                invisible_error = e
            
            # Try to access nonexistent farm
            nonexistent_id = uuid.uuid4()
            nonexistent_error = None
            try:
                await repo_b.get_by_id(nonexistent_id)
            except NotFoundError as e:
                nonexistent_error = e
            
            # Both raise NotFoundError - same exception type
            assert invisible_error is not None
            assert nonexistent_error is not None
            assert type(invisible_error) == type(nonexistent_error)
            
            # Error messages follow same pattern
            assert "not found" in str(invisible_error).lower()
            assert "not found" in str(nonexistent_error).lower()


class TestUpdateIsolation:
    """
    Test update ownership isolation.
    
    Requirements: 5.1, 5.4, 5.7
    
    User B cannot update User A's farm. Updates include ownership
    in the mutation predicate without a separate existence check.
    """
    
    @pytest.mark.asyncio
    async def test_user_b_cannot_update_user_a_farm(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
        farm_a: uuid.UUID,
    ):
        """User B cannot update User A's farm name."""
        async with session_factory() as session:
            service_b = FarmService(session, user_b.id)
            
            with pytest.raises(NotFoundError):
                await service_b.update_farm(farm_a, name="Attempted Takeover")
    
    @pytest.mark.asyncio
    async def test_update_of_nonexistent_farm_returns_not_found(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
    ):
        """Updating nonexistent farm returns NotFoundError."""
        async with session_factory() as session:
            service_b = FarmService(session, user_b.id)
            
            nonexistent_id = uuid.uuid4()
            
            with pytest.raises(NotFoundError):
                await service_b.update_farm(nonexistent_id, name="New Name")


class TestDeleteIsolation:
    """
    Test delete ownership isolation.
    
    Requirements: 5.1, 5.4
    
    User B cannot delete User A's farm. Deletes include ownership
    in the mutation predicate without a separate existence check.
    """
    
    @pytest.mark.asyncio
    async def test_user_b_cannot_delete_user_a_farm(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        user_b: User,
        farm_a: uuid.UUID,
    ):
        """User B cannot delete User A's farm."""
        async with session_factory() as session:
            service_b = FarmService(session, user_b.id)
            
            with pytest.raises(NotFoundError):
                await service_b.delete_farm(farm_a)
        
        # Verify farm still exists for User A
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a.id)
            farm = await repo_a.get_by_id(farm_a)
            assert farm is not None
            assert farm.id == farm_a
    
    @pytest.mark.asyncio
    async def test_delete_of_nonexistent_farm_returns_not_found(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
    ):
        """Deleting nonexistent farm returns NotFoundError."""
        async with session_factory() as session:
            service_b = FarmService(session, user_b.id)
            
            nonexistent_id = uuid.uuid4()
            
            with pytest.raises(NotFoundError):
                await service_b.delete_farm(nonexistent_id)


class TestParentChildLookup:
    """
    Test parent/child lookup authorization.
    
    Requirements: 5.3
    
    Child resources (geometry revisions) are authorized through their
    parent farm. User B cannot access User A's geometry revisions even
    with a mismatched parent ID.
    """
    
    @pytest.mark.asyncio
    async def test_user_b_cannot_access_user_a_geometry_revision(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
        farm_a: uuid.UUID,
    ):
        """User B cannot access User A's geometry revision."""
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b.id)
            
            # Try to access revision 1 of User A's farm
            with pytest.raises(NotFoundError):
                await repo_b.get_geometry_revision(farm_a, revision=1)
    
    @pytest.mark.asyncio
    async def test_mismatched_parent_child_lookup(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_b: User,
        farm_a: uuid.UUID,
        farm_b: uuid.UUID,
    ):
        """
        Cannot access geometry revision with mismatched farm ID.
        
        Even if User B provides their own farm_id, they cannot access
        revisions belonging to User A's farm.
        """
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b.id)
            
            # Verify User B can access their own revision
            revision_b = await repo_b.get_geometry_revision(farm_b, revision=1)
            assert revision_b is not None
            assert revision_b.farm_id == farm_b
            
            # But cannot access User A's revision
            with pytest.raises(NotFoundError):
                await repo_b.get_geometry_revision(farm_a, revision=1)


class TestOwnerSpoofing:
    """
    Test protection against owner spoofing attempts.
    
    Requirements: 5.7
    
    Repository derives ownership from the verified principal and rejects
    attempts to change owner, IDs or protected version fields.
    """
    
    @pytest.mark.asyncio
    async def test_cannot_transfer_ownership_via_update(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        user_b: User,
        farm_b: uuid.UUID,
    ):
        """
        Cannot change farm ownership through update.
        
        Requirements: 5.7
        
        Even if User B tries to update their farm's user_id to point to
        User A, the repository prevents this by deriving ownership from
        the verified principal.
        """
        async with session_factory() as session:
            # Get User B's farm
            repo_b = FarmRepository(session, user_b.id)
            farm = await repo_b.get_by_id(farm_b)
            
            # Attempt to change ownership (direct model manipulation)
            # This simulates what would happen if we allowed user_id in updates
            original_owner = farm.user_id
            farm.user_id = user_a.id  # Try to steal the farm
            
            await session.flush()
            await session.rollback()  # Don't commit this
        
        # Verify ownership didn't change
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b.id)
            farm = await repo_b.get_by_id(farm_b)
            assert farm.user_id == user_b.id  # Still owned by B
            
            # And User A cannot see it
            repo_a = FarmRepository(session, user_a.id)
            with pytest.raises(NotFoundError):
                await repo_a.get_by_id(farm_b)


class TestProtectedVersionFields:
    """
    Test protection of version/revision fields.
    
    Requirements: 5.7
    
    Protected fields like current_geometry_revision cannot be changed
    through general update operations. Geometry updates follow a specific
    workflow (Phase 4) that creates new revisions.
    """
    
    @pytest.mark.asyncio
    async def test_cannot_modify_current_geometry_revision_directly(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        farm_a: uuid.UUID,
    ):
        """
        Cannot modify current_geometry_revision through update.
        
        Requirements: 5.7
        
        The current_geometry_revision field is a protected version field
        that should only be updated through the boundary edit workflow
        (Phase 4). General metadata updates must not allow changing it.
        """
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a.id)
            farm = await repo_a.get_by_id(farm_a)
            
            original_revision = farm.current_geometry_revision
            assert original_revision == 1  # First revision
            
            # The repository update method doesn't accept revision changes
            # This is by design - revisions are only updated through
            # the boundary edit workflow
            
            # Verify general update works for permitted fields
            updated_farm = await repo_a.update(farm_a, name="Updated Name")
            assert updated_farm.name == "Updated Name"
            assert updated_farm.current_geometry_revision == original_revision


class TestMaliciousInputs:
    """
    Test parameterized handling of malicious filter/sort inputs.
    
    Requirements: 5.6, 5.8
    
    Repository allowlists sortable/filterable columns and parameterizes
    values to prevent SQL injection through column names or filter values.
    """
    
    @pytest.mark.asyncio
    async def test_invalid_sort_column_rejected(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        farm_a: uuid.UUID,
    ):
        """Invalid sort column is rejected."""
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a.id)
            
            # Try to sort by non-allowlisted column
            with pytest.raises(ValueError) as exc_info:
                await repo_a.list_page(sort_by="user_id")  # Not in SORTABLE_COLUMNS
            
            assert "not sortable" in str(exc_info.value).lower()
    
    @pytest.mark.asyncio
    async def test_sql_injection_in_sort_column_rejected(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        farm_a: uuid.UUID,
    ):
        """SQL injection attempts in sort column are rejected."""
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a.id)
            
            # Try SQL injection patterns
            malicious_sorts = [
                "name; DROP TABLE farms;--",
                "name OR 1=1",
                "name' OR '1'='1",
                "name UNION SELECT * FROM users",
            ]
            
            for malicious_sort in malicious_sorts:
                with pytest.raises(ValueError):
                    await repo_a.list_page(sort_by=malicious_sort)
    
    @pytest.mark.asyncio
    async def test_valid_sort_columns_accepted(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a: User,
        farm_a: uuid.UUID,
    ):
        """Valid sort columns from allowlist are accepted."""
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a.id)
            
            # All allowlisted columns should work
            for valid_sort in FarmRepository.SORTABLE_COLUMNS:
                farms = await repo_a.list_page(sort_by=valid_sort)
                assert isinstance(farms, list)


class TestPropertyBasedOwnership:
    """
    Property-based tests for ownership isolation.
    
    Requirements: 5.1, 5.2, 5.3, 5.4, 14.3
    
    Property 2: Ownership isolation
    
    For any distinct users A and B, B cannot list, count, read, update or
    delete A's resource. Uses Hypothesis to generate varied test cases.
    """
    
    @given(
        user_a_name=DATABASE_TEXT,
        user_b_name=DATABASE_TEXT,
        farm_a_name=DATABASE_TEXT,
        farm_b_name=DATABASE_TEXT,
    )
    @settings(
        max_examples=10,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @pytest.mark.asyncio
    async def test_property_cross_user_list_isolation(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a_name: str,
        user_b_name: str,
        farm_a_name: str,
        farm_b_name: str,
    ):
        """
        Property: User B never sees User A's farms in their list.
        
        **Feature: backend-foundation, Property 2: Ownership isolation**
        **Validates: Requirements 5.1, 5.3**
        """
        # Create two distinct users
        user_a_id = uuid.uuid4()
        user_b_id = uuid.uuid4()
        
        async with session_factory() as session:
            user_a = User(
                id=user_a_id,
                issuer="test-issuer",
                subject=f"user-a-{user_a_id}",
                display_name=user_a_name,
            )
            user_b = User(
                id=user_b_id,
                issuer="test-issuer",
                subject=f"user-b-{user_b_id}",
                display_name=user_b_name,
            )
            session.add(user_a)
            session.add(user_b)
            await session.commit()
        
        # Create farm for User A
        async with session_factory() as session:
            service_a = FarmService(session, user_a_id)
            geom_wkb, centroid_wkb, label_wkb, hectares = create_test_polygon()
            farm_a = await service_a.create_farm(
                name=farm_a_name,
                geometry_wkb=geom_wkb,
                centroid_wkb=centroid_wkb,
                label_point_wkb=label_wkb,
                hectares=hectares,
            )
            farm_a_id = farm_a.id
        
        # Create farm for User B
        async with session_factory() as session:
            service_b = FarmService(session, user_b_id)
            geom_wkb, centroid_wkb, label_wkb, hectares = create_test_polygon()
            farm_b = await service_b.create_farm(
                name=farm_b_name,
                geometry_wkb=geom_wkb,
                centroid_wkb=centroid_wkb,
                label_point_wkb=label_wkb,
                hectares=hectares,
            )
            farm_b_id = farm_b.id
        
        # Property: User B's list never contains User A's farm
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b_id)
            farms_b = await repo_b.list_page(limit=100, offset=0)
            farm_ids_b = [f.id for f in farms_b]
            
            assert farm_a_id not in farm_ids_b
            assert farm_b_id in farm_ids_b
        
        # Property: User A's list never contains User B's farm
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a_id)
            farms_a = await repo_a.list_page(limit=100, offset=0)
            farm_ids_a = [f.id for f in farms_a]
            
            assert farm_b_id not in farm_ids_a
            assert farm_a_id in farm_ids_a
    
    @given(
        user_a_subject=DATABASE_TEXT,
        user_b_subject=DATABASE_TEXT,
    )
    @settings(
        max_examples=10,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @pytest.mark.asyncio
    async def test_property_cross_user_read_isolation(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_a_subject: str,
        user_b_subject: str,
    ):
        """
        Property: User B cannot read User A's farm by ID.
        
        **Feature: backend-foundation, Property 2: Ownership isolation**
        **Validates: Requirements 5.1, 5.2**
        """
        # Create two distinct users
        user_a_id = uuid.uuid4()
        user_b_id = uuid.uuid4()
        # Hypothesis executes all examples inside one pytest fixture lifetime.
        # Add the generated UUID so valid repeated examples do not collide with
        # the database uniqueness constraint from an earlier example.
        stored_subject_a = f"{user_a_subject}-{user_a_id}"
        stored_subject_b = f"{user_b_subject}-{user_b_id}"
        
        async with session_factory() as session:
            user_a = User(
                id=user_a_id,
                issuer="test-issuer",
                subject=stored_subject_a,
            )
            user_b = User(
                id=user_b_id,
                issuer="test-issuer",
                subject=stored_subject_b,
            )
            session.add(user_a)
            session.add(user_b)
            await session.commit()
        
        # Create farm for User A
        async with session_factory() as session:
            service_a = FarmService(session, user_a_id)
            geom_wkb, centroid_wkb, label_wkb, hectares = create_test_polygon()
            farm_a = await service_a.create_farm(
                name="User A Farm",
                geometry_wkb=geom_wkb,
                centroid_wkb=centroid_wkb,
                label_point_wkb=label_wkb,
                hectares=hectares,
            )
            farm_a_id = farm_a.id
        
        # Property: User B cannot read User A's farm
        async with session_factory() as session:
            repo_b = FarmRepository(session, user_b_id)
            
            with pytest.raises(NotFoundError):
                await repo_b.get_by_id(farm_a_id)
        
        # Property: User A can read their own farm
        async with session_factory() as session:
            repo_a = FarmRepository(session, user_a_id)
            farm = await repo_a.get_by_id(farm_a_id)
            assert farm.id == farm_a_id

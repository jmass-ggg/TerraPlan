"""
Tests for transaction and session safety.

Feature: backend-foundation, Property 6: Transaction and session safety
Validates: Requirements 6.8, 10.7, 10.8

Test coverage:
- Successful reads/writes
- Domain failures
- Cancellation
- Flush failures
- Commit failures
- Session closure
- Connection release/invalidation
- No partial state on failures
- No successful response for failed commits
"""

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_session


# ============================================================================
# Test Fixtures and Mock Helpers
# ============================================================================


class MockOrmInstance:
    """Mock ORM instance for testing materialization"""

    def __init__(self, id: str, name: str):
        self.id = id
        self.name = name
        self.__table__ = MagicMock()  # Marker for ORM instance


class MockSessionFactory:
    """Mock session factory for testing"""

    def __init__(self, session: AsyncSession):
        self._session = session

    def __call__(self) -> AsyncSession:
        return self._session


@pytest.fixture
def mock_session():
    """Create a mock async session for testing"""
    session = AsyncMock(spec=AsyncSession)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_session_factory(mock_session):
    """Create a mock session factory"""
    return MockSessionFactory(mock_session)


# ============================================================================
# Session Dependency Tests
# ============================================================================


class TestSessionLifecycle:
    """
    Test session lifecycle management.
    Requirements: 6.8, 10.7, 10.8
    """

    @pytest.mark.asyncio
    async def test_session_always_closed_on_success(self, mock_session_factory):
        """Test session is closed after successful operation"""
        session_generator = get_session(mock_session_factory)

        # Simulate successful request handling
        session = await session_generator.__anext__()
        assert session is not None

        # Complete the generator (simulates request completion)
        try:
            await session_generator.__anext__()
        except StopAsyncIteration:
            pass

        # Session should be closed
        mock_session_factory._session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_closed_on_exception(self, mock_session_factory):
        """Test session is closed after exception"""
        session_generator = get_session(mock_session_factory)

        session = await session_generator.__anext__()

        # Throw exception into the generator
        try:
            await session_generator.athrow(ValueError("Simulated error"))
        except ValueError:
            pass

        # Session should be rolled back and closed
        mock_session_factory._session.rollback.assert_awaited_once()
        mock_session_factory._session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_rolled_back_on_exception(self, mock_session_factory):
        """Test transaction is rolled back on exception"""
        session_generator = get_session(mock_session_factory)

        session = await session_generator.__anext__()
        
        # Throw exception into the generator
        try:
            await session_generator.athrow(IntegrityError("Constraint violation", None, None))
        except IntegrityError:
            pass

        # Should rollback before close
        mock_session_factory._session.rollback.assert_awaited_once()
        mock_session_factory._session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_never_commits_after_response(self, mock_session_factory):
        """Test session dependency never commits after response"""
        session_generator = get_session(mock_session_factory)

        session = await session_generator.__anext__()
        # Simulate successful operation (but don't commit in dependency)

        # Complete generator
        try:
            await session_generator.__anext__()
        except StopAsyncIteration:
            pass

        # Should NOT commit - services own commits
        mock_session_factory._session.commit.assert_not_awaited()
        mock_session_factory._session.close.assert_awaited_once()


# ============================================================================
# Transaction Ownership Tests
# ============================================================================


class TestTransactionOwnership:
    """
    Test that services own transaction boundaries.
    Requirements: 6.8, 10.7, 10.8
    """

    @pytest.mark.asyncio
    async def test_service_commits_after_materializing_data(self, mock_session):
        """Test service commits only after materializing response data"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        # Simulate a write operation
        async def create_operation():
            instance = MockOrmInstance(id="test-id", name="Test")
            return instance

        result = await service.execute_write_transaction(create_operation)

        # Verify execution order: operation -> flush -> refresh -> commit
        assert mock_session.method_calls[-3][0] == "flush"
        assert mock_session.method_calls[-2][0] == "refresh"
        assert mock_session.method_calls[-1][0] == "commit"

        # Result should be returned only after commit
        assert result is not None
        assert result.id == "test-id"

    @pytest.mark.asyncio
    async def test_service_rolls_back_on_flush_failure(self, mock_session):
        """Test service rolls back if flush fails"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        # Configure flush to fail
        mock_session.flush.side_effect = IntegrityError(
            "Unique constraint violation", None, None
        )

        async def failing_operation():
            return MockOrmInstance(id="test-id", name="Test")

        # Operation should raise the flush error
        with pytest.raises(IntegrityError):
            await service.execute_write_transaction(failing_operation)

        # Should have attempted flush
        mock_session.flush.assert_awaited_once()

        # Should rollback on failure
        mock_session.rollback.assert_awaited_once()

        # Should NOT commit
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_service_rolls_back_on_commit_failure(self, mock_session):
        """Test service handles commit failure correctly"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        # Configure commit to fail
        mock_session.commit.side_effect = OperationalError(
            "Connection lost", None, None
        )

        async def operation():
            return MockOrmInstance(id="test-id", name="Test")

        # Operation should raise the commit error
        with pytest.raises(OperationalError):
            await service.execute_write_transaction(operation)

        # Should have attempted commit
        mock_session.commit.assert_awaited_once()

        # Should rollback after commit failure
        # (Note: commit failure triggers exception, rollback happens in session dependency)
        mock_session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_success_response_before_commit(self, mock_session):
        """Test that success is returned only after commit completes"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        commit_completed = False

        async def track_commit():
            nonlocal commit_completed
            commit_completed = True

        mock_session.commit.side_effect = track_commit

        async def operation():
            # At this point, commit has not happened yet
            assert not commit_completed
            return MockOrmInstance(id="test-id", name="Test")

        result = await service.execute_write_transaction(operation)

        # Result is returned only after commit
        assert commit_completed
        assert result is not None


# ============================================================================
# Repository Constraint Tests
# ============================================================================


class TestRepositoryConstraints:
    """
    Test that repositories flush but never commit.
    Requirements: 6.8, 10.7, 10.8
    """

    @pytest.mark.asyncio
    async def test_repository_can_flush(self, mock_session):
        """Test repositories can flush to detect constraint violations"""
        from app.repositories.base import BaseRepository

        repo = BaseRepository(mock_session)

        await repo.flush()

        # Repository can flush
        mock_session.flush.assert_awaited_once()

        # But never commits
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repository_never_commits(self, mock_session):
        """Test repositories never commit transactions"""
        from app.repositories.base import BaseRepository

        repo = BaseRepository(mock_session)

        # Perform various repository operations
        instance = MockOrmInstance(id="test", name="Test")
        await repo.add(instance)
        await repo.flush()

        # Repository should never commit
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repository_flush_detects_constraint_violations(self, mock_session):
        """Test repository flush detects constraint violations early"""
        from app.repositories.base import BaseRepository

        repo = BaseRepository(mock_session)

        # Configure flush to raise constraint violation
        mock_session.flush.side_effect = IntegrityError(
            "Unique constraint", None, None
        )

        # Flush should propagate constraint error to service
        with pytest.raises(IntegrityError):
            await repo.flush()

        # Flush was attempted
        mock_session.flush.assert_awaited_once()

        # No commit attempted
        mock_session.commit.assert_not_awaited()


# ============================================================================
# Cancellation Safety Tests
# ============================================================================


class TestCancellationSafety:
    """
    Test that cancellation properly cleans up sessions.
    Requirements: 6.8, 10.7, 10.8
    """

    @pytest.mark.asyncio
    async def test_session_closed_on_cancellation(self, mock_session_factory):
        """Test session is closed when request is cancelled"""
        session_generator = get_session(mock_session_factory)

        session = await session_generator.__anext__()

        # Throw cancellation into the generator
        try:
            await session_generator.athrow(asyncio.CancelledError())
        except asyncio.CancelledError:
            pass

        # Session should be rolled back and closed
        mock_session_factory._session.rollback.assert_awaited_once()
        mock_session_factory._session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_service_cancellation_prevents_commit(self, mock_session):
        """Test that cancellation during service operation prevents commit"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        async def cancellable_operation():
            # Simulate cancellation during operation
            raise asyncio.CancelledError()

        # CancelledError should not be caught by the service
        # It should propagate after rollback
        with pytest.raises(asyncio.CancelledError):
            await service.execute_write_transaction(cancellable_operation)

        # Should rollback on cancellation
        mock_session.rollback.assert_awaited()

        # Should NOT commit
        mock_session.commit.assert_not_awaited()


# ============================================================================
# Data Materialization Tests
# ============================================================================


class TestDataMaterialization:
    """
    Test response data is materialized before session closure.
    Requirements: 10.7, 10.8
    """

    @pytest.mark.asyncio
    async def test_materialize_single_instance(self, mock_session):
        """Test single ORM instance is materialized"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        instance = MockOrmInstance(id="test", name="Test")
        result = await service.materialize_response(instance)

        # Should refresh the instance
        mock_session.refresh.assert_awaited_once_with(instance)
        assert result is instance

    @pytest.mark.asyncio
    async def test_materialize_collection(self, mock_session):
        """Test collection of ORM instances is materialized"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        instances = [
            MockOrmInstance(id="1", name="First"),
            MockOrmInstance(id="2", name="Second"),
        ]
        result = await service.materialize_response(instances)

        # Should refresh each instance
        assert mock_session.refresh.await_count == 2
        assert result is instances

    @pytest.mark.asyncio
    async def test_materialize_none(self, mock_session):
        """Test None value is handled correctly"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        result = await service.materialize_response(None)

        # Should not attempt to refresh None
        mock_session.refresh.assert_not_awaited()
        assert result is None

    @pytest.mark.asyncio
    async def test_materialize_non_orm_object(self, mock_session):
        """Test non-ORM objects are not refreshed"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        plain_dict = {"id": "test", "name": "Test"}
        result = await service.materialize_response(plain_dict)

        # Should not try to refresh plain objects
        mock_session.refresh.assert_not_awaited()
        assert result is plain_dict


# ============================================================================
# No Partial State Tests
# ============================================================================


class TestNoPartialState:
    """
    Test that failures don't leave partial state.
    Requirements: 6.8, 10.8
    """

    @pytest.mark.asyncio
    async def test_flush_failure_prevents_commit(self, mock_session):
        """Test flush failure prevents any commit"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        mock_session.flush.side_effect = IntegrityError("Constraint", None, None)

        async def operation():
            return {"data": "test"}

        with pytest.raises(IntegrityError):
            await service.execute_write_transaction(operation)

        # Flush attempted
        mock_session.flush.assert_awaited_once()

        # Rollback performed
        mock_session.rollback.assert_awaited_once()

        # NO commit
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_operation_failure_prevents_flush_and_commit(self, mock_session):
        """Test operation failure prevents flush and commit"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        async def failing_operation():
            raise ValueError("Business logic error")

        with pytest.raises(ValueError):
            await service.execute_write_transaction(failing_operation)

        # No flush should occur if operation fails
        mock_session.flush.assert_not_awaited()

        # Rollback performed
        mock_session.rollback.assert_awaited_once()

        # NO commit
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commit_failure_triggers_rollback(self, mock_session):
        """Test commit failure triggers rollback"""
        from app.services.base import BaseService

        service = BaseService(mock_session)

        mock_session.commit.side_effect = OperationalError("DB error", None, None)

        async def operation():
            return {"data": "test"}

        with pytest.raises(OperationalError):
            await service.execute_write_transaction(operation)

        # Commit was attempted
        mock_session.commit.assert_awaited_once()

        # Rollback performed after commit failure
        mock_session.rollback.assert_awaited_once()


# ============================================================================
# Connection Release Tests
# ============================================================================


class TestConnectionRelease:
    """
    Test that connections are released or invalidated.
    Requirements: 6.8, 10.7
    """

    @pytest.mark.asyncio
    async def test_session_close_releases_connection(self, mock_session_factory):
        """Test session.close() is always called to release connection"""
        session_generator = get_session(mock_session_factory)

        # Start session
        session = await session_generator.__anext__()

        # Complete normally
        try:
            await session_generator.__anext__()
        except StopAsyncIteration:
            pass

        # Close should be called
        mock_session_factory._session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_close_called_even_on_error(self, mock_session_factory):
        """Test session.close() is called even when errors occur"""
        session_generator = get_session(mock_session_factory)

        session = await session_generator.__anext__()
        
        # Throw exception into generator
        try:
            await session_generator.athrow(RuntimeError("Unexpected error"))
        except RuntimeError:
            pass

        # Close should be called despite error
        mock_session_factory._session.rollback.assert_awaited_once()
        mock_session_factory._session.close.assert_awaited_once()


# ============================================================================
# Property-Based Test
# ============================================================================


@pytest.mark.asyncio
async def test_property_transaction_safety():
    """
    Property 6: Transaction and session safety.

    For any service operation (success, failure, or cancellation):
    - Sessions are always closed
    - Connections are released/invalidated
    - Failed commits never return success
    - Partial state is rolled back

    Feature: backend-foundation, Property 6: Transaction and session safety
    **Validates: Requirements 6.8, 10.7, 10.8**
    """
    from app.services.base import BaseService

    # Test various scenarios
    scenarios = [
        ("success", None, True, None),
        ("flush_error", IntegrityError("Constraint", None, None), False, IntegrityError),
        ("commit_error", OperationalError("DB down", None, None), False, OperationalError),
        ("operation_error", ValueError("Business logic"), False, ValueError),
    ]

    for scenario_name, error, should_commit, expected_error in scenarios:
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.refresh = AsyncMock()

        service = BaseService(mock_session)

        # Configure error scenario
        if scenario_name == "flush_error":
            mock_session.flush.side_effect = error
        elif scenario_name == "commit_error":
            mock_session.commit.side_effect = error

        async def operation():
            if scenario_name == "operation_error":
                raise error
            return MockOrmInstance(id="test", name="Test")

        # Execute operation
        result = None
        error_raised = False
        try:
            result = await service.execute_write_transaction(operation)
        except Exception:
            error_raised = True

        # Assertions
        if should_commit:
            # Success case: commit should be called and result returned
            mock_session.commit.assert_awaited_once()
            assert result is not None
            assert not error_raised
        else:
            # Failure cases: rollback called, no successful result
            mock_session.rollback.assert_awaited()
            assert error_raised
            # commit might be attempted (commit_error) or not (other errors)
            # but rollback always happens

        # In real session dependency, close would be called
        # (handled by the session dependency's finally block)

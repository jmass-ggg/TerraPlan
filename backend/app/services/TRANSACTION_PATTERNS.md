# Transaction Ownership Patterns

## Requirements
- **6.8**: Return write success only after commit; roll back failed commits
- **10.7**: One session per request; never share sessions across concurrent tasks
- **10.8**: Services own transaction boundaries; repositories flush but never commit

## Core Principles

### 1. Session Lifecycle (Dependency)
The `get_session` dependency manages the session lifecycle:

```python
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()  # Roll back on exception
        raise
    finally:
        await session.close()  # Always release connection
```

**Key behaviors:**
- Creates a new session per request/task
- NEVER commits after response (services own commits)
- Rolls back on exception/cancellation
- Always closes session to release/invalidate connection

### 2. Service Transaction Ownership

Services explicitly control transaction boundaries:

```python
class MyService(BaseService):
    async def create_resource(self, data: dict) -> Resource:
        """
        Create a resource with proper transaction ownership.
        """
        # Use the transaction helper
        return await self.execute_write_transaction(
            lambda: self._create_operation(data)
        )
    
    async def _create_operation(self, data: dict) -> Resource:
        # Perform operation (implicit transaction begin)
        resource = Resource(**data)
        self.session.add(resource)
        
        # Repository may flush to detect violations
        await self.session.flush()
        
        # Return result
        # execute_write_transaction will:
        # 1. Flush again to ensure constraints
        # 2. Materialize the resource (refresh)
        # 3. Commit the transaction
        # 4. Return success only after commit
        return resource
```

**Transaction flow:**
1. Execute operation (implicit transaction begin)
2. Repository may flush to detect constraint violations
3. Service flushes again before commit
4. **Materialize response data** (while session is active)
5. Commit transaction
6. Return success **only after commit succeeds**

### 3. Repository Constraints

Repositories participate in transactions but do NOT commit:

```python
class MyRepository(BaseRepository):
    async def create(self, data: dict) -> Resource:
        resource = Resource(**data)
        self.session.add(resource)
        
        # Repositories MAY flush to detect violations early
        await self.flush()
        
        # MUST NOT commit - service owns the commit
        # await self.session.commit()  # ❌ NEVER DO THIS
        
        return resource
```

**Repository rules:**
- Receive session from service
- May flush to detect constraint violations
- MUST NOT commit transactions
- Return results; service commits after materializing

### 4. Materialization Before Commit

Response data MUST be materialized before session closure:

```python
async def update_resource(self, id: UUID, changes: dict) -> Resource:
    # Update operation
    resource = await self.repository.update(id, changes)
    
    # Flush to detect violations
    await self.session.flush()
    
    # Materialize lazy-loaded relationships BEFORE commit
    # This prevents lazy-load errors after session closure
    await self.session.refresh(resource)
    
    # Now safe to commit
    await self.session.commit()
    
    # Return success only after commit
    return resource
```

### 5. Error Handling and Rollback

Failures trigger rollback and connection release:

```python
async def risky_operation(self, data: dict) -> Resource:
    try:
        # Operation that might fail
        resource = await self._create_with_validation(data)
        await self.session.flush()
        await self.session.refresh(resource)
        await self.session.commit()
        return resource
    except IntegrityError:
        # Service explicitly rolls back
        await self.session.rollback()
        raise DomainException("Constraint violation")
    # Session dependency also rolls back in finally
    # Connection is released/invalidated by dependency
```

## Complete Example

```python
from app.services.base import BaseService
from app.repositories.base import BaseRepository
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

class FarmRepository(BaseRepository):
    async def create(self, name: str, user_id: UUID) -> Farm:
        farm = Farm(name=name, user_id=user_id)
        self.session.add(farm)
        # May flush, but does NOT commit
        await self.flush()
        return farm

class FarmService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = FarmRepository(session)
    
    async def create_farm(self, name: str, user_id: UUID) -> Farm:
        """
        Create a farm with proper transaction ownership.
        
        Returns farm only after successful commit.
        """
        return await self.execute_write_transaction(
            lambda: self.repository.create(name, user_id)
        )
        # Transaction flow:
        # 1. repository.create() adds farm and flushes
        # 2. execute_write_transaction() flushes again
        # 3. Materializes farm (refresh)
        # 4. Commits transaction
        # 5. Returns farm (only after commit)
        
        # On failure:
        # - Transaction is rolled back
        # - Session dependency releases connection
        # - Exception propagates to route handler
```

## Anti-Patterns to Avoid

### ❌ Committing in Repository
```python
class BadRepository:
    async def create(self, data):
        self.session.add(Resource(**data))
        await self.session.commit()  # ❌ WRONG: Repository commits
        return resource
```

### ❌ Committing in Dependency
```python
async def bad_session_dependency():
    session = session_factory()
    try:
        yield session
        await session.commit()  # ❌ WRONG: Dependency commits
    finally:
        await session.close()
```

### ❌ Returning Before Commit
```python
async def bad_service_method(self):
    resource = await self.repository.create(data)
    # ❌ WRONG: Returned before commit
    result = resource.to_dict()
    await self.session.commit()  # Commit happens after return prep
    return result
```

### ❌ Not Materializing Before Commit
```python
async def bad_service_method(self):
    resource = await self.repository.create(data)
    await self.session.commit()
    # ❌ WRONG: Accessing relationships after commit/session close
    return resource.relationships  # May cause lazy-load error
```

## Testing Patterns

Transaction safety should be verified with:

1. **Successful write**: Returns only after commit
2. **Flush failure**: Rolls back, returns error
3. **Commit failure**: Rolls back, returns error
4. **Cancellation**: Rolls back, releases connection
5. **Session closure**: No partial state persisted

Example test:
```python
async def test_write_returns_after_commit():
    # Arrange
    service = FarmService(session)
    
    # Act
    farm = await service.create_farm("Test", user_id)
    
    # Assert: Farm is committed and persisted
    session.expire_all()  # Clear session cache
    persisted = await session.get(Farm, farm.id)
    assert persisted is not None
    assert persisted.name == "Test"
```

## Summary

**Session Dependency:**
- Creates session
- NEVER commits
- Rolls back on exception
- Always closes session

**Services:**
- Own transaction boundaries
- Materialize data before commit
- Commit explicitly
- Return success only after commit

**Repositories:**
- May flush
- MUST NOT commit
- Participate in service transaction

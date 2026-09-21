"""
Base repository patterns.

Requirements: 5.1, 5.6, 5.8, 6.8, 10.7, 10.8

Repositories may flush to detect constraint violations but must not commit.
Transaction ownership belongs to services.
"""

import uuid
from typing import TypeVar, Generic

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    DomainException,
    ForbiddenError,
    NotFoundError,
)

T = TypeVar("T")


class BaseRepository:
    """
    Base repository demonstrating transaction participation.
    
    Requirements: 6.8, 10.7, 10.8
    
    Repository rules:
    1. Repositories receive a session from services
    2. Repositories may flush to detect constraint violations
    3. Repositories MUST NOT commit transactions
    4. Services own transaction boundaries
    """
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def flush(self) -> None:
        """
        Flush pending changes to detect constraint violations.
        
        This allows repositories to validate database constraints
        without committing the transaction. The service will commit
        after materializing response data.
        """
        await self.session.flush()
    
    async def add(self, instance) -> None:
        """
        Add an instance to the session.
        
        The instance will be flushed/committed by the service.
        """
        self.session.add(instance)
    
    async def delete(self, instance) -> None:
        """
        Mark an instance for deletion.
        
        The deletion will be flushed/committed by the service.
        """
        await self.session.delete(instance)
    
    async def refresh(self, instance) -> None:
        """
        Refresh an instance to materialize lazy-loaded attributes.
        
        Should be called before transaction commit to ensure
        response data is fully materialized.
        """
        await self.session.refresh(instance)


class OwnedRepository(Generic[T], BaseRepository):
    """
    Base repository for user-owned resources.
    
    Requirements: 5.1, 5.6, 5.8
    
    This repository is bound to a verified internal user UUID and ensures
    all operations are scoped to that owner. It prevents cross-user access
    by including ownership in all queries, updates, and deletes.
    
    Key principles:
    1. All operations filter by owner_id
    2. Absent and invisible resources return the same NotFoundError
    3. No global lookups that could reveal another user's records
    4. Parameterized queries with allowlisted sort/filter columns
    5. Reject attempts to change owner, IDs, or protected fields
    
    Shared provider/catalog repositories remain separate from this pattern.
    Anonymous administrative writes are denied.
    """
    
    def __init__(self, session: AsyncSession, owner_id: uuid.UUID):
        """
        Initialize repository bound to an owner.
        
        Args:
            session: Active database session
            owner_id: Internal UUID of the verified principal
        """
        super().__init__(session)
        self.owner_id = owner_id
    
    def _ensure_ownership_filter(self, query, model_class: type[T]):
        """
        Apply ownership filter to a query.
        
        Requirements: 5.1
        
        This ensures all read operations are scoped to the owner,
        preventing cross-user access.
        
        Args:
            query: SQLAlchemy query to filter
            model_class: Model class with user_id column
            
        Returns:
            Filtered query
        """
        return query.where(model_class.user_id == self.owner_id)
    
    @staticmethod
    def _validate_sortable_column(column_name: str, allowed_columns: set[str]) -> str:
        """
        Validate and allowlist sortable columns.
        
        Requirements: 5.6, 5.8
        
        Prevents SQL injection through column names by only allowing
        explicitly permitted columns for sorting.
        
        Args:
            column_name: Requested sort column
            allowed_columns: Set of permitted column names
            
        Returns:
            Validated column name
            
        Raises:
            ValueError: If column is not in allowlist
        """
        if column_name not in allowed_columns:
            raise ValueError(f"Column '{column_name}' is not sortable")
        return column_name


# Imported and re-exported here for compatibility with the repository API.

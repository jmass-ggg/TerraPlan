"""
Base service patterns for transaction ownership.

Requirements: 6.8, 10.7, 10.8

Services own transaction boundaries. Repositories may flush but never commit.
Response data must be materialized and validated before session closure.
Write success is returned only after successful commit.
"""

from typing import TypeVar, Generic, Callable, Awaitable, Any
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class BaseService:
    """
    Base class demonstrating transaction ownership pattern.
    
    Requirements: 6.8, 10.7, 10.8
    
    Transaction rules:
    1. Services begin and commit/rollback transactions
    2. Repositories flush within transactions but never commit
    3. Materialize and validate response data before session closure
    4. Return success only after successful commit
    5. Roll back on any failure or cancellation
    
    Usage pattern:
        async with service.transaction():
            result = await service.perform_operation()
            # result is materialized and validated
            # commit happens automatically on context exit
        return result  # Success only after commit
    """
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def materialize_response(self, instance: Any) -> Any:
        """
        Materialize lazy-loaded attributes before session closure.
        
        This ensures response data is fully loaded while session is active,
        preventing lazy-load errors after session closure.
        
        Requirements: 10.7, 10.8
        """
        if instance is None:
            return instance
            
        # Refresh ORM instances to materialize relationships
        if hasattr(instance, "__dict__") and hasattr(instance, "__table__"):
            await self.session.refresh(instance)
        
        # For collections, refresh each item
        if isinstance(instance, list):
            for item in instance:
                if hasattr(item, "__dict__") and hasattr(item, "__table__"):
                    await self.session.refresh(item)
        
        return instance
    
    async def execute_write_transaction(
        self,
        operation: Callable[[], Awaitable[T]],
        materialize: bool = True
    ) -> T:
        """
        Execute a write operation within a transaction.
        
        Requirements: 6.8, 10.7, 10.8
        
        Transaction pattern:
        1. Execute operation (implicit transaction begin)
        2. Flush to detect constraint violations early
        3. Materialize response data (while session is active)
        4. Commit transaction
        5. Return validated response (only after commit succeeds)
        
        On exception/cancellation:
        - Transaction is rolled back by session dependency
        - Connection is released/invalidated
        - Exception propagates to caller
        
        Args:
            operation: Async function performing the write operation
            materialize: Whether to materialize ORM objects (default True)
            
        Returns:
            Operation result, only after successful commit
        """
        try:
            # Execute the operation
            result = await operation()
            
            # Flush to detect database constraint violations early
            await self.session.flush()
            
            # Materialize response data before commit
            # This ensures no lazy loading after session closure
            if materialize and result is not None:
                result = await self.materialize_response(result)
            
            # Commit the transaction
            # Return success only after commit completes
            await self.session.commit()
            
            return result
            
        except BaseException:
            # Roll back on any failure, including cancellation
            # BaseException catches CancelledError which inherits from BaseException, not Exception
            # Note: Session dependency also rolls back in its except/finally
            await self.session.rollback()
            raise


class TransactionMixin:
    """
    Mixin providing transaction management utilities.
    
    Requirements: 6.8, 10.7, 10.8
    
    Can be used by service classes that need explicit transaction control.
    """
    
    session: AsyncSession  # Must be provided by the using class
    
    async def commit_transaction(self) -> None:
        """
        Commit the current transaction.
        
        Requirements: 10.8
        
        Should only be called after:
        1. Flushing changes to detect constraint violations
        2. Materializing and validating response data
        
        Write success should be returned only after this completes.
        """
        await self.session.commit()
    
    async def rollback_transaction(self) -> None:
        """
        Roll back the current transaction.
        
        Requirements: 6.8, 10.8
        
        Called on failures or cancellation.
        Session dependency also rolls back in finally block.
        """
        await self.session.rollback()
    
    async def flush_changes(self) -> None:
        """
        Flush changes to detect constraint violations without committing.
        
        Requirements: 10.7
        
        Repositories may call this, but must not commit independently.
        Services commit after materializing response data.
        """
        await self.session.flush()
    
    async def materialize_for_response(self, instance: Any) -> Any:
        """
        Materialize ORM instance attributes before session closure.
        
        Requirements: 10.7, 10.8
        
        Call this before committing to ensure response data is fully loaded
        and won't trigger lazy loads after session closure.
        """
        if instance is None:
            return instance
            
        if hasattr(instance, "__dict__") and hasattr(instance, "__table__"):
            await self.session.refresh(instance)
        
        return instance

"""Backward-compatible import for the Phase 4 farm service."""

from .farm_service import (
    FarmCreationResult,
    FarmService,
    create_farm,
    delete_farm,
    update_farm,
)

__all__ = [
    "FarmCreationResult",
    "FarmService",
    "create_farm",
    "update_farm",
    "delete_farm",
]

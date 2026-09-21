"""AI services for FarmTwin"""

from .crop_explanation_service import (
    CropExplanation,
    CropExplanationProvider,
    OpenRouterCropExplanationProvider,
    get_crop_explanation_provider,
)

__all__ = [
    "CropExplanation",
    "CropExplanationProvider",
    "OpenRouterCropExplanationProvider",
    "get_crop_explanation_provider",
]

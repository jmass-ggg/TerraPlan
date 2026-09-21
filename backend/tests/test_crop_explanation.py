"""
Tests for AI crop explanation service.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from app.core.config import Settings
from app.domain.crop_engine import ComponentScores, SimulationResult
from app.domain.crop_register import CropRequirements
from app.services.ai.crop_explanation_service import (
    OpenRouterCropExplanationProvider,
)


@pytest.fixture
def mock_settings():
    """Mock settings with OpenRouter configuration."""
    settings = Mock(spec=Settings)
    settings.openrouter_api_key = Mock()
    settings.openrouter_api_key.get_secret_value.return_value = "test_api_key"
    settings.openrouter_model = "google/gemini-flash-1.5-8b"
    settings.openrouter_timeout_seconds = 10
    settings.redis_url = "redis://localhost:6379"
    return settings


@pytest.fixture
def mock_settings_no_key():
    """Mock settings without API key."""
    settings = Mock(spec=Settings)
    settings.openrouter_api_key = Mock()
    settings.openrouter_api_key.get_secret_value.return_value = ""
    settings.openrouter_model = "google/gemini-flash-1.5-8b"
    settings.openrouter_timeout_seconds = 10
    settings.redis_url = "redis://localhost:6379"
    return settings


@pytest.fixture
def sample_crop_result():
    """Sample crop simulation result."""
    return SimulationResult(
        crop_name="Maize",
        suitability_index=54,
        label="Higher caution",
        components=ComponentScores(
            temperature=92.0,
            water=41.0,
            soil=None,
            heat_safety=88.0,
            drought_flood_safety=63.0,
            environmental_condition=47.0,
        ),
        limiting_factor="water / rainfall",
        reason="Maize scores 54/100 (higher caution). The primary limiting factor is water / rainfall.",
        hard_exclusion=False,
        hard_exclusion_reason=None,
        engine_version="farmtwin-crop-v2",
        snapshot_id="test-snapshot-123",
        data_mode="live",
        input_completeness={
            "temperature_mean_c": "real",
            "rainfall_total_mm": "real",
            "soil_ph": "missing",
            "soil_clay_pct": "missing",
        },
    )


@pytest.fixture
def sample_crop_requirements():
    """Sample crop requirements."""
    return CropRequirements(
        name="Maize",
        data_version="v1",
        category="cereal",
        source_citation="FAO",
        min_temp_c=18.0,
        optimum_temp_c=25.0,
        max_temp_c=32.0,
        min_rainfall_mm=500.0,
        max_rainfall_mm=800.0,
        min_soil_ph=5.5,
        max_soil_ph=7.5,
        soil_texture_preferences=("loam",),
        drought_tolerance=0.6,
        heat_tolerance_ceiling_c=40.0,
        duration_months=4,
    )


@pytest.mark.asyncio
async def test_explanation_never_changes_score(
    mock_settings, sample_crop_result, sample_crop_requirements
):
    """Test that AI explanation never modifies the suitability score."""
    provider = OpenRouterCropExplanationProvider(mock_settings)
    
    # Mock the AI call to return a valid response
    with patch.object(provider, '_generate_via_openrouter') as mock_ai:
        from app.services.ai.crop_explanation_service import CropExplanation, CropExplanationFactor
        
        mock_ai.return_value = CropExplanation(
            headline="Water limits suitability",
            summary="Maize faces water constraints.",
            strengths=(),
            concerns=(CropExplanationFactor(factor="Water", message="Low water availability"),),
            action="Consider irrigation",
            data_note="Soil data unavailable",
            source="ai",
            cached=False,
        )
        
        # Mock Redis cache
        with patch('app.services.ai.crop_explanation_service.aioredis.from_url') as mock_redis:
            mock_redis.return_value.__aenter__.return_value.get = AsyncMock(return_value=None)
            mock_redis.return_value.__aenter__.return_value.setex = AsyncMock()
            
            result = await provider.generate(
                result=sample_crop_result,
                crop_requirements=sample_crop_requirements,
                farm_id="test-farm-123",
            )
    
    # Verify the score was not changed
    assert sample_crop_result.suitability_index == 54
    assert result.source == "ai"


@pytest.mark.asyncio
async def test_missing_api_key_fallback(
    mock_settings_no_key, sample_crop_result, sample_crop_requirements
):
    """Test that missing API key results in deterministic fallback."""
    provider = OpenRouterCropExplanationProvider(mock_settings_no_key)
    
    result = await provider.generate(
        result=sample_crop_result,
        crop_requirements=sample_crop_requirements,
        farm_id="test-farm-123",
    )
    
    assert result.source == "deterministic_fallback"
    assert result.cached is False
    assert "54" in result.summary or "54/100" in result.summary


@pytest.mark.asyncio
async def test_openrouter_timeout_fallback(
    mock_settings, sample_crop_result, sample_crop_requirements
):
    """Test that OpenRouter timeout results in deterministic fallback."""
    provider = OpenRouterCropExplanationProvider(mock_settings)
    
    # Mock Redis cache to return None
    with patch('app.services.ai.crop_explanation_service.aioredis.from_url') as mock_redis:
        mock_redis.return_value.__aenter__.return_value.get = AsyncMock(return_value=None)
        
        # Mock OpenRouter to timeout
        with patch.object(provider, '_generate_via_openrouter') as mock_ai:
            import httpx
            mock_ai.side_effect = httpx.TimeoutException("Request timeout")
            
            result = await provider.generate(
                result=sample_crop_result,
                crop_requirements=sample_crop_requirements,
                farm_id="test-farm-123",
            )
    
    assert result.source == "deterministic_fallback"


@pytest.mark.asyncio
async def test_cache_hit_no_ai_call(
    mock_settings, sample_crop_result, sample_crop_requirements
):
    """Test that cached explanations don't trigger OpenRouter calls."""
    provider = OpenRouterCropExplanationProvider(mock_settings)
    
    cached_data = {
        "headline": "Cached headline",
        "summary": "Cached summary",
        "strengths": [{"factor": "Temperature", "message": "Good"}],
        "concerns": [{"factor": "Water", "message": "Low"}],
        "action": "Cached action",
        "data_note": "Cached note",
    }
    
    # Mock Redis to return cached data
    with patch('app.services.ai.crop_explanation_service.aioredis.from_url') as mock_redis:
        import json
        mock_redis.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=json.dumps(cached_data)
        )
        
        # Mock AI generation - should not be called
        with patch.object(provider, '_generate_via_openrouter') as mock_ai:
            result = await provider.generate(
                result=sample_crop_result,
                crop_requirements=sample_crop_requirements,
                farm_id="test-farm-123",
            )
            
            # Verify AI was not called
            mock_ai.assert_not_called()
    
    assert result.source == "ai"
    assert result.cached is True
    assert result.headline == "Cached headline"


@pytest.mark.asyncio
async def test_different_crops_different_cache_keys(
    mock_settings, sample_crop_result, sample_crop_requirements
):
    """Test that different crops generate different cache keys."""
    provider = OpenRouterCropExplanationProvider(mock_settings)
    
    key1 = provider._generate_cache_key(sample_crop_result, "farm-123")
    
    # Change crop name
    result2 = SimulationResult(
        crop_name="Wheat",  # Different crop
        suitability_index=sample_crop_result.suitability_index,
        label=sample_crop_result.label,
        components=sample_crop_result.components,
        limiting_factor=sample_crop_result.limiting_factor,
        reason=sample_crop_result.reason,
        hard_exclusion=sample_crop_result.hard_exclusion,
        hard_exclusion_reason=sample_crop_result.hard_exclusion_reason,
        engine_version=sample_crop_result.engine_version,
        snapshot_id=sample_crop_result.snapshot_id,
        data_mode=sample_crop_result.data_mode,
        input_completeness=sample_crop_result.input_completeness,
    )
    
    key2 = provider._generate_cache_key(result2, "farm-123")
    
    assert key1 != key2


@pytest.mark.asyncio
async def test_missing_soil_data_not_invented(
    mock_settings, sample_crop_result, sample_crop_requirements
):
    """Test that missing soil data is not invented in the prompt."""
    provider = OpenRouterCropExplanationProvider(mock_settings)
    
    prompt_data = provider._build_prompt_data(sample_crop_result, sample_crop_requirements)
    
    # Verify soil is marked as unavailable
    assert "soil" not in prompt_data["assessment"]["components"] or \
           prompt_data["assessment"]["components"]["soil"]["available"] is False
    
    # Verify missing components list includes soil
    assert "soil" in prompt_data["assessment"]["missing_components"]

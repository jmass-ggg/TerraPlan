"""
Live integration test for OpenRouter API.
This test will make an actual API call to OpenRouter.
"""

import pytest
from unittest.mock import Mock

from app.core.config import Settings
from app.domain.crop_engine import ComponentScores, SimulationResult
from app.domain.crop_register import CropRequirements
from app.services.ai.crop_explanation_service import (
    OpenRouterCropExplanationProvider,
)


@pytest.fixture
def live_settings():
    """Use real settings from .env file."""
    return Settings()


@pytest.fixture
def sample_maize_result():
    """Sample Maize simulation result."""
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
        snapshot_id="test-snapshot-live",
        data_mode="live",
        input_completeness={
            "temperature_mean_c": "real",
            "rainfall_total_mm": "real",
            "soil_ph": "missing",
            "soil_clay_pct": "missing",
        },
    )


@pytest.fixture
def sample_wheat_result():
    """Sample Wheat simulation result with high score."""
    return SimulationResult(
        crop_name="Wheat",
        suitability_index=90,
        label="Good match",
        components=ComponentScores(
            temperature=97.0,
            water=89.0,
            soil=None,
            heat_safety=100.0,
            drought_flood_safety=85.0,
            environmental_condition=45.0,
        ),
        limiting_factor="environmental_condition",
        reason="Wheat is a good match for these conditions. The main constraint is environmental condition.",
        hard_exclusion=False,
        hard_exclusion_reason=None,
        engine_version="farmtwin-crop-v2",
        snapshot_id="test-snapshot-live",
        data_mode="live",
        input_completeness={
            "temperature_mean_c": "real",
            "rainfall_total_mm": "real",
            "soil_ph": "missing",
            "soil_clay_pct": "missing",
        },
    )


@pytest.fixture
def maize_requirements():
    """Maize crop requirements."""
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


@pytest.fixture
def wheat_requirements():
    """Wheat crop requirements."""
    return CropRequirements(
        name="Wheat",
        data_version="v1",
        category="cereal",
        source_citation="FAO",
        min_temp_c=10.0,
        optimum_temp_c=20.0,
        max_temp_c=28.0,
        min_rainfall_mm=400.0,
        max_rainfall_mm=700.0,
        min_soil_ph=6.0,
        max_soil_ph=7.5,
        soil_texture_preferences=("loam",),
        drought_tolerance=0.7,
        heat_tolerance_ceiling_c=35.0,
        duration_months=5,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not Settings().openrouter_api_key.get_secret_value(),
    reason="OPENROUTER_API_KEY not configured"
)
async def test_live_openrouter_maize_explanation(
    live_settings, sample_maize_result, maize_requirements
):
    """
    Live test: Generate AI explanation for Maize using real OpenRouter API.
    This test will be skipped if OPENROUTER_API_KEY is not set.
    """
    print("\n🌽 Testing live OpenRouter API call for Maize...")
    
    provider = OpenRouterCropExplanationProvider(live_settings)
    
    explanation = await provider.generate(
        result=sample_maize_result,
        crop_requirements=maize_requirements,
        farm_id="test-farm-live-123",
    )
    
    print(f"\n✅ Explanation generated successfully!")
    print(f"Source: {explanation.source}")
    print(f"Cached: {explanation.cached}")
    print(f"\nHeadline: {explanation.headline}")
    print(f"\nSummary: {explanation.summary}")
    
    if explanation.strengths:
        print("\n✓ Strengths:")
        for strength in explanation.strengths:
            print(f"  - {strength.factor}: {strength.message}")
    
    if explanation.concerns:
        print("\n⚠ Concerns:")
        for concern in explanation.concerns:
            print(f"  - {concern.factor}: {concern.message}")
    
    if explanation.action:
        print(f"\n💡 Action: {explanation.action}")
    
    if explanation.data_note:
        print(f"\n📝 Data note: {explanation.data_note}")
    
    # Assertions
    assert explanation.source in ["ai", "deterministic_fallback"]
    assert explanation.headline is not None
    assert len(explanation.headline) > 0
    assert explanation.summary is not None
    assert len(explanation.summary) > 0
    
    # If AI succeeded, verify it mentioned the crop
    if explanation.source == "ai":
        summary_lower = explanation.summary.lower()
        assert "maize" in summary_lower or "corn" in summary_lower
        # Should mention water as the limiting factor
        assert "water" in summary_lower or "rainfall" in summary_lower


@pytest.mark.asyncio
@pytest.mark.skipif(
    not Settings().openrouter_api_key.get_secret_value(),
    reason="OPENROUTER_API_KEY not configured"
)
async def test_live_openrouter_wheat_explanation(
    live_settings, sample_wheat_result, wheat_requirements
):
    """
    Live test: Generate AI explanation for Wheat (high score) using real OpenRouter API.
    """
    print("\n🌾 Testing live OpenRouter API call for Wheat...")
    
    provider = OpenRouterCropExplanationProvider(live_settings)
    
    explanation = await provider.generate(
        result=sample_wheat_result,
        crop_requirements=wheat_requirements,
        farm_id="test-farm-live-456",
    )
    
    print(f"\n✅ Explanation generated successfully!")
    print(f"Source: {explanation.source}")
    print(f"Cached: {explanation.cached}")
    print(f"\nHeadline: {explanation.headline}")
    print(f"\nSummary: {explanation.summary}")
    
    if explanation.strengths:
        print("\n✓ Strengths:")
        for strength in explanation.strengths:
            print(f"  - {strength.factor}: {strength.message}")
    
    if explanation.concerns:
        print("\n⚠ Concerns:")
        for concern in explanation.concerns:
            print(f"  - {concern.factor}: {concern.message}")
    
    if explanation.action:
        print(f"\n💡 Action: {explanation.action}")
    
    # Assertions
    assert explanation.source in ["ai", "deterministic_fallback"]
    assert explanation.headline is not None
    assert explanation.summary is not None
    
    # If AI succeeded, verify it's positive for high score
    if explanation.source == "ai":
        summary_lower = explanation.summary.lower()
        assert "wheat" in summary_lower
        # Should indicate it's a good match
        assert any(word in summary_lower for word in ["good", "strong", "suitable", "favorable"])


@pytest.mark.asyncio
@pytest.mark.skipif(
    not Settings().openrouter_api_key.get_secret_value(),
    reason="OPENROUTER_API_KEY not configured"
)
async def test_live_openrouter_caching(
    live_settings, sample_maize_result, maize_requirements
):
    """
    Live test: Verify that repeated calls use cache.
    """
    print("\n🔄 Testing OpenRouter caching...")
    
    provider = OpenRouterCropExplanationProvider(live_settings)
    
    # First call - should hit API
    explanation1 = await provider.generate(
        result=sample_maize_result,
        crop_requirements=maize_requirements,
        farm_id="test-farm-cache-789",
    )
    
    print(f"First call - Source: {explanation1.source}, Cached: {explanation1.cached}")
    
    # Second call - should hit cache
    explanation2 = await provider.generate(
        result=sample_maize_result,
        crop_requirements=maize_requirements,
        farm_id="test-farm-cache-789",
    )
    
    print(f"Second call - Source: {explanation2.source}, Cached: {explanation2.cached}")
    
    # If first call succeeded with AI, second should be cached
    if explanation1.source == "ai":
        assert explanation2.cached is True
        assert explanation2.headline == explanation1.headline
        assert explanation2.summary == explanation1.summary
        print("✅ Cache is working correctly!")


if __name__ == "__main__":
    # Run with: python -m pytest tests/test_openrouter_live.py -v -s
    print("Run these tests with: ./venv/bin/python -m pytest tests/test_openrouter_live.py -v -s")

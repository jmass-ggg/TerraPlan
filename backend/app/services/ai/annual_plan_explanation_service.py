"""
AI-generated explanations for Annual Crop Plan recommendations.

Generates 2-5 sentence explanations for why each crop was recommended for its season,
using real farm data: crop name, month/season, weather, climate, soil, terrain, and risks.

Requirements: Practical agronomic tone, no hallucinated data, smart fallback if AI fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import redis.asyncio as aioredis

from app.core.config import Settings
from app.domain.snapshot_context import SnapshotContext
from app.domain.crop_register import CropRequirements

logger = logging.getLogger(__name__)

# Cache TTL: 7 days
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class AnnualPlanExplanation:
    """Farmer-friendly explanation for why a crop was recommended."""
    text: str  # 2-5 sentences
    source: str  # "ai" | "rule_based_fallback"
    cached: bool


class AnnualPlanExplanationService:
    """Generates explanations for annual crop plan recommendations."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._api_key = settings.openrouter_api_key.get_secret_value()
        self._model = settings.openrouter_model
        self._timeout = settings.openrouter_timeout_seconds
        self._redis_url = settings.redis_url

    async def generate(
        self,
        crop: CropRequirements,
        context: SnapshotContext,
        month: int,
        month_name: str,
        suitability_index: int,
        limiting_factor: str | None,
        previous_crop: str | None,
        rotation_effect: str,
        farm_id: str,
    ) -> AnnualPlanExplanation:
        """
        Generate explanation for why this crop was recommended for this month.
        
        Falls back to rule-based explanation on any failure.
        """
        # Check if API key is configured
        if not self._api_key:
            logger.debug("OpenRouter API key not configured, using rule-based fallback")
            return self._rule_based_fallback(
                crop.name, month_name, suitability_index, limiting_factor, 
                previous_crop, rotation_effect
            )

        # Generate cache key
        cache_key = self._generate_cache_key(
            crop.name, month, suitability_index, context, farm_id
        )

        # Try cache first
        try:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.info(
                    "annual_plan_explanation.cache_hit: crop=%s, month=%s, farm=%s",
                    crop.name, month_name, farm_id,
                )
                return cached
        except Exception as exc:
            logger.warning("annual_plan_explanation.cache_read_failed: %s", exc)

        # Generate via AI
        try:
            explanation = await self._generate_via_openrouter(
                crop, context, month, month_name, suitability_index, 
                limiting_factor, previous_crop, rotation_effect, farm_id
            )
            # Cache the result
            try:
                await self._cache_explanation(cache_key, explanation)
            except Exception as exc:
                logger.warning("annual_plan_explanation.cache_write_failed: %s", exc)
            return explanation
        except Exception as exc:
            logger.warning(
                "annual_plan_explanation.ai_failed: crop=%s, month=%s, farm=%s, error=%s",
                crop.name, month_name, farm_id, str(exc),
            )
            return self._rule_based_fallback(
                crop.name, month_name, suitability_index, limiting_factor,
                previous_crop, rotation_effect
            )

    def _generate_cache_key(
        self,
        crop_name: str,
        month: int,
        suitability_index: int,
        context: SnapshotContext,
        farm_id: str,
    ) -> str:
        """Generate cache key from farm, crop, month, and context."""
        snapshot_part = context.snapshot_id or "no_snapshot"
        data_mode_part = context.data_mode
        
        # Create hash of the key components
        key_material = (
            f"{farm_id}:{crop_name}:{month}:{suitability_index}:"
            f"{snapshot_part}:{data_mode_part}"
        )
        key_hash = hashlib.sha256(key_material.encode()).hexdigest()[:16]
        
        return f"farmtwin:annual_plan_explanation:{key_hash}"

    async def _get_cached(self, cache_key: str) -> AnnualPlanExplanation | None:
        """Retrieve cached explanation from Redis."""
        async with aioredis.from_url(self._redis_url) as client:
            cached_data = await client.get(cache_key)
            if cached_data:
                data = json.loads(cached_data)
                return AnnualPlanExplanation(
                    text=data["text"],
                    source="ai",
                    cached=True,
                )
        return None

    async def _cache_explanation(
        self, cache_key: str, explanation: AnnualPlanExplanation
    ) -> None:
        """Cache explanation in Redis."""
        data = {
            "text": explanation.text,
        }
        async with aioredis.from_url(self._redis_url) as client:
            await client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(data))

    async def _generate_via_openrouter(
        self,
        crop: CropRequirements,
        context: SnapshotContext,
        month: int,
        month_name: str,
        suitability_index: int,
        limiting_factor: str | None,
        previous_crop: str | None,
        rotation_effect: str,
        farm_id: str,
    ) -> AnnualPlanExplanation:
        """Call OpenRouter API to generate explanation."""
        start_time = time.time()

        # Build structured prompt payload
        prompt_data = self._build_prompt_data(
            crop, context, month, month_name, suitability_index,
            limiting_factor, previous_crop, rotation_effect
        )
        
        # Call OpenRouter
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {
                            "role": "system",
                            "content": self._get_system_prompt(),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(prompt_data),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Parse response
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
        explanation_json = json.loads(content)
        
        logger.info(
            "annual_plan_explanation.ai_success: crop=%s, month=%s, farm=%s, "
            "model=%s, latency_ms=%d",
            crop.name, month_name, farm_id, self._model, latency_ms,
        )
        
        # Extract explanation text (2-5 sentences)
        explanation_text = explanation_json.get("explanation", "")[:500]
        
        return AnnualPlanExplanation(
            text=explanation_text,
            source="ai",
            cached=False,
        )

    def _build_prompt_data(
        self,
        crop: CropRequirements,
        context: SnapshotContext,
        month: int,
        month_name: str,
        suitability_index: int,
        limiting_factor: str | None,
        previous_crop: str | None,
        rotation_effect: str,
    ) -> dict[str, Any]:
        """Build structured data for the AI prompt."""
        # Build environmental context
        environmental_data: dict[str, Any] = {}
        
        if context.temperature_mean_c is not None:
            environmental_data["temperature_mean_c"] = round(context.temperature_mean_c, 1)
        
        if context.rainfall_total_mm is not None:
            environmental_data["rainfall_total_mm"] = round(context.rainfall_total_mm, 1)
        
        if context.soil_ph is not None:
            environmental_data["soil_ph"] = round(context.soil_ph, 1)
        
        if context.soil_clay_pct is not None:
            environmental_data["soil_clay_pct"] = round(context.soil_clay_pct, 1)
        
        if context.soil_sand_pct is not None:
            environmental_data["soil_sand_pct"] = round(context.soil_sand_pct, 1)
        
        if context.ndvi_mean is not None:
            environmental_data["ndvi_mean"] = round(context.ndvi_mean, 2)
        
        # Calculate drought/flood risk from available data
        if context.rainfall_total_mm is not None and context.climate_baseline_rainfall_mm is not None:
            ratio = context.rainfall_total_mm / context.climate_baseline_rainfall_mm
            if ratio < 0.5:
                environmental_data["drought_risk"] = "high"
            elif ratio < 0.75:
                environmental_data["drought_risk"] = "moderate"
            else:
                environmental_data["drought_risk"] = "low"
        
        if context.rain_7d_mm is not None and context.rain_7d_mm > 80:
            environmental_data["flood_risk"] = "moderate"
        elif context.rain_7d_mm is not None:
            environmental_data["flood_risk"] = "low"

        return {
            "crop": {
                "name": crop.name,
                "category": crop.category,
                "duration_months": crop.duration_months,
            },
            "season": {
                "month": month,
                "month_name": month_name,
            },
            "suitability": {
                "score": suitability_index,
                "limiting_factor": limiting_factor,
            },
            "rotation": {
                "previous_crop": previous_crop,
                "effect": rotation_effect,
            },
            "environment": environmental_data,
            "data_mode": context.data_mode,
        }

    def _get_system_prompt(self) -> str:
        """Return the system prompt for the AI model."""
        return """You are FarmTwin's annual crop plan assistant.

Your job is to explain why a specific crop was recommended for a specific month in the farmer's annual plan.

STRICT RULES:
1. Write 2-5 complete sentences explaining the recommendation.
2. Use ONLY the environmental data provided - never invent weather, rainfall, soil, or other measurements.
3. Explain why this crop fits THIS month/season.
4. Mention climate/rainfall/temperature suitability when data is available.
5. Mention soil/moisture conditions when data is available.
6. Assess drought/flood risk when available.
7. Mention crop rotation effects (preferred/avoid/diverse) when a previous crop exists.
8. Keep the tone practical and agronomic - advice a farmer can use.
9. If the limiting factor is significant, acknowledge it but stay positive.
10. Never mention scores or numbers directly - translate them into farmer-friendly language.
11. Use simple English suitable for farmers.
12. Do not mention "suitability score", "index", "assessment" or technical AI terminology.
13. Return valid JSON matching this schema:
{
  "explanation": "2-5 sentences explaining why this crop was recommended for this month"
}

Focus on crop-specific reasoning based on the actual environmental conditions and rotation context provided."""

    def _rule_based_fallback(
        self,
        crop_name: str,
        month_name: str,
        suitability_index: int,
        limiting_factor: str | None,
        previous_crop: str | None,
        rotation_effect: str,
    ) -> AnnualPlanExplanation:
        """Generate rule-based explanation when AI is unavailable."""
        sentences = []
        
        # Base sentence about seasonal fit
        if suitability_index >= 82:
            sentences.append(f"{crop_name} is well-suited for {month_name} conditions.")
        elif suitability_index >= 60:
            sentences.append(f"{crop_name} is a reasonable match for {month_name} conditions.")
        else:
            sentences.append(f"{crop_name} can be grown in {month_name} with careful management.")
        
        # Rotation sentence
        if previous_crop and rotation_effect == "preferred":
            sentences.append(f"It provides a preferred rotation after {previous_crop}.")
        elif previous_crop and rotation_effect == "avoid":
            sentences.append(f"Rotation after {previous_crop} carries a planning penalty.")
        elif previous_crop and rotation_effect == "diverse":
            sentences.append(f"It adds crop diversity after {previous_crop}.")
        elif rotation_effect == "same-crop penalty":
            sentences.append("Repeating the same crop carries a rotation penalty.")
        
        # Limiting factor sentence
        if limiting_factor and suitability_index < 82:
            sentences.append(f"The main limiting factor is {limiting_factor}.")
        
        text = " ".join(sentences)
        
        return AnnualPlanExplanation(
            text=text,
            source="rule_based_fallback",
            cached=False,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_service_instance: AnnualPlanExplanationService | None = None


def get_annual_plan_explanation_service(
    settings: Settings | None = None
) -> AnnualPlanExplanationService:
    """Get or create the annual plan explanation service singleton."""
    global _service_instance
    
    if _service_instance is None:
        if settings is None:
            from app.core.config import get_settings
            settings = get_settings()
        _service_instance = AnnualPlanExplanationService(settings)
    
    return _service_instance

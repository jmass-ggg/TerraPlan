"""
AI-generated crop suitability explanations.

Translates deterministic crop assessment data into farmer-friendly explanations
using OpenRouter LLM API. Falls back to deterministic descriptions on failure.

Requirements: AI must never modify scores, only explain them.
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx
import redis.asyncio as aioredis

from app.core.config import Settings
from app.domain.crop_engine import SimulationResult
from app.domain.crop_register import CropRequirements

logger = logging.getLogger(__name__)

# Cache TTL: 7 days (explanations remain valid for a snapshot version)
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CropExplanationFactor:
    """A single strength or concern about the crop."""
    factor: str
    message: str


@dataclass(frozen=True)
class CropExplanation:
    """Farmer-friendly explanation of crop suitability assessment."""
    headline: str
    summary: str
    strengths: tuple[CropExplanationFactor, ...]
    concerns: tuple[CropExplanationFactor, ...]
    action: str | None
    data_note: str | None
    source: str  # "ai" | "deterministic_fallback"
    cached: bool


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class CropExplanationProvider(ABC):
    """Abstract interface for crop explanation generation."""

    @abstractmethod
    async def generate(
        self,
        result: SimulationResult,
        crop_requirements: CropRequirements,
        farm_id: str,
    ) -> CropExplanation:
        """Generate farmer-friendly explanation for the crop assessment."""
        pass


# ---------------------------------------------------------------------------
# OpenRouter provider
# ---------------------------------------------------------------------------

class OpenRouterCropExplanationProvider(CropExplanationProvider):
    """Generates crop explanations via OpenRouter API with Redis caching."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._api_key = settings.openrouter_api_key.get_secret_value()
        self._model = settings.openrouter_model
        self._timeout = settings.openrouter_timeout_seconds
        self._redis_url = settings.redis_url

    async def generate(
        self,
        result: SimulationResult,
        crop_requirements: CropRequirements,
        farm_id: str,
    ) -> CropExplanation:
        """
        Generate explanation with caching.
        
        Falls back to deterministic explanation on any failure.
        """
        # Check if API key is configured
        if not self._api_key:
            logger.debug("OpenRouter API key not configured, using deterministic fallback")
            return self._deterministic_fallback(result)

        # Generate cache key
        cache_key = self._generate_cache_key(result, farm_id)

        # Try cache first
        try:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.info(
                    "crop_explanation.cache_hit: crop=%s, farm=%s",
                    result.crop_name,
                    farm_id,
                )
                return cached
        except Exception as exc:
            logger.warning("crop_explanation.cache_read_failed: %s", exc)

        # Generate via AI
        try:
            explanation = await self._generate_via_openrouter(result, crop_requirements, farm_id)
            # Cache the result
            try:
                await self._cache_explanation(cache_key, explanation)
            except Exception as exc:
                logger.warning("crop_explanation.cache_write_failed: %s", exc)
            return explanation
        except Exception as exc:
            logger.warning(
                "crop_explanation.ai_failed: crop=%s, farm=%s, error=%s",
                result.crop_name,
                farm_id,
                str(exc),
            )
            return self._deterministic_fallback(result)

    def _generate_cache_key(self, result: SimulationResult, farm_id: str) -> str:
        """Generate cache key from farm, crop, and snapshot."""
        # Include snapshot_id (or "no_snapshot") to invalidate when data changes
        snapshot_part = result.snapshot_id or "no_snapshot"
        data_mode_part = result.data_mode
        
        # Create hash of the key components
        key_material = f"{farm_id}:{result.crop_name}:{snapshot_part}:{data_mode_part}"
        key_hash = hashlib.sha256(key_material.encode()).hexdigest()[:16]
        
        return f"farmtwin:crop_explanation:{key_hash}"

    async def _get_cached(self, cache_key: str) -> CropExplanation | None:
        """Retrieve cached explanation from Redis."""
        async with aioredis.from_url(self._redis_url) as client:
            cached_data = await client.get(cache_key)
            if cached_data:
                data = json.loads(cached_data)
                return CropExplanation(
                    headline=data["headline"],
                    summary=data["summary"],
                    strengths=tuple(
                        CropExplanationFactor(**f) for f in data["strengths"]
                    ),
                    concerns=tuple(
                        CropExplanationFactor(**f) for f in data["concerns"]
                    ),
                    action=data.get("action"),
                    data_note=data.get("data_note"),
                    source="ai",
                    cached=True,
                )
        return None

    async def _cache_explanation(self, cache_key: str, explanation: CropExplanation) -> None:
        """Cache explanation in Redis."""
        data = {
            "headline": explanation.headline,
            "summary": explanation.summary,
            "strengths": [{"factor": f.factor, "message": f.message} for f in explanation.strengths],
            "concerns": [{"factor": f.factor, "message": f.message} for f in explanation.concerns],
            "action": explanation.action,
            "data_note": explanation.data_note,
        }
        async with aioredis.from_url(self._redis_url) as client:
            await client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(data))

    async def _generate_via_openrouter(
        self,
        result: SimulationResult,
        crop_requirements: CropRequirements,
        farm_id: str,
    ) -> CropExplanation:
        """Call OpenRouter API to generate explanation."""
        import time
        start_time = time.time()

        # Build structured prompt payload
        prompt_data = self._build_prompt_data(result, crop_requirements)
        
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
            "crop_explanation.ai_success: crop=%s, farm=%s, model=%s, latency_ms=%d",
            result.crop_name,
            farm_id,
            self._model,
            latency_ms,
        )
        
        # Convert to CropExplanation
        return CropExplanation(
            headline=explanation_json.get("headline", "")[:100],  # Truncate to reasonable length
            summary=explanation_json.get("summary", "")[:300],
            strengths=tuple(
                CropExplanationFactor(
                    factor=s.get("factor", "")[:50],
                    message=s.get("message", "")[:200],
                )
                for s in explanation_json.get("strengths", [])[:2]  # Max 2
            ),
            concerns=tuple(
                CropExplanationFactor(
                    factor=c.get("factor", "")[:50],
                    message=c.get("message", "")[:200],
                )
                for c in explanation_json.get("concerns", [])[:2]  # Max 2
            ),
            action=explanation_json.get("action", "")[:150] if explanation_json.get("action") else None,
            data_note=explanation_json.get("data_note", "")[:150] if explanation_json.get("data_note") else None,
            source="ai",
            cached=False,
        )

    def _build_prompt_data(
        self,
        result: SimulationResult,
        crop_requirements: CropRequirements,
    ) -> dict[str, Any]:
        """Build structured data for the AI prompt."""
        # Identify missing components
        missing_components = []
        components_dict: dict[str, Any] = {}
        
        if result.components.temperature is not None:
            components_dict["temperature"] = {
                "score": int(round(result.components.temperature)),
                "available": True,
            }
        else:
            missing_components.append("temperature")
            
        if result.components.water is not None:
            components_dict["water"] = {
                "score": int(round(result.components.water)),
                "available": True,
            }
        else:
            missing_components.append("water")
            
        if result.components.soil is not None:
            components_dict["soil"] = {
                "score": int(round(result.components.soil)),
                "available": True,
            }
        else:
            missing_components.append("soil")
            
        if result.components.heat_safety is not None:
            components_dict["heat_safety"] = int(round(result.components.heat_safety))
            
        if result.components.drought_flood_safety is not None:
            components_dict["drought_flood_safety"] = int(round(result.components.drought_flood_safety))
            
        if result.components.environmental_condition is not None:
            components_dict["environmental_condition"] = int(round(result.components.environmental_condition))

        return {
            "crop": {
                "name": result.crop_name,
                "category": crop_requirements.category,
            },
            "crop_requirements": {
                "temperature_min_c": crop_requirements.min_temp_c,
                "temperature_optimum_c": crop_requirements.optimum_temp_c,
                "temperature_max_c": crop_requirements.max_temp_c,
                "rainfall_min_mm": crop_requirements.min_rainfall_mm,
                "rainfall_max_mm": crop_requirements.max_rainfall_mm,
                "soil_ph_min": crop_requirements.min_soil_ph,
                "soil_ph_max": crop_requirements.max_soil_ph,
                "drought_tolerance": crop_requirements.drought_tolerance,
                "duration_months": crop_requirements.duration_months,
            },
            "assessment": {
                "overall_score": result.suitability_index,
                "status": result.label,
                "hard_exclusion": result.hard_exclusion,
                "hard_exclusion_reason": result.hard_exclusion_reason,
                "components": components_dict,
                "primary_limiting_factor": result.limiting_factor,
                "missing_components": missing_components,
            },
        }

    def _get_system_prompt(self) -> str:
        """Return the system prompt for the AI model."""
        return """You are FarmTwin's crop explanation assistant.

Your job is NOT to calculate crop suitability. FarmTwin has already calculated the crop's suitability using deterministic environmental rules.

Your job is to translate that assessment into short, clear advice that a farmer can understand.

STRICT RULES:
1. Never change, recalculate or disagree with the supplied suitability score.
2. Never invent weather, rainfall, soil, yield, crop requirements or environmental measurements.
3. Only use information supplied in the input.
4. If a component is unavailable, explicitly say that the assessment does not currently include that component.
5. Identify the strongest positive factor (if any).
6. Identify the most important limiting factor.
7. Explain what the result means practically for growing this crop.
8. When appropriate, provide ONE simple action the farmer could consider.
9. Use simple English suitable for farmers.
10. Avoid technical AI terminology.
11. Do not mention normalization, weighted vectors or mathematical implementation.
12. Do not claim certainty about outcomes.
13. Do not claim that the crop will definitely succeed or fail.
14. Keep the explanation concise.
15. Return valid JSON matching this schema:
{
  "headline": "Short headline (max 8 words)",
  "summary": "Brief summary (35-50 words)",
  "strengths": [{"factor": "Component name", "message": "Why it's good"}],
  "concerns": [{"factor": "Component name", "message": "Why it's limiting"}],
  "action": "One actionable suggestion (max 25 words, optional)",
  "data_note": "Note about missing data (max 25 words, optional)"
}

Maximum: 2 strengths, 2 concerns.

Focus on crop-specific reasoning. Different crops should receive different explanations based on their unique requirements and the actual assessment data."""

    def _deterministic_fallback(self, result: SimulationResult) -> CropExplanation:
        """Generate deterministic explanation when AI is unavailable."""
        # Build simple deterministic explanation
        if result.hard_exclusion:
            headline = "Not suitable for this farm"
            summary = f"{result.crop_name} is excluded due to environmental conditions that exceed tolerance thresholds."
            strengths = ()
            concerns = (
                CropExplanationFactor(
                    factor="Environmental conditions",
                    message=result.hard_exclusion_reason or "Conditions exceed crop tolerance",
                ),
            )
            action = "Consider alternative crops better suited to current conditions."
        elif result.suitability_index and result.suitability_index >= 82:
            headline = "Strong match for this farm"
            summary = f"{result.crop_name} is a good match for current conditions. The main constraint is {result.limiting_factor}."
            strengths = (
                CropExplanationFactor(
                    factor="Overall suitability",
                    message=f"Conditions are favorable for {result.crop_name}",
                ),
            )
            concerns = (
                CropExplanationFactor(
                    factor=result.limiting_factor.title(),
                    message="This is the primary limiting factor",
                ),
            )
            action = None
        else:
            headline = f"Moderate match: {result.limiting_factor} limits suitability"
            summary = f"{result.crop_name} scores {result.suitability_index}/100. The primary limiting factor is {result.limiting_factor}."
            strengths = ()
            concerns = (
                CropExplanationFactor(
                    factor=result.limiting_factor.title(),
                    message="This factor significantly reduces suitability",
                ),
            )
            action = f"Review {result.limiting_factor} conditions before planting."

        # Add data completeness note if applicable
        data_note = None
        missing = [k for k, v in result.input_completeness.items() if v == "missing"]
        if missing:
            data_note = f"Assessment excludes {', '.join(missing[:2])} (unavailable)."

        return CropExplanation(
            headline=headline,
            summary=summary,
            strengths=strengths,
            concerns=concerns,
            action=action,
            data_note=data_note,
            source="deterministic_fallback",
            cached=False,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_provider_instance: CropExplanationProvider | None = None


def get_crop_explanation_provider(settings: Settings | None = None) -> CropExplanationProvider:
    """Get or create the crop explanation provider singleton."""
    global _provider_instance
    
    if _provider_instance is None:
        if settings is None:
            from app.core.config import get_settings
            settings = get_settings()
        _provider_instance = OpenRouterCropExplanationProvider(settings)
    
    return _provider_instance

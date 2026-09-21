"""
Cache service for expensive external API calls.

Uses Redis (if available) with graceful degradation when unavailable.
"""
from typing import Any, Optional
import json
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

try:
    from redis import Redis
    from redis.exceptions import RedisError
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not installed - caching disabled")


class CacheService:
    """Manages caching for external API responses."""
    
    def __init__(self, redis_client: Optional[Any] = None):
        """Initialize cache service with optional Redis client.
        
        Args:
            redis_client: Optional Redis client. If None or Redis unavailable,
                         caching will be disabled.
        """
        self.redis = redis_client if REDIS_AVAILABLE else None
        self._enabled = self.redis is not None
        
        if not REDIS_AVAILABLE and redis_client is not None:
            logger.warning("Redis client provided but redis package not installed")
    
    def _make_key(self, provider: str, **params) -> str:
        """Generate cache key from provider + params.
        
        Example: "soil:lat=0.5143:lon=35.2698"
        """
        param_str = ":".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"{provider}:{param_str}"
    
    async def get(self, provider: str, **params) -> Optional[dict]:
        """Retrieve cached response.
        
        Args:
            provider: Provider name (e.g., "soilgrids")
            **params: Cache key parameters (e.g., lat=0.5, lon=35.2)
            
        Returns:
            Cached dict if found, None otherwise
        """
        if not self._enabled:
            return None
            
        key = self._make_key(provider, **params)
        
        try:
            data = self.redis.get(key)
            if data:
                logger.debug(f"Cache hit: {key}")
                return json.loads(data)
            else:
                logger.debug(f"Cache miss: {key}")
                return None
        except Exception as exc:
            logger.warning(f"Cache get failed for {key}: {exc}")
            return None
    
    async def set(
        self,
        provider: str,
        data: dict,
        ttl_days: int = 30,
        **params
    ) -> bool:
        """Store response in cache.
        
        Args:
            provider: Provider name (e.g., "soilgrids")
            data: Response data to cache
            ttl_days: Time-to-live in days (default 30)
            **params: Cache key parameters (e.g., lat=0.5, lon=35.2)
            
        Returns:
            True if cached successfully, False otherwise
        """
        if not self._enabled:
            return False
            
        key = self._make_key(provider, **params)
        
        try:
            self.redis.setex(
                key,
                timedelta(days=ttl_days),
                json.dumps(data)
            )
            logger.debug(f"Cache set: {key} (TTL: {ttl_days} days)")
            return True
        except Exception as exc:
            logger.warning(f"Cache set failed for {key}: {exc}")
            return False

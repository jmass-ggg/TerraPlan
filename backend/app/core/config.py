"""
Typed configuration with validation for FarmTwin backend.

Requirements: 1.1, 1.4, 1.5, 1.7, 9.7, 12.1, 13.1
"""

import ipaddress
import json
from enum import Enum
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Environment(str, Enum):
    """Deployment environment"""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class DataMode(str, Enum):
    """Data source mode, independent of deployment environment"""

    LIVE = "live"
    HISTORICAL_REPLAY = "historical_replay"
    DEMONSTRATION = "demonstration"


class AuthMode(str, Enum):
    """Authentication mode"""

    OIDC = "oidc"
    LOCAL_DEMO = "local_demo"


class DatabaseSettings(BaseModel):
    """Database connection and pool settings"""

    host: str
    port: Annotated[int, Field(gt=0, le=65535)] = 5432
    name: str
    user: str
    password: SecretStr
    pool_size: Annotated[int, Field(gt=0)] = 5
    max_overflow: Annotated[int, Field(ge=0)] = 5
    pool_timeout_seconds: Annotated[float, Field(gt=0, le=1)] = 1.0
    connect_timeout_seconds: Annotated[float, Field(gt=0, le=1)] = 1.0
    statement_timeout_ms: Annotated[int, Field(gt=0, le=1000)] = 1000

    model_config = {"extra": "ignore"}

    def get_url(self) -> URL:
        """Construct SQLAlchemy URL safely without exposing password"""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        )

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Override to exclude password from dumps"""
        d = super().model_dump(**kwargs)
        if "password" in d:
            d["password"] = "***"
        return d

    def __repr__(self) -> str:
        """Safe representation without password"""
        return (
            f"DatabaseSettings(host={self.host!r}, port={self.port}, "
            f"name={self.name!r}, user={self.user!r}, password='***', "
            f"pool_size={self.pool_size}, max_overflow={self.max_overflow})"
        )


class AuthSettings(BaseModel):
    """Authentication settings"""

    mode: AuthMode = AuthMode.OIDC
    issuer: HttpUrl | None = None
    audience: str | None = None
    jwks_url: HttpUrl | None = None
    algorithms: list[str] | None = None

    model_config = {"extra": "ignore"}

    @field_validator("algorithms", mode="before")
    @classmethod
    def parse_algorithms(cls, v: Any) -> list[str] | None:
        """Parse algorithms from JSON string or list"""
        if v is None:
            return None
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if not isinstance(parsed, list):
                    raise ValueError("algorithms must be a JSON array")
                return parsed
            except json.JSONDecodeError as e:
                raise ValueError(f"algorithms must be valid JSON: {e}")
        if isinstance(v, list):
            return v
        raise ValueError("algorithms must be a JSON array or list")

    # NO validation here - it happens at the Settings level after all fields are assembled


class DemoSettings(BaseModel):
    """Local demo mode settings"""

    local_only: bool = False
    isolated_database: bool = False

    model_config = {"extra": "ignore"}


class CORSSettings(BaseModel):
    """CORS configuration"""

    origins: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("origins", mode="before")
    @classmethod
    def parse_origins(cls, v: Any) -> list[str]:
        """Parse origins from JSON string or list"""
        if isinstance(v, str):
            if not v:
                return []
            try:
                parsed = json.loads(v)
                if not isinstance(parsed, list):
                    raise ValueError("CORS__ORIGINS must be a JSON array")
                return parsed
            except json.JSONDecodeError as e:
                raise ValueError(f"CORS__ORIGINS must be valid JSON: {e}")
        if isinstance(v, list):
            return v
        if v is None:
            return []
        raise ValueError("CORS__ORIGINS must be a JSON array or list")

    @field_validator("origins")
    @classmethod
    def validate_exact_origins(cls, v: list[str]) -> list[str]:
        """Require serialized HTTP origins, never patterns or URLs with credentials."""
        seen: set[str] = set()
        for origin in v:
            if not isinstance(origin, str):
                raise ValueError("Each CORS origin must be a string")
            if "*" in origin:
                raise ValueError(
                    f"Wildcard CORS origin not allowed: {origin}. "
                    "Use exact origins only."
                )
            if origin != origin.strip() or any(char.isspace() for char in origin):
                raise ValueError("CORS origins must not contain whitespace")
            if "\\" in origin:
                raise ValueError("CORS origins must use canonical URL separators")

            parsed = urlsplit(origin)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("CORS origin contains an invalid port") from exc

            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(
                    "CORS origins must be exact http:// or https:// origins"
                )
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Credential-bearing CORS origins are not allowed")
            if parsed.path or parsed.query or parsed.fragment:
                raise ValueError(
                    "CORS origins must contain only scheme, host, and optional port"
                )
            if origin in seen:
                raise ValueError(f"Duplicate CORS origin not allowed: {origin}")
            seen.add(origin)
        return v


class ProxySettings(BaseModel):
    """Reverse proxies whose forwarding metadata may change the ASGI scope."""

    trusted_ips: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("trusted_ips", mode="before")
    @classmethod
    def parse_trusted_ips(cls, v: Any) -> list[str]:
        """Parse an explicit JSON list of proxy IP addresses or CIDR networks."""
        if isinstance(v, str):
            if not v:
                return []
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"PROXY__TRUSTED_IPS must be valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, list):
                raise ValueError("PROXY__TRUSTED_IPS must be a JSON array")
            return parsed
        if isinstance(v, list):
            return v
        if v is None:
            return []
        raise ValueError("PROXY__TRUSTED_IPS must be a JSON array or list")

    @field_validator("trusted_ips")
    @classmethod
    def validate_trusted_ips(cls, v: list[str]) -> list[str]:
        """Fail closed on wildcard, malformed, and duplicate proxy entries."""
        seen: set[str] = set()
        for entry in v:
            if not isinstance(entry, str) or not entry or entry != entry.strip():
                raise ValueError("Trusted proxies must be non-blank IP/CIDR strings")
            if entry == "*":
                raise ValueError("Wildcard trusted proxy configuration is prohibited")
            try:
                if "/" in entry:
                    ipaddress.ip_network(entry, strict=True)
                else:
                    ipaddress.ip_address(entry)
            except ValueError as exc:
                raise ValueError(
                    f"Trusted proxy must be an exact IP or canonical CIDR: {entry}"
                ) from exc
            if entry in seen:
                raise ValueError(f"Duplicate trusted proxy not allowed: {entry}")
            seen.add(entry)
        return v


class Settings(BaseSettings):
    """Root application settings with nested configuration"""

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Required top-level settings
    environment: Environment
    data_mode: DataMode

    # Nested settings - Use Annotated with default_factory for proper settings parsing
    auth: Annotated[AuthSettings, Field(default_factory=lambda: AuthSettings())]
    database: Annotated[DatabaseSettings, Field(default_factory=lambda: DatabaseSettings())]
    demo: Annotated[DemoSettings, Field(default_factory=lambda: DemoSettings())]
    cors: Annotated[CORSSettings, Field(default_factory=lambda: CORSSettings())]
    proxy: Annotated[ProxySettings, Field(default_factory=lambda: ProxySettings())]

    # Redis URL for the analysis job queue (worker process)
    redis_url: str = "redis://localhost:6379"

    # OpenRouter AI configuration for crop explanations
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "google/gemini-flash-1.5-8b"
    openrouter_timeout_seconds: int = 10

    # Copernicus Data Space Ecosystem (CDSE) credentials for satellite band downloads.
    # Register free at https://dataspace.copernicus.eu/ then set both vars.
    # When blank, satellite band downloads will fail with 401 and the stage
    # will resolve to evidence_status="unavailable" (non-fatal).
    cdse_username: str = ""
    cdse_password: SecretStr = SecretStr("")

    # Conduit station coordinates — used when seeding the fixture station row.
    # Set to the physical location of your Conduit hardware (WGS84).
    # Defaults are for the demo station co-located with Sentinel-2 tile T37MBU
    # (central Kenya highlands, ~0.3°S / 36.8°E, ~1800 m elevation).
    conduit_station_latitude: float = -0.3
    conduit_station_longitude: float = 36.8
    conduit_station_elevation_m: float = 1800.0

    # Logging
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is a recognized level"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(
                f"LOG_LEVEL must be one of {valid_levels}, got {v!r}"
            )
        return v_upper

    @model_validator(mode="after")
    def validate_https_in_non_dev(self) -> "Settings":
        """Require HTTPS for identity URLs outside development and validate auth completion"""
        # Validate that OIDC mode has all required fields (Req 1.7)
        if self.auth.mode == AuthMode.OIDC:
            if not self.auth.issuer:
                raise ValueError("AUTH__ISSUER is required when AUTH__MODE is 'oidc'")
            if not self.auth.audience:
                raise ValueError("AUTH__AUDIENCE is required when AUTH__MODE is 'oidc'")
            if not self.auth.jwks_url:
                raise ValueError("AUTH__JWKS_URL is required when AUTH__MODE is 'oidc'")
            if not self.auth.algorithms:
                raise ValueError("AUTH__ALGORITHMS is required when AUTH__MODE is 'oidc'")
            if "none" in [alg.lower() for alg in self.auth.algorithms]:
                raise ValueError("AUTH__ALGORITHMS must not include 'none' algorithm")
                
            # Now check HTTPS requirements for non-development (Req 1.7)
            if self.environment != Environment.DEVELOPMENT:
                if self.auth.issuer.scheme != "https":
                    raise ValueError(
                        f"AUTH__ISSUER must use HTTPS in {self.environment.value} "
                        f"environment, got {self.auth.issuer.scheme}"
                    )
                if self.auth.jwks_url.scheme != "https":
                    raise ValueError(
                        f"AUTH__JWKS_URL must use HTTPS in {self.environment.value} "
                        f"environment, got {self.auth.jwks_url.scheme}"
                    )
        return self

    @model_validator(mode="after")
    def validate_demo_mode_restrictions(self) -> "Settings":
        """Enforce local demo restrictions (Req 1.6, 12.2)"""
        if self.auth.mode == AuthMode.LOCAL_DEMO:
            errors = []

            # Must be development environment
            if self.environment != Environment.DEVELOPMENT:
                errors.append(
                    f"LOCAL_DEMO requires ENVIRONMENT=development, "
                    f"got {self.environment.value}"
                )

            # Must have local_only enabled
            if not self.demo.local_only:
                errors.append("LOCAL_DEMO requires DEMO__LOCAL_ONLY=true")

            # Must have isolated database
            if not self.demo.isolated_database:
                errors.append("LOCAL_DEMO requires DEMO__ISOLATED_DATABASE=true")

            if errors:
                raise ValueError(
                    "Invalid local_demo configuration:\n  - " + "\n  - ".join(errors)
                )

        return self

    @model_validator(mode="after")
    def validate_independence(self) -> "Settings":
        """Ensure environment, auth mode and data mode remain independent (Req 1.2, 1.3)"""
        # This is documented behavior - no specific validation needed beyond
        # the demo restrictions above. The settings are already independent by design.
        return self

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Override to exclude secrets from dumps"""
        d = super().model_dump(**kwargs)
        # Replace the nested database dict with the sanitized version
        d["database"] = self.database.model_dump(**kwargs)
        return d

    def __repr__(self) -> str:
        """Safe representation without secrets"""
        return (
            f"Settings(environment={self.environment.value}, "
            f"data_mode={self.data_mode.value}, "
            f"auth_mode={self.auth.mode.value}, "
            f"log_level={self.log_level})"
        )


# FastAPI dependency for settings injection
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """
    Get settings instance for FastAPI dependency injection.
    
    Creates settings once and reuses the same instance.
    Allows tests to inject custom settings by setting _settings_instance.
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def set_settings(settings: Settings) -> None:
    """Set custom settings instance (primarily for testing)"""
    global _settings_instance
    _settings_instance = settings


def clear_settings() -> None:
    """Clear settings instance (primarily for testing)"""
    global _settings_instance
    _settings_instance = None

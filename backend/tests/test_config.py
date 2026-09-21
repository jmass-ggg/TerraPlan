"""
Tests for configuration validation and security rules.

Feature: backend-foundation, Property 1: Invalid configuration fails safely
Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

import os

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError

from app.core.config import (
    AuthMode,
    DataMode,
    Environment,
    Settings,
)


# ---------------------------------------------------------------------------
# Helpers - isolate from .env
# ---------------------------------------------------------------------------

def _oidc_dev(**overrides) -> dict:
    """Minimal valid OIDC development Settings kwargs, no .env loading."""
    base = dict(
        _env_file=None,
        environment="development",
        data_mode="live",
        auth={
            "mode": "oidc",
            "issuer": "https://auth.example.com",
            "audience": "farmtwin-api",
            "jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "algorithms": ["RS256"],
        },
        database={
            "host": "localhost",
            "name": "farmtwin",
            "user": "user",
            "password": "pass",
        },
    )
    base.update(overrides)
    return base


def _demo_dev(**overrides) -> dict:
    """Minimal valid local-demo Settings kwargs, no .env loading."""
    base = dict(
        _env_file=None,
        environment="development",
        data_mode="demonstration",
        auth={"mode": "local_demo"},
        demo={"local_only": True, "isolated_database": True},
        database={
            "host": "localhost",
            "name": "farmtwin_demo",
            "user": "demo_user",
            "password": "demo_pass",
        },
    )
    base.update(overrides)
    return base


# ============================================================================
# Valid Configuration Tests
# ============================================================================


def test_valid_oidc_production_config():
    config = Settings(
        _env_file=None,
        environment="production",
        data_mode="live",
        auth={
            "mode": "oidc",
            "issuer": "https://auth.example.com",
            "audience": "farmtwin-api",
            "jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "algorithms": ["RS256"],
        },
        database={
            "host": "db.example.com",
            "name": "farmtwin",
            "user": "app_user",
            "password": "secret123",
        },
        cors={"origins": ["https://app.example.com"]},
    )
    assert config.environment == Environment.PRODUCTION
    assert config.data_mode == DataMode.LIVE
    assert config.auth.mode == AuthMode.OIDC
    assert config.auth.issuer.scheme == "https"
    assert config.auth.algorithms == ["RS256"]


def test_valid_demo_config():
    config = Settings(**_demo_dev())
    assert config.environment == Environment.DEVELOPMENT
    assert config.data_mode == DataMode.DEMONSTRATION
    assert config.auth.mode == AuthMode.LOCAL_DEMO
    assert config.demo.local_only is True
    assert config.demo.isolated_database is True


def test_historical_replay_mode():
    config = Settings(
        _env_file=None,
        environment="staging",
        data_mode="historical_replay",
        auth={
            "mode": "oidc",
            "issuer": "https://auth.example.com",
            "audience": "farmtwin-api",
            "jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "algorithms": ["RS256"],
        },
        database={"host": "localhost", "name": "farmtwin", "user": "user", "password": "pass"},
    )
    assert config.data_mode == DataMode.HISTORICAL_REPLAY


# ============================================================================
# Missing Required Fields
# ============================================================================


def test_missing_required_environment():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            data_mode="live",
            auth={"mode": "oidc", "issuer": "https://a.example.com",
                  "audience": "api", "jwks_url": "https://a.example.com/jwks.json",
                  "algorithms": ["RS256"]},
            database={"host": "localhost", "name": "farmtwin", "user": "u", "password": "p"},
        )
    assert "environment" in str(exc_info.value).lower()


def test_missing_required_data_mode():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            environment="development",
            auth={"mode": "oidc", "issuer": "https://a.example.com",
                  "audience": "api", "jwks_url": "https://a.example.com/jwks.json",
                  "algorithms": ["RS256"]},
            database={"host": "localhost", "name": "farmtwin", "user": "u", "password": "p"},
        )
    assert "data_mode" in str(exc_info.value).lower()


def test_missing_database_host():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            environment="development",
            data_mode="live",
            auth={"mode": "oidc", "issuer": "https://a.example.com",
                  "audience": "api", "jwks_url": "https://a.example.com/jwks.json",
                  "algorithms": ["RS256"]},
            database={"name": "farmtwin", "user": "u", "password": "p"},
        )
    assert "host" in str(exc_info.value).lower()


def test_invalid_environment_value():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            environment="invalid_env",
            data_mode="live",
            database={"host": "h", "name": "n", "user": "u", "password": "p"},
        )
    assert "environment" in str(exc_info.value).lower()


def test_invalid_data_mode_value():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            environment="development",
            data_mode="invalid_mode",
            database={"host": "h", "name": "n", "user": "u", "password": "p"},
        )
    assert "data_mode" in str(exc_info.value).lower()


# ============================================================================
# OIDC Requirements (Req 1.7)
# ============================================================================


def test_oidc_missing_issuer():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(auth={
            "mode": "oidc",
            "audience": "farmtwin-api",
            "jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "algorithms": ["RS256"],
        }))
    assert "issuer" in str(exc_info.value).lower()


def test_oidc_missing_audience():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(auth={
            "mode": "oidc",
            "issuer": "https://auth.example.com",
            "jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "algorithms": ["RS256"],
        }))
    assert "audience" in str(exc_info.value).lower()


def test_oidc_missing_jwks_url():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(auth={
            "mode": "oidc",
            "issuer": "https://auth.example.com",
            "audience": "farmtwin-api",
            "algorithms": ["RS256"],
        }))
    assert "jwks" in str(exc_info.value).lower()


def test_oidc_missing_algorithms():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(auth={
            "mode": "oidc",
            "issuer": "https://auth.example.com",
            "audience": "farmtwin-api",
            "jwks_url": "https://auth.example.com/.well-known/jwks.json",
        }))
    assert "algorithm" in str(exc_info.value).lower()


def test_oidc_rejects_none_algorithm():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(auth={
            "mode": "oidc",
            "issuer": "https://auth.example.com",
            "audience": "farmtwin-api",
            "jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "algorithms": ["RS256", "none"],
        }))
    assert "none" in str(exc_info.value).lower()


def test_oidc_requires_https_in_production():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            environment="production",
            data_mode="live",
            auth={
                "mode": "oidc",
                "issuer": "http://auth.example.com",
                "audience": "farmtwin-api",
                "jwks_url": "https://auth.example.com/.well-known/jwks.json",
                "algorithms": ["RS256"],
            },
            database={"host": "h", "name": "n", "user": "u", "password": "p"},
        )
    assert "https" in str(exc_info.value).lower()


def test_oidc_allows_http_in_development():
    config = Settings(
        _env_file=None,
        environment="development",
        data_mode="live",
        auth={
            "mode": "oidc",
            "issuer": "http://localhost:8080",
            "audience": "farmtwin-api",
            "jwks_url": "http://localhost:8080/.well-known/jwks.json",
            "algorithms": ["RS256"],
        },
        database={"host": "localhost", "name": "farmtwin", "user": "user", "password": "pass"},
    )
    assert config.auth.issuer.scheme == "http"
    assert config.auth.jwks_url.scheme == "http"


# ============================================================================
# Demo Mode Restrictions (Req 1.6, 12.2)
# ============================================================================


def test_demo_requires_development_environment():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_demo_dev(environment="production"))
    assert "development" in str(exc_info.value).lower()


def test_local_auth_and_data_mode_are_independent():
    config = Settings(**_demo_dev(data_mode="live"))
    assert config.data_mode == DataMode.LIVE


def test_demo_requires_local_only():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_demo_dev(demo={"local_only": False, "isolated_database": True}))
    assert "local_only" in str(exc_info.value).lower()


def test_demo_requires_isolated_database():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_demo_dev(demo={"local_only": True, "isolated_database": False}))
    assert "isolated" in str(exc_info.value).lower()


# ============================================================================
# CORS Validation (Req 13.1)
# ============================================================================


def test_cors_rejects_wildcard():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(cors={"origins": ["*"]}))
    assert "wildcard" in str(exc_info.value).lower()


def test_cors_accepts_exact_origins():
    config = Settings(**_oidc_dev(cors={"origins": [
        "https://app.example.com",
        "https://admin.example.com",
    ]}))
    assert len(config.cors.origins) == 2
    assert "https://app.example.com" in config.cors.origins


def test_cors_empty_origins_allowed():
    config = Settings(**_oidc_dev(cors={"origins": []}))
    assert config.cors.origins == []


# ============================================================================
# Database Settings (Req 1.4)
# ============================================================================


def test_invalid_database_port():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(database={
            "host": "localhost", "port": 70000,
            "name": "farmtwin", "user": "user", "password": "pass",
        }))
    assert "port" in str(exc_info.value).lower()


def test_invalid_pool_size():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(database={
            "host": "localhost", "name": "farmtwin",
            "user": "user", "password": "pass", "pool_size": 0,
        }))
    assert "pool" in str(exc_info.value).lower()


def test_invalid_timeout():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(database={
            "host": "localhost", "name": "farmtwin",
            "user": "user", "password": "pass", "pool_timeout_seconds": -1,
        }))
    assert "timeout" in str(exc_info.value).lower()


# ============================================================================
# Log Level Validation (Req 9.7)
# ============================================================================


def test_valid_log_levels():
    for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        config = Settings(**_oidc_dev(log_level=level))
        assert config.log_level == level


def test_invalid_log_level():
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_oidc_dev(log_level="INVALID"))
    assert "log_level" in str(exc_info.value).lower()


# ============================================================================
# Secret Handling (Req 1.5)
# ============================================================================


def test_password_not_in_repr():
    config = Settings(**_oidc_dev(database={
        "host": "localhost", "name": "farmtwin",
        "user": "user", "password": "super_secret_password_canary_12345",
    }))
    assert "super_secret_password_canary_12345" not in repr(config)
    db_repr = repr(config.database)
    assert "super_secret_password_canary_12345" not in db_repr
    assert "***" in db_repr or "SecretStr" in db_repr


def test_password_not_in_model_dump():
    config = Settings(**_oidc_dev(database={
        "host": "localhost", "name": "farmtwin",
        "user": "user", "password": "super_secret_password_canary_67890",
    }))
    dump = config.model_dump()
    assert dump["database"]["password"] == "***"
    assert "super_secret_password_canary_67890" not in str(dump)


def test_password_not_in_validation_error():
    try:
        Settings(
            _env_file=None,
            environment="invalid",
            data_mode="live",
            database={
                "host": "localhost", "name": "farmtwin",
                "user": "user", "password": "secret_canary_validation_98765",
            },
        )
    except ValidationError as e:
        assert "secret_canary_validation_98765" not in str(e)


# ============================================================================
# Environment Variable Delimiter (Req 1.1)
# ============================================================================


def test_nested_delimiter_double_underscore():
    """Verify nested settings parse correctly from double-underscore env vars."""
    env_vars = {
        "ENVIRONMENT": "development",
        "DATA_MODE": "live",
        "DATABASE__HOST": "testhost",
        "DATABASE__NAME": "testdb",
        "DATABASE__USER": "testuser",
        "DATABASE__PASSWORD": "testpass",
        "AUTH__MODE": "oidc",
        "AUTH__ISSUER": "https://auth.test.com",
        "AUTH__AUDIENCE": "test-api",
        "AUTH__JWKS_URL": "https://auth.test.com/.well-known/jwks.json",
        "AUTH__ALGORITHMS": '["RS256"]',
    }

    original_env = {}
    for key, value in env_vars.items():
        original_env[key] = os.environ.get(key)
        os.environ[key] = value

    try:
        config = Settings(_env_file=None)
        assert config.database.host == "testhost"
        assert config.database.name == "testdb"
        assert config.auth.mode == AuthMode.OIDC
        assert str(config.auth.issuer) == "https://auth.test.com/"
    finally:
        for key, original_value in original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value


# ============================================================================
# Property-Based Tests
# ============================================================================


@given(
    environment=st.sampled_from(["development", "staging", "production"]),
    data_mode=st.sampled_from(["live", "historical_replay", "demonstration"]),
    port=st.integers(min_value=1, max_value=65535),
    pool_size=st.integers(min_value=1, max_value=50),
)
def test_property_valid_configs_succeed(
    environment: str, data_mode: str, port: int, pool_size: int
):
    """
    Property: Valid configuration combinations should succeed.

    Feature: backend-foundation, Property 1: Invalid configuration fails safely
    Validates: Requirements 1.1, 1.2, 1.3, 1.4
    """
    try:
        config = Settings(
            _env_file=None,
            environment=environment,
            data_mode=data_mode,
            auth={
                "mode": "oidc",
                "issuer": "https://auth.example.com",
                "audience": "farmtwin-api",
                "jwks_url": "https://auth.example.com/.well-known/jwks.json",
                "algorithms": ["RS256"],
            },
            database={
                "host": "localhost",
                "port": port,
                "name": "farmtwin",
                "user": "user",
                "password": "pass",
                "pool_size": pool_size,
            },
        )
        assert config.environment.value == environment
        assert config.data_mode.value == data_mode
        assert config.database.port == port
        assert config.database.pool_size == pool_size
    except ValidationError:
        pytest.fail(f"Valid configuration failed: env={environment}, mode={data_mode}")


@given(
    secret=st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=10,
        max_size=50,
    )
)
def test_property_secrets_never_exposed(secret: str):
    """
    Property: Secrets should never appear in representations or dumps.

    Feature: backend-foundation, Property 1: Invalid configuration fails safely
    Validates: Requirements 1.5
    """
    config = Settings(**_oidc_dev(database={
        "host": "localhost", "name": "farmtwin", "user": "user", "password": secret,
    }))
    assert secret not in repr(config)
    assert secret not in str(config.model_dump())
    assert secret not in repr(config.database)

    try:
        Settings(
            _env_file=None,
            environment="invalid_env",
            data_mode="live",
            database={"host": "localhost", "name": "farmtwin", "user": "user", "password": secret},
        )
    except ValidationError as e:
        assert secret not in str(e)


@given(origin=st.text(min_size=1, max_size=100))
def test_property_wildcards_rejected(origin: str):
    """
    Property: CORS origins containing wildcards should be rejected.

    Feature: backend-foundation, Property 1: Invalid configuration fails safely
    Validates: Requirements 13.1
    """
    if "*" in origin:
        with pytest.raises(ValidationError) as exc_info:
            Settings(**_oidc_dev(cors={"origins": [origin]}))
        assert "wildcard" in str(exc_info.value).lower()

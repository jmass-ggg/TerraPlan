# Dependency Selection Decisions

## Core Framework (Python 3.12+)

**FastAPI 0.115.0**
- Latest stable FastAPI with Pydantic v2 support
- Provides OpenAPI generation, dependency injection, async support
- [Docs](https://fastapi.tiangolo.com/)

**Uvicorn 0.31.0**
- ASGI server with uvloop for performance
- Standard extras include websockets and httptools
- [Docs](https://www.uvicorn.org/)

## Data Validation

**Pydantic 2.9.2**
- Core validation library with strict type checking
- V2 provides better performance and validation control
- [Docs](https://docs.pydantic.dev/)

**Pydantic Settings 2.6.0**
- Environment variable configuration with nested delimiter support
- Integrates with Pydantic v2
- [Docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

## Database

**SQLAlchemy 2.0.35**
- ORM with async support via asyncio extension
- Declarative base and relationship management
- [Async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

**asyncpg 0.29.0**
- High-performance async PostgreSQL driver
- Required for SQLAlchemy async operations
- [Docs](https://magicstack.github.io/asyncpg/)

**Alembic 1.13.3**
- Database migration tool for SQLAlchemy
- Supports transactional DDL and version management
- [Docs](https://alembic.sqlalchemy.org/)

**GeoAlchemy2 0.15.2**
- Spatial extension for SQLAlchemy
- Provides PostGIS geometry/geography types
- [Docs](https://geoalchemy-2.readthedocs.io/)

## Authentication

**python-jose[cryptography] 3.3.0**
- JWT encoding/decoding with cryptography backend
- OIDC token verification with JWK support
- [Docs](https://python-jose.readthedocs.io/)

## Testing

**Pytest 8.3.3**
- Standard Python testing framework
- Async support via pytest-asyncio
- [Docs](https://docs.pytest.org/)

**pytest-asyncio 0.24.0**
- Pytest plugin for async test functions
- Required for testing FastAPI async routes
- [Docs](https://pytest-asyncio.readthedocs.io/)

**Hypothesis 6.115.2**
- Property-based testing library
- Generates test cases for invariants
- Configured for 100 iterations minimum per property
- [Docs](https://hypothesis.readthedocs.io/)

**httpx 0.27.2**
- Async HTTP client for testing FastAPI routes
- Compatible with TestClient
- [Docs](https://www.python-httpx.org/)

## Key Behavioral Notes

### Pydantic Settings
- Use `env_nested_delimiter="__"` for nested configuration (e.g., `DATABASE__HOST`)
- SecretStr fields automatically excluded from repr/logs
- Model validation runs before application startup

### SQLAlchemy Async
- Each request gets one AsyncSession via dependency injection
- Sessions must be explicitly closed (handled by dependency)
- Use `async with` for transactions or explicit `begin()`
- Connection pool timeout and statement timeout must fit within probe budget

### FastAPI Lifespan
- Use `@asynccontextmanager` lifespan for startup/shutdown
- Initialize database engine and identity clients during startup
- Clean up resources (dispose engine) during shutdown
- [Lifespan docs](https://fastapi.tiangolo.com/advanced/events/)

### Alembic
- Run migrations via management command, not during request handling
- Transactional migrations use `op.execute()` within transaction
- Version table updates are part of migration transaction
- [Alembic docs](https://alembic.sqlalchemy.org/en/latest/tutorial.html)

### Testing with PostgreSQL
- Integration tests require actual PostgreSQL/PostGIS database
- Use isolated test database and credentials
- Do not substitute SQLite for migration/spatial tests
- Cleanup targets only test-created resources

## Version Verification Required

Before implementing unfamiliar features:
1. Check pinned version documentation
2. Verify async behavior matches SQLAlchemy 2.0 async patterns
3. Confirm timeout/pool settings align with driver capabilities
4. Test probe deadlines with actual database scenarios

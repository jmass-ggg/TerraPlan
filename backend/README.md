# FarmTwin Backend — Phase 10

Python 3.12 FastAPI service with PostgreSQL/PostGIS for agricultural field management and decision support.

## Product Scope

FarmTwin is a farm-level climate and agriculture decision-support platform. Farmers identify the exact boundary of their land on a map; the system combines that boundary with environmental observations, weather, soil, terrain, and satellite information to create a digital representation of the farm (the Farm Digital Twin).

The backend provides authenticated access to farm boundaries, environmental observations, crop suitability analysis, hazard screening, annual planning, climate scenarios, and data-source status.

## Phase 10 Capabilities

### ✅ Implemented and Verified (Phases 0–10)

**Foundation (Phases 0–3):**
- Configuration with validated environment, data, and auth modes
- PostgreSQL/PostGIS with Alembic transactional migrations
- Separate admin/runtime database roles with least-privilege separation
- OIDC bearer token verification and explicit local-demo mode
- Structured JSON logging (privacy-safe: no credentials, geometry, PII)
- Health (`/health`) and readiness (`/ready`) probes with 5-second deadline
- Consistent error envelope, stable codes, request ID propagation
- CORS with exact origins, proxy trust configuration

**Farm Persistence (Phase 4):**
- Farm creation, editing, deletion with GeoJSON Polygon boundary
- Shapely + PyProj geodesic area validation (WGS84)
- Geometry revision history with optimistic concurrency (409 on stale write)
- Idempotent farm creation (`Idempotency-Key` header)
- Cross-user ownership isolation (invisible/nonexistent equivalence)

**Conduit Pipeline (Phase 5 / earlier):**
- Historical fixture ingestion (191-record June 2025 Conduit dataset)
- Observation normalization: quality-flagged temperature consensus, VPD, humidity, wind, pressure
- Hourly and daily aggregate computation
- Deduplication by (station, valid_time) hash
- Data mode honesty: historical fixture always carries `historical_replay` mode

**Environmental Context and Digital Twin (Phase 5):**
- Provider adapters: Open-Meteo weather, Sentinel-2 satellite, SoilGrids soil, Copernicus DEM terrain, ERA5-Land climate baseline
- Unavailable sources return null evidence — never substituted with zeros
- Snapshot versioning: each twin is a dated, versioned snapshot of farm + environment
- Conduit eligibility check: distance ≤ 50 km, elevation difference ≤ 500 m

**Crop Suitability and Simulator (Phase 6):**
- 12-crop register (maize, beans, sorghum, cowpea, kale, tomatoes, cabbage, carrots, onions, potatoes, spinach, sweet potatoes)
- Deterministic scoring engine: temperature, water, soil, wind components
- Hard exclusion flags (crop cannot be grown under current conditions)
- Snapshot-backed context (analyses are tied to a versioned snapshot)
- Demonstration fallback when no live snapshot exists

**Hazards and Actions (Phase 7):**
- Drought, heat, heavy-rainfall, wind hazard screening
- Rule-based action recommendations linked to hazard drivers
- Action completion persistence (mark as done; state survives restart)
- Flood exposure explicitly unknown without terrain drainage evidence
- `app/data/actions/rules_v1.yaml` — auditable YAML rule registry

**Annual Planner (Phase 8):**
- Persistent monthly planting schedule per farm and plan year
- Overlap validation: crops cannot double-book the same field/month
- Change proposals: when environment changes, old slots get proposed revisions
- Reuses deterministic crop-scoring engine (no separate algorithm)
- `PATCH /api/v1/farms/{id}/plan/slots` — idempotent slot upsert

**Climate Scenarios (Phase 9):**
- Rainfall and temperature delta scenarios (−30% to +30%)
- Recomputes crop rankings and hazard levels against baseline snapshot
- Zero-delta identity: Δ=0 returns identical results to baseline
- Scenarios are hypothetical — never mutate the baseline snapshot

**Supporting Pages (Phase 10):**
- `GET /api/v1/data-sources` — all 6 providers with rich metadata (resolution, extent, license, pipeline explanation)
- `PATCH /api/v1/me` — display name update
- `not_configured` vs `unavailable` status distinction in data sources response
- No credentials, raw URLs, or secrets in any response

### ⚠️ Implemented but NOT verified for production

**OIDC Authentication:**
- JWT signature verification infrastructure is complete
- Real identity provider integration NOT verified
- Trust chain (frontend → provider → backend) NOT documented
- Do NOT accept real multi-user farm data until verification is complete
- See `docs/build-log.md` section 9.4

### ❌ Not implemented (deferred to Phases 11–12)

- Distributed rate limiting with 429 responses
- Mutation audit events
- Backup restoration verification
- Production identity provider and HTTPS configuration
- Live Conduit endpoint (historical fixture only; live endpoint credentials not configured)

## Data Modes

Every response carries `X-FarmTwin-Data-Mode` and `X-FarmTwin-Auth-Mode` headers.

| `DATA_MODE` | Meaning |
|---|---|
| `live` | Real-time provider data polled on-demand |
| `historical_replay` | Historical observations with preserved original timestamps |
| `demonstration` | Synthetic/fixture data; remote APIs not polled |

**Current default:** `demonstration` (historical Conduit fixture, no live provider calls)

## Quick Start — Local Demo Mode

### Prerequisites

```bash
python3 --version        # 3.12 or later
docker compose version   # v2+
# Ports needed: 5433 (demo DB), 8000 (API)
```

### Step-by-step setup

```bash
# 1. Navigate to backend directory
cd farmtwin/backend

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install locked dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# .env is already set for local demo mode — review:
# AUTH__MODE=local_demo
# DATA_MODE=demonstration
# DEMO__LOCAL_ONLY=true
# DEMO__ISOLATED_DATABASE=true
# DATABASE__PORT=5433

# 5. Start PostgreSQL + PostGIS (from farmtwin/ directory)
cd ..
docker compose --profile demo up -d db-demo
# Wait ~10 s then verify:
docker compose logs db-demo | grep "ready to accept connections"
cd backend

# 6. Run migrations (uses admin role)
python -m app.db.management upgrade
# Expected: Migration 20250908_0008_climate_scenarios applied

# 7. Initialise demo fixtures
python -m app.db.demo_setup
# Creates demo user UUID 00000000-0000-0000-0000-000000000001
# Ingests 191-record historical Conduit fixture

# 8. Start the API
uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload \
  --no-proxy-headers \
  --timeout-graceful-shutdown 20

# 9. Verify
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/api/v1/me
curl http://127.0.0.1:8000/api/v1/farms
curl http://127.0.0.1:8000/api/v1/data-sources
```

### Migrations (all 8 applied in order)

| Revision | Description |
|---|---|
| `0001_initial_schema` | Users, farms, geometry revisions, installation metadata |
| `0002_conduit_pipeline` | Stations, ingestion runs, normalized observations, aggregates |
| `0003_farm_idempotency` | `idempotency_key` on farms |
| `0004_runtime_permissions` | GRANT SELECT/INSERT/UPDATE to runtime role |
| `0005_environmental_twin` | Snapshots, provider evidence |
| `0006_hazard_action_completions` | Action completion records |
| `0007_annual_planner` | Plan years and plan slots |
| `0008_climate_scenarios` | Scenario parameters and results |

```bash
# Check current head
python -m app.db.management current

# Full history
python -m app.db.management history

# Upgrade to head
python -m app.db.management upgrade
```

### Troubleshooting

**Database connection error:**
```bash
docker compose ps db-demo
docker compose exec db-demo psql -U farmtwin_admin -d farmtwin_demo -c "SELECT 1;"
```

**Migration error:**
```bash
python -m app.db.management current
python -m app.db.management history
```

**API startup error:**
```bash
python -c "from app.core.config import Settings; Settings()"
```

**Port conflict:**
```bash
lsof -i :5433
lsof -i :8000
```

## Running Tests

Tests use pytest + Hypothesis for property-based tests against a real PostgreSQL/PostGIS database.

### Prerequisites

```bash
# Demo database must be running (port 5433)
docker compose --profile demo up -d db-demo
```

### Run the full suite

```bash
cd farmtwin/backend
pytest

# With verbosity
pytest -v

# With coverage
pytest --cov=app --cov-report=term-missing

# Property-based tests only
pytest -v -m hypothesis
```

### Key test files

| File | Coverage |
|---|---|
| `test_config.py` | Configuration validation (Property 1) |
| `test_authentication.py` | Auth boundary fail-closed (Property 3) |
| `test_schemas.py` | Typed validation (Property 5) |
| `test_data_modes.py` | Honest data modes (Property 10) |
| `test_farm_persistence.py` | Round-trip, ownership, revisions (Props 1, 2, 6–8) |
| `test_geometry_properties.py` | Geodesic area, topology, label point (Props 3–5) |
| `test_conduit_parser.py` | Conduit parsing and normalization |
| `test_conduit_normalizer.py` | Quality flagging and aggregation |
| `test_conduit_eligibility_properties.py` | Distance/elevation guard |
| `test_crop_engine_properties.py` | Deterministic scoring (Props) |
| `test_risk_engine_properties.py` | Hazard screening (Props) |
| `test_annual_planner_properties.py` | Overlap / monotonicity (Props) |
| `test_scenario_properties.py` | Zero-delta identity (Property) |
| `test_api_contract.py` | Versioned API contracts |
| `test_lifecycle.py` | Bounded probes and shutdown |
| `test_migration_integrity.py` | Schema and constraint integrity |
| `test_ownership.py` | Cross-user isolation |

### Test count (Phase 10 gate)

```
420+ backend tests passing, 0 failures
```

### Continuous integration

```bash
pip install -r requirements.txt
docker compose --profile demo up -d db-demo
timeout 30 bash -c 'until docker compose exec db-demo pg_isready; do sleep 1; done'
pytest -v --cov=app --cov-report=xml
black --check app/ tests/
ruff check app/ tests/
```

## Configuration

All settings use environment variables. See `.env.example` for the full template.

### Core settings

| Variable | Values | Required |
|---|---|---|
| `ENVIRONMENT` | `development` / `staging` / `production` | Yes |
| `DATA_MODE` | `live` / `historical_replay` / `demonstration` | Yes |
| `AUTH__MODE` | `oidc` / `local_demo` | Yes |
| `DATABASE__HOST` | Hostname | Yes |
| `DATABASE__PORT` | Port (default 5432) | No |
| `DATABASE__NAME` | DB name | Yes |
| `DATABASE__USER` | DB user | Yes |
| `DATABASE__PASSWORD` | DB password | Yes |
| `CORS__ORIGINS` | JSON array of origins | No |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | No |

Use double underscores `__` for nested settings (e.g. `DATABASE__HOST`).

### Authentication modes

#### Local demo (development only)

```bash
AUTH__MODE=local_demo
DATA_MODE=demonstration
DEMO__LOCAL_ONLY=true
DEMO__ISOLATED_DATABASE=true
ENVIRONMENT=development
```

Fixed demo user UUID: `00000000-0000-0000-0000-000000000001`  
No bearer token required. API binds to `127.0.0.1` only.

#### OIDC (multi-user — NOT VERIFIED)

```bash
AUTH__MODE=oidc
AUTH__ISSUER=https://your-provider.com
AUTH__AUDIENCE=https://api.farmtwin.com
AUTH__JWKS_URL=https://your-provider.com/.well-known/jwks.json
AUTH__ALGORITHMS=["RS256"]
```

See `docs/build-log.md` section 9.4 for verification requirements before enabling.

## Database management

Two roles with separated privileges:

| Role | Privileges | Use |
|---|---|---|
| `farmtwin_admin` | Full schema (CREATE, ALTER, DROP) | Alembic migrations only |
| `farmtwin_runtime` | Data only (SELECT, INSERT, UPDATE) | Running API |

```bash
# Upgrade to head
export DATABASE__USER=farmtwin_admin
export DATABASE__PASSWORD=admin_dev_password
python -m app.db.management upgrade

# Downgrade (TEST environments only — never production)
python -m app.db.management downgrade -1
```

See `app/db/MIGRATION_RECOVERY.md` for recovery procedures.

## API endpoints (Phase 10)

### Public

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Process health (no DB dependency) |
| `GET` | `/ready` | Full readiness: DB, PostGIS, migrations, marker |

### Protected (bearer token in OIDC; no token in local demo)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/me` | Current user profile |
| `PATCH` | `/api/v1/me` | Update display name |
| `GET` | `/api/v1/farms` | List farms (paginated) |
| `POST` | `/api/v1/farms` | Create farm (idempotent) |
| `GET` | `/api/v1/farms/{id}` | Farm detail with geometry |
| `PATCH` | `/api/v1/farms/{id}` | Update farm name or geometry |
| `DELETE` | `/api/v1/farms/{id}` | Delete farm |
| `GET` | `/api/v1/farms/{id}/twin` | Environmental twin snapshot |
| `GET` | `/api/v1/farms/{id}/crops` | Crop suitability list |
| `GET` | `/api/v1/farms/{id}/crops/{crop_id}` | Crop detail |
| `GET` | `/api/v1/farms/{id}/risks` | Hazard assessment |
| `GET` | `/api/v1/farms/{id}/risks/{hazard}` | Hazard detail |
| `POST` | `/api/v1/farms/{id}/actions/{action_id}/complete` | Mark action complete |
| `GET` | `/api/v1/farms/{id}/plan` | Annual plan (year param) |
| `PATCH` | `/api/v1/farms/{id}/plan/slots` | Upsert plan slot |
| `DELETE` | `/api/v1/farms/{id}/plan/slots/{slot_id}` | Remove plan slot |
| `POST` | `/api/v1/farms/{id}/scenarios` | Compute scenario |
| `GET` | `/api/v1/conduit/current` | Latest Conduit observation |
| `GET` | `/api/v1/conduit/features` | Latest daily aggregate |
| `GET` | `/api/v1/conduit/history` | Paginated observation history |
| `GET` | `/api/v1/data-sources` | All 6 provider entries |

Interactive docs: http://localhost:8000/docs  
OpenAPI spec: http://localhost:8000/openapi.json

## Project structure

```
backend/
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── conduit.py          # Conduit + data-sources endpoints
│   │   │   ├── crops.py            # Crop simulator endpoints
│   │   │   ├── crop_schemas.py     # Crop request/response schemas
│   │   │   ├── decision_support.py # Decision recommendations
│   │   │   ├── farm_schemas.py     # Farm request/response schemas
│   │   │   ├── farms.py            # Farm CRUD endpoints
│   │   │   ├── planner.py          # Annual planner endpoints
│   │   │   ├── planner_schemas.py  # Planner schemas
│   │   │   ├── profile.py          # User profile (GET + PATCH /me)
│   │   │   ├── risk_schemas.py     # Risk response schemas
│   │   │   ├── risks.py            # Hazard screening endpoints
│   │   │   ├── routes.py           # Router composition
│   │   │   ├── scenarios.py        # Climate scenario endpoints
│   │   │   └── twin.py             # Digital twin snapshot endpoint
│   │   ├── dependencies.py
│   │   ├── health.py
│   │   └── schemas.py
│   ├── core/                       # Config, security, DB, logging
│   ├── data/
│   │   ├── conduit/                # Parser, normalizer, deduplicator, aggregator
│   │   └── providers/              # Weather, satellite, soil, terrain, climate, Conduit eligibility
│   ├── domain/
│   │   ├── crop_engine.py          # Deterministic suitability scoring
│   │   ├── crop_register.py        # 12-crop requirement registry
│   │   ├── geometry.py             # Shapely + PyProj validation
│   │   ├── risk_engine.py          # Hazard screening logic
│   │   ├── risk_rules.py           # Rule loader (rules_v1.yaml)
│   │   └── snapshot_context.py     # Farm snapshot assembly
│   ├── models/                     # SQLAlchemy ORM models
│   ├── repositories/               # Ownership-scoped data access
│   ├── services/                   # Business logic and transactions
│   ├── db/
│   │   ├── migrations/versions/    # 8 Alembic migrations
│   │   ├── management.py
│   │   ├── demo_setup.py
│   │   └── MIGRATION_RECOVERY.md
│   └── main.py
├── data/
│   ├── actions/rules_v1.yaml       # Hazard action rules
│   └── crops/requirements_v1.yaml  # Crop requirement parameters
├── tests/                          # 420+ tests (pytest + Hypothesis)
├── .env.example
├── Dockerfile
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Known limitations

| Limitation | Status |
|---|---|
| Live Conduit endpoint | Historical fixture only; live credentials not configured |
| Live Open-Meteo / Sentinel-2 / SoilGrids / DEM | APIs are free/public but not polled in `demonstration` mode |
| OIDC multi-user authentication | Infrastructure complete; trust chain unverified |
| Flood probability | Explicitly unknown; requires drainage evidence not yet computed |
| Rate limiting | Not implemented; required before public release (Phase 11) |
| Mutation audit log | Not implemented; required before public release (Phase 11) |
| Yield forecasting | Not in scope; no validated crop growth model |

## References

- Requirements: `.kiro/specs/backend-foundation/requirements.md`
- Design: `.kiro/specs/backend-foundation/design.md`
- Master plan: `project_build.md`
- Build log (gate evidence): `docs/build-log.md`
- Data sources register: `docs/data-sources.md`
- API contract: `docs/api-contract.md`
- Dependency rationale: `DEPENDENCIES.md`
- Migration recovery: `app/db/MIGRATION_RECOVERY.md`

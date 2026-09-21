"""Mandatory readiness and bounded lifecycle tests.

Feature: backend-foundation
Property 4: Bounded probes
Property 12: Bounded service lifecycle
Requirements: 2.6, 3.1-3.6, 10.2-10.5, 14.2, 14.3
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_app_session_factory
from app.core.config import Settings
from app.core.database import EXPECTED_ALEMBIC_HEAD, check_readiness
from app.core.lifecycle import ServiceLifecycleState
from app.main import create_app
from app.models import Base
import app.main as main_module


def _settings() -> Settings:
    return Settings(
        environment="development",
        data_mode="demonstration",
        auth={"mode": "local_demo"},
        demo={"local_only": True, "isolated_database": True},
        database={"host": "localhost", "name": "test", "user": "test"},
        cors={"origins": []},
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ProbeSession:
    def __init__(
        self,
        *,
        failure: str | None = None,
        delay: float = 0.0,
        close_delay: float = 0.0,
    ):
        self.failure = failure
        self.delay = delay
        self.close_delay = close_delay
        self.closed = False
        self.invalidated = False
        self.rolled_back = False

    async def execute(self, statement):
        query = str(statement)
        if self.delay and self.failure == "slow":
            await asyncio.sleep(self.delay)
        if self.failure in {"database_down", "pool_exhausted"} and "SELECT 1" in query:
            raise RuntimeError("SECRET_CONNECTION_DETAIL")
        if "PostGIS_Version" in query:
            if self.failure == "postgis_error":
                raise RuntimeError("SECRET_EXTENSION_DETAIL")
            return _ScalarResult(None if self.failure == "missing_postgis" else "3.4")
        if "alembic_version" in query:
            return _ScalarResult(
                "unexpected_revision"
                if self.failure == "wrong_head"
                else EXPECTED_ALEMBIC_HEAD
            )
        if "installation_metadata" in query:
            return _ScalarResult(
                "AUTHENTICATED" if self.failure == "marker_mismatch" else "LOCAL_DEMO"
            )
        return _ScalarResult(1)

    async def rollback(self):
        self.rolled_back = True

    async def invalidate(self):
        self.invalidated = True

    async def close(self):
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        self.closed = True


class _ProbeFactory:
    def __init__(self, session: _ProbeSession):
        self.session = session

    def __call__(self):
        return self.session


@pytest.mark.asyncio
async def test_healthy_probe_checks_every_required_dependency():
    session = _ProbeSession()
    checks, ready = await check_readiness(_ProbeFactory(session), _settings())
    assert ready is True
    assert checks == {
        "database": "ok",
        "postgis": "ok",
        "migrations": "ok",
        "installation": "ok",
    }
    assert session.rolled_back and session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("database_down", ("database", "unavailable")),
        ("pool_exhausted", ("database", "unavailable")),
        ("missing_postgis", ("postgis", "missing")),
        ("postgis_error", ("postgis", "unavailable")),
        ("wrong_head", ("migrations", "incompatible")),
        ("marker_mismatch", ("installation", "marker_mismatch")),
    ],
)
async def test_probe_fails_closed_for_dependency_states(failure, expected):
    checks, ready = await check_readiness(
        _ProbeFactory(_ProbeSession(failure=failure)), _settings()
    )
    assert ready is False
    key, value = expected
    assert checks[key] == value
    assert "SECRET" not in json.dumps(checks)


@pytest.mark.asyncio
async def test_blackholed_or_slow_probe_includes_invalidation_under_five_seconds():
    session = _ProbeSession(failure="slow", delay=10.0)
    started = time.monotonic()
    checks, ready = await check_readiness(_ProbeFactory(session), _settings())
    elapsed = time.monotonic() - started
    assert ready is False
    assert checks["database"] == "timeout"
    assert elapsed < 5.0
    assert session.invalidated and session.closed


@pytest.mark.asyncio
async def test_cleanup_delay_remains_inside_five_second_probe_contract():
    session = _ProbeSession(close_delay=10.0)
    started = time.monotonic()
    checks, ready = await check_readiness(_ProbeFactory(session), _settings())
    elapsed = time.monotonic() - started
    assert ready is False
    assert checks["cleanup"] == "incomplete"
    assert elapsed < 5.0


async def _request(app, path: str):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_database_failure_does_not_fail_liveness_and_ready_is_sanitized():
    app = create_app(_settings())
    factory = _ProbeFactory(_ProbeSession(failure="database_down"))

    async def factory_override():
        return factory

    app.dependency_overrides[get_app_session_factory] = factory_override
    live = await _request(app, "/health")
    ready = await _request(app, "/ready")
    assert live.status_code == 200
    assert live.json()["status"] == "healthy"
    assert live.json()["non_live"] is True
    assert ready.status_code == 503
    assert ready.json()["status"] == "unavailable"
    assert ready.json()["checks"] == {"database": "unavailable"}
    assert "SECRET_CONNECTION_DETAIL" not in ready.text


@pytest.mark.asyncio
async def test_shutdown_marks_not_ready_drains_existing_and_rejects_new_traffic():
    app = create_app(_settings())
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.get("/in-flight")
    async def in_flight():
        entered.set()
        await release.wait()
        return {"completed": True}

    app.state.session_factory = _ProbeFactory(_ProbeSession())
    app.state.lifecycle.mark_ready()
    active = asyncio.create_task(_request(app, "/in-flight"))
    await entered.wait()
    app.state.lifecycle.begin_shutdown()

    readiness = await _request(app, "/ready")
    rejected = await _request(app, "/api/v1/farms")
    assert readiness.status_code == 503
    assert readiness.json()["checks"] == {"lifecycle": "shutting_down"}
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    release.set()
    assert (await active).status_code == 200
    await app.state.lifecycle.drain(1.0)
    assert app.state.lifecycle.active_requests == 0


@pytest.mark.asyncio
async def test_drain_cancels_request_after_budget():
    state = ServiceLifecycleState()
    entered = asyncio.Event()

    async def blocked():
        assert state.begin_request()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            state.finish_request()

    task = asyncio.create_task(blocked())
    await entered.wait()
    started = time.monotonic()
    await state.drain(0.05)
    assert time.monotonic() - started < 1.0
    assert task.cancelled()
    assert state.active_requests == 0


class _ResourceEngine:
    def __init__(self):
        self.disposed = False

    async def dispose(self):
        self.disposed = True


class _IdentityClient:
    def __init__(self):
        self.is_closed = False

    async def aclose(self):
        self.is_closed = True


@pytest.mark.asyncio
async def test_lifespan_reports_ready_only_after_checks_and_disposes_resources(
    monkeypatch,
):
    engine = _ResourceEngine()
    identity = _IdentityClient()
    factory = _ProbeFactory(_ProbeSession())

    monkeypatch.setattr(main_module, "create_engine", lambda settings: engine)
    monkeypatch.setattr(main_module, "create_session_factory", lambda value: factory)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda **kwargs: identity)

    async def ready_check(session_factory, settings):
        assert session_factory is factory
        return {"database": "ok"}, True

    monkeypatch.setattr(main_module, "check_readiness", ready_check)
    app = create_app(_settings())
    assert app.state.lifecycle.ready is False
    async with app.router.lifespan_context(app):
        assert app.state.lifecycle.ready is True
        assert app.state.engine is engine
        assert app.state.identity_client is identity
        assert app.state.session_factory is factory

    assert app.state.lifecycle.ready is False
    assert engine.disposed is True
    assert identity.is_closed is True
    assert app.state.engine is None
    assert app.state.identity_client is None
    assert app.state.session_factory is None


@pytest.mark.asyncio
async def test_lifespan_fails_startup_on_incompatible_database_and_cleans_up(
    monkeypatch,
):
    engine = _ResourceEngine()
    identity = _IdentityClient()
    monkeypatch.setattr(main_module, "create_engine", lambda settings: engine)
    monkeypatch.setattr(
        main_module, "create_session_factory", lambda value: _ProbeFactory(_ProbeSession())
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda **kwargs: identity)

    async def failed_check(session_factory, settings):
        return {"migrations": "incompatible"}, False

    monkeypatch.setattr(main_module, "check_readiness", failed_check)
    app = create_app(_settings())
    with pytest.raises(RuntimeError, match="Startup dependency verification failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("incompatible startup must not begin serving")

    assert app.state.lifecycle.ready is False
    assert engine.disposed is True
    assert identity.is_closed is True


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_real_sigterm_drains_in_flight_request_and_cleans_resources(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    env["FARMTWIN_LIFECYCLE_MARKER_DIR"] = str(tmp_path)
    process = subprocess.Popen(
        [
            str(Path(__file__).parents[1] / "venv/bin/python"),
            "-m",
            "uvicorn",
            "tests.lifecycle_signal_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-proxy-headers",
            "--timeout-graceful-shutdown",
            "20",
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            for _ in range(100):
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/docs")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.02)
            else:
                pytest.fail("test server did not start")

            in_flight = asyncio.create_task(
                client.get(f"http://127.0.0.1:{port}/slow")
            )
            for _ in range(100):
                if (tmp_path / "request-started").exists():
                    break
                await asyncio.sleep(0.01)
            assert (tmp_path / "request-started").exists()

            started = time.monotonic()
            process.send_signal(signal.SIGTERM)
            response = await in_flight
            assert response.status_code == 200
            assert response.json() == {"completed": True}

        await asyncio.to_thread(process.wait, 30)
        elapsed = time.monotonic() - started
        # Uvicorn 0.31 restores and re-raises the captured signal after its
        # graceful shutdown path, so POSIX reports the expected SIGTERM code.
        assert process.returncode in {0, -signal.SIGTERM}
        assert elapsed < 30.0
        cleanup = json.loads((tmp_path / "cleanup.json").read_text(encoding="utf-8"))
        assert cleanup == {
            "database_disposed": True,
            "identity_client_closed": True,
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.asyncio
async def test_committed_farm_remains_available_after_application_restart(monkeypatch):
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://farmtwin_test:test_password@localhost:5434/farmtwin_test",
    )
    assert "test" in database_url.lower()
    backend_dir = Path(__file__).parents[1]
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            # Reset every mapped table so newer migrations cannot leave schema
            # objects behind when this lifecycle test runs in the full suite.
            # Drop tables with circular FKs explicitly first using CASCADE,
            # then let SQLAlchemy drop the rest in dependency order.
            for statement in (
                "DROP TABLE IF EXISTS action_completions CASCADE",
                "DROP TABLE IF EXISTS analysis_snapshots CASCADE",
                "DROP TABLE IF EXISTS analysis_jobs CASCADE",
            ):
                await connection.execute(text(statement))
            await connection.run_sync(Base.metadata.drop_all)
            for statement in (
                "DROP TABLE IF EXISTS farm_geometry_revisions CASCADE",
                "DROP TABLE IF EXISTS farms CASCADE",
                "DROP TABLE IF EXISTS installation_metadata CASCADE",
                "DROP TABLE IF EXISTS users CASCADE",
                "DROP TABLE IF EXISTS alembic_version CASCADE",
                "DROP FUNCTION IF EXISTS update_timestamp() CASCADE",
                "DROP TYPE IF EXISTS installationmode CASCADE",
            ):
                await connection.execute(text(statement))
            await connection.execute(text("""
                DO $$
                BEGIN
                    CREATE ROLE farmtwin_runtime NOLOGIN;
                EXCEPTION
                    WHEN duplicate_object THEN NULL;
                END
                $$
            """))

        monkeypatch.setenv("DATABASE_URL", database_url)
        alembic = AlembicConfig(str(backend_dir / "alembic.ini"))
        alembic.set_main_option(
            "script_location", str(backend_dir / "app/db/migrations")
        )
        await asyncio.to_thread(command.upgrade, alembic, "head")

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO installation_metadata (id, mode) "
                    "VALUES (1, 'LOCAL_DEMO')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, issuer, subject) VALUES "
                    "('00000000-0000-0000-0000-000000000001', "
                    "'https://demo.farmtwin.local', 'demo-user')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO farms "
                    "(id, user_id, name, current_geometry_revision) VALUES "
                    "('30000000-0000-4000-8000-000000000001', "
                    "'00000000-0000-0000-0000-000000000001', "
                    "'Persistent Farm', 1)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO farm_geometry_revisions "
                    "(id, farm_id, revision, geometry, centroid, label_point, hectares) "
                    "VALUES ('40000000-0000-4000-8000-000000000001', "
                    "'30000000-0000-4000-8000-000000000001', 1, "
                    "ST_GeomFromText('POLYGON((36.8 -1.3,36.81 -1.3,"
                    "36.81 -1.29,36.8 -1.3))', 4326), "
                    "ST_GeomFromText('POINT(36.805 -1.295)', 4326), "
                    "ST_GeomFromText('POINT(36.805 -1.295)', 4326), 1.25)"
                )
            )

        factory = async_sessionmaker(engine, expire_on_commit=False)
        first_app = create_app(_settings())
        first_app.state.session_factory = factory
        first = await _request(first_app, "/api/v1/farms")
        assert first.status_code == 200
        assert [item["name"] for item in first.json()["items"]] == [
            "Persistent Farm"
        ]

        # A fresh application instance represents the post-termination start;
        # it receives a new pool/factory view of the same committed database.
        await engine.dispose()
        restarted_engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            restarted_app = create_app(_settings())
            restarted_app.state.session_factory = async_sessionmaker(
                restarted_engine, expire_on_commit=False
            )
            restarted = await _request(restarted_app, "/api/v1/farms")
            assert restarted.status_code == 200
            assert restarted.json()["total"] == 1
            assert restarted.json()["items"][0]["name"] == "Persistent Farm"
        finally:
            await restarted_engine.dispose()
    finally:
        await engine.dispose()

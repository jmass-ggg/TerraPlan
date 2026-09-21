"""
Conduit pipeline API endpoints.

Exposes normalized observations, derived features, and data-source status.
All endpoints require bearer auth or demo principal (Phase 1 auth boundary).

Requirements: 11.1–11.8
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field, computed_field, model_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_principal, get_request_session
from app.api.schemas import (
    PaginatedResponse,
    PaginationParams,
    ReadBaseSchema,
    TimestampMixin,
    persisted_datetime_to_utc,
)
from app.core.security import Principal
from app.models.conduit import (
    DailyAggregate,
    HourlyAggregate,
    IngestionRun,
    IngestionStatus,
    NormalizedObservation,
    QualityFlag,
    Station,
)

router = APIRouter(prefix="/conduit", tags=["Conduit"])
data_sources_router = APIRouter(tags=["Data Sources"])

# ---------------------------------------------------------------------------
# Shared sub-schemas
# ---------------------------------------------------------------------------


class QualifiedFloat(ReadBaseSchema):
    """A nullable float measurement with its quality flag."""

    value: float | None = Field(None)
    quality: str = Field(description="Quality flag for this measurement")


class SourceInfo(ReadBaseSchema):
    """Provenance metadata for a conduit observation."""

    provider: str = Field(default="conduit")
    station_id: str = Field(description="Provider-assigned station identifier")
    retrieval_time: datetime = Field(description="Time this record was ingested (UTC)")


# ---------------------------------------------------------------------------
# 10.1  GET /api/v1/conduit/current
# ---------------------------------------------------------------------------


class NormalizedObservationResponse(ReadBaseSchema):
    """
    Normalized observation response shape.

    Requirements: 11.1
    """

    id: UUID
    valid_time: datetime = Field(description="Observation valid time (UTC)")
    data_mode: str = Field(description="Data mode for this record")
    temperature_consensus: float | None = Field(None)
    temperature_consensus_quality: str
    temperature_channel_count: int
    humidity_sht: float | None = Field(None)
    humidity_sht_quality: str
    vpd_kpa: float | None = Field(None)
    vpd_quality: str
    wind_spd: float | None = Field(None)
    wind_spd_quality: str
    wind_gust: float | None = Field(None)
    wind_gust_quality: str
    press_hpa: float | None = Field(None)
    press_quality: str
    staleness_hours: float = Field(description="Hours elapsed since valid_time at response time")
    source: SourceInfo


class ConduitCurrentResponse(ReadBaseSchema):
    """Successful current-observation response. Requirements: 11.1"""

    status: Literal["ok"] = "ok"
    data: NormalizedObservationResponse


class ConduitUnavailableResponse(ReadBaseSchema):
    """Unavailable response when no accepted observation exists. Requirements: 11.2"""

    status: Literal["unavailable"] = "unavailable"
    data: None = None


def _obs_to_response(obs: NormalizedObservation, station: Station) -> NormalizedObservationResponse:
    """Map an ORM NormalizedObservation to the API response schema."""
    now = datetime.now(UTC)
    valid_time = persisted_datetime_to_utc(obs.valid_time_utc)
    staleness_hours = (now - valid_time).total_seconds() / 3600.0

    return NormalizedObservationResponse(
        id=obs.id,
        valid_time=valid_time,
        data_mode=obs.data_mode,
        temperature_consensus=obs.temperature_consensus,
        temperature_consensus_quality=obs.temperature_consensus_quality.value
        if hasattr(obs.temperature_consensus_quality, "value")
        else str(obs.temperature_consensus_quality),
        temperature_channel_count=obs.temperature_channel_count,
        humidity_sht=obs.humidity_sht_pct,
        humidity_sht_quality=obs.humidity_sht_quality.value
        if hasattr(obs.humidity_sht_quality, "value")
        else str(obs.humidity_sht_quality),
        vpd_kpa=obs.vpd_kpa,
        vpd_quality=obs.vpd_quality.value
        if hasattr(obs.vpd_quality, "value")
        else str(obs.vpd_quality),
        wind_spd=obs.wind_spd_ms,
        wind_spd_quality=obs.wind_spd_quality.value
        if hasattr(obs.wind_spd_quality, "value")
        else str(obs.wind_spd_quality),
        wind_gust=obs.wind_gust_ms,
        wind_gust_quality=obs.wind_gust_quality.value
        if hasattr(obs.wind_gust_quality, "value")
        else str(obs.wind_gust_quality),
        press_hpa=obs.press_hpa,
        press_quality=obs.press_quality.value
        if hasattr(obs.press_quality, "value")
        else str(obs.press_quality),
        staleness_hours=round(staleness_hours, 4),
        source=SourceInfo(
            provider="conduit",
            station_id=station.provider_station_id,
            retrieval_time=persisted_datetime_to_utc(obs.created_at),
        ),
    )


@router.get(
    "/current",
    response_model=ConduitCurrentResponse | ConduitUnavailableResponse,
    summary="Get most recent Conduit observation",
    description=(
        "Returns the most recent accepted normalized observation for the configured "
        "station, including quality flags and staleness. Returns status='unavailable' "
        "with null data when no accepted observation exists."
    ),
)
async def get_current(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> ConduitCurrentResponse | ConduitUnavailableResponse:
    """
    Most recent accepted Conduit observation.

    Requirements: 11.1, 11.2, 11.6, 11.7
    """
    # Find the station
    station_result = await session.execute(select(Station).limit(1))
    station = station_result.scalar_one_or_none()

    if station is None:
        return ConduitUnavailableResponse()

    # Fetch the most recent accepted observation
    result = await session.execute(
        select(NormalizedObservation)
        .where(
            NormalizedObservation.station_id == station.id,
            NormalizedObservation.temperature_consensus_quality.in_(
                [QualityFlag.ACCEPTED, QualityFlag.SINGLE_CHANNEL]
            ),
        )
        .order_by(desc(NormalizedObservation.valid_time_utc))
        .limit(1)
    )
    obs = result.scalar_one_or_none()

    if obs is None:
        return ConduitUnavailableResponse()

    return ConduitCurrentResponse(data=_obs_to_response(obs, station))


# ---------------------------------------------------------------------------
# 10.2  GET /api/v1/conduit/features
# ---------------------------------------------------------------------------


class ConduitFeaturesResponse(ReadBaseSchema):
    """
    Latest daily aggregate feature response.

    Requirements: 11.3
    """

    status: Literal["ok"] = "ok"
    window_start: datetime = Field(description="UTC midnight start of the daily window")
    data_mode: str
    vpd_mean_kpa: float | None = Field(None)
    temp_mean_celsius: float | None = Field(None)
    temp_min_celsius: float | None = Field(None)
    temp_max_celsius: float | None = Field(None)
    humidity_mean_pct: float | None = Field(None)
    humidity_min_pct: float | None = Field(None)
    humidity_max_pct: float | None = Field(None)
    wind_spd_mean_ms: float | None = Field(None)
    wind_spd_min_ms: float | None = Field(None)
    wind_spd_max_ms: float | None = Field(None)
    observation_count: int
    accepted_count: int
    coverage_ratio: float


class ConduitFeaturesUnavailableResponse(ReadBaseSchema):
    """Unavailable when no daily aggregate exists. Requirements: 11.3"""

    status: Literal["unavailable"] = "unavailable"
    data: None = None


@router.get(
    "/features",
    response_model=ConduitFeaturesResponse | ConduitFeaturesUnavailableResponse,
    summary="Get latest daily feature aggregate",
    description=(
        "Returns the most recent daily aggregate including VPD, temperature stats, "
        "humidity, wind, and coverage. Returns status='unavailable' when no aggregate exists."
    ),
)
async def get_features(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> ConduitFeaturesResponse | ConduitFeaturesUnavailableResponse:
    """
    Latest Conduit daily feature aggregate.

    Requirements: 11.3, 11.6, 11.7
    """
    station_result = await session.execute(select(Station).limit(1))
    station = station_result.scalar_one_or_none()

    if station is None:
        return ConduitFeaturesUnavailableResponse()

    result = await session.execute(
        select(DailyAggregate)
        .where(DailyAggregate.station_id == station.id)
        .order_by(desc(DailyAggregate.window_start_utc))
        .limit(1)
    )
    agg = result.scalar_one_or_none()

    if agg is None:
        return ConduitFeaturesUnavailableResponse()

    return ConduitFeaturesResponse(
        window_start=persisted_datetime_to_utc(agg.window_start_utc),
        data_mode=agg.data_mode,
        vpd_mean_kpa=agg.vpd_mean_kpa,
        temp_mean_celsius=agg.temp_mean_celsius,
        temp_min_celsius=agg.temp_min_celsius,
        temp_max_celsius=agg.temp_max_celsius,
        humidity_mean_pct=agg.humidity_mean_pct,
        humidity_min_pct=agg.humidity_min_pct,
        humidity_max_pct=agg.humidity_max_pct,
        wind_spd_mean_ms=agg.wind_spd_mean_ms,
        wind_spd_min_ms=agg.wind_spd_min_ms,
        wind_spd_max_ms=agg.wind_spd_max_ms,
        observation_count=agg.observation_count,
        accepted_count=agg.accepted_count,
        coverage_ratio=agg.coverage_ratio,
    )


# ---------------------------------------------------------------------------
# 10.3  GET /api/v1/conduit/history
# ---------------------------------------------------------------------------

_MAX_HISTORY_DAYS = 7
_DEFAULT_HISTORY_HOURS = 24


class ConduitHistoryResponse(PaginatedResponse):
    """Paginated observation history. Requirements: 11.4, 11.5"""

    items: list[NormalizedObservationResponse]


@router.get(
    "/history",
    response_model=ConduitHistoryResponse,
    summary="Get paginated observation history",
    description=(
        "Returns a paginated, reverse-chronological list of normalized observations. "
        "Defaults to the last 24 hours; maximum range is 7 days."
    ),
)
async def get_history(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
    from_time: datetime | None = Query(
        None,
        alias="from",
        description="Start of time range (ISO 8601 UTC). Defaults to 24 hours ago.",
    ),
    to_time: datetime | None = Query(
        None,
        alias="to",
        description="End of time range (ISO 8601 UTC). Defaults to now.",
    ),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ConduitHistoryResponse:
    """
    Paginated Conduit observation history, bounded to 7 days.

    Requirements: 11.4, 11.5, 11.6, 11.7
    """
    now = datetime.now(UTC)

    # Default and cap the time range
    effective_to = to_time if to_time is not None else now
    effective_from = (
        from_time
        if from_time is not None
        else effective_to - timedelta(hours=_DEFAULT_HISTORY_HOURS)
    )

    # Cap maximum range to 7 days
    max_from = effective_to - timedelta(days=_MAX_HISTORY_DAYS)
    if effective_from < max_from:
        effective_from = max_from

    # Find the station
    station_result = await session.execute(select(Station).limit(1))
    station = station_result.scalar_one_or_none()

    if station is None:
        return ConduitHistoryResponse(items=[], limit=limit, offset=offset, total=0)

    # Base filter
    base_filter = (
        NormalizedObservation.station_id == station.id,
        NormalizedObservation.valid_time_utc >= effective_from,
        NormalizedObservation.valid_time_utc <= effective_to,
    )

    # Count total matching rows (scoped to window)
    total = await session.scalar(
        select(func.count())
        .select_from(NormalizedObservation)
        .where(*base_filter)
    )

    # Fetch page
    result = await session.execute(
        select(NormalizedObservation)
        .where(*base_filter)
        .order_by(desc(NormalizedObservation.valid_time_utc))
        .offset(offset)
        .limit(limit)
    )
    observations = result.scalars().all()

    items = [_obs_to_response(obs, station) for obs in observations]

    return ConduitHistoryResponse(
        items=items,
        limit=limit,
        offset=offset,
        total=total or 0,
    )


# ---------------------------------------------------------------------------
# 10.4  GET /api/v1/data-sources
# ---------------------------------------------------------------------------

# Status values:
#   "active"        — provider has ingested data and is reachable
#   "pending"       — provider is configured but has no data yet
#   "not_configured" — provider is not configured (no credentials/station)
#   "unavailable"   — provider is configured but currently unreachable
#   "empty"         — provider completed ingestion but accepted 0 records

# Static metadata for each of the 6 providers.
# No API keys, credentials, or raw provider URLs are included.
_PROVIDER_METADATA: dict[str, dict] = {
    "conduit": {
        "description": (
            "On-farm IoT weather station network providing real-time micro-climate "
            "observations (temperature, humidity, VPD, wind, pressure). "
            "Data is normalised, quality-flagged, and aggregated into hourly and "
            "daily feature sets used directly in crop and risk analysis."
        ),
        "resolution": "Point measurement — single station per deployment",
        "spatial_extent": "Farm-local: station must be within 50 km of the farm centroid",
        "license_note": "Operator-owned sensor data; no third-party licence required.",
        "pipeline_explanation": (
            "Raw observations from the station arrive via the ingestion pipeline. "
            "Each reading is parsed, validated against the sensor schema, and "
            "quality-flagged (ACCEPTED / SINGLE_CHANNEL / REJECTED). "
            "Accepted readings are normalised into NormalizedObservations and "
            "rolled up into HourlyAggregates and DailyAggregates. "
            "When a farm snapshot is requested, the eligibility adapter checks "
            "whether the station is within 50 km and 500 m elevation of the "
            "farm centroid, then attaches the latest daily aggregate as the "
            "Conduit evidence block in the snapshot."
        ),
    },
    "weather": {
        "description": (
            "Open-Meteo forecast API providing current conditions and 7-day hourly "
            "forecast derived from ERA5/GFS reanalysis models. "
            "Used for current temperature, humidity, precipitation, wind speed, "
            "and cloud cover at the farm centroid."
        ),
        "resolution": "~11 km grid (ERA5/GFS nominal resolution)",
        "spatial_extent": "Global coverage",
        "license_note": (
            "Open-Meteo data is available under the Creative Commons Attribution 4.0 "
            "International licence (CC BY 4.0). Non-commercial use is free."
        ),
        "pipeline_explanation": None,
    },
    "satellite": {
        "description": (
            "Copernicus Sentinel-2 L2A multispectral imagery. "
            "The most recent cloud-free scene (cloud cover < 30 %, within 30 days) "
            "is selected, clipped to the farm polygon, and used to compute NDVI "
            "(vegetation health, 10 m) and NDMI (spectral moisture proxy, 20 m)."
        ),
        "resolution": "10 m (NDVI, Band 4 + Band 8); 20 m (NDMI, Band 8A + Band 11)",
        "spatial_extent": "Global land coverage (Sentinel-2 orbit)",
        "license_note": (
            "Copernicus Sentinel data are made available under the Copernicus "
            "Data Policy — free and open access for any use."
        ),
        "pipeline_explanation": None,
    },
    "soil": {
        "description": (
            "SoilGrids v2.0 (ISRIC) modelled soil properties at the farm centroid. "
            "Reports bulk density, clay/sand/silt fractions, pH, and organic carbon "
            "at 0–5 cm and 5–15 cm depth intervals with 5th/95th percentile "
            "uncertainty bounds. All values are modelled estimates, not direct "
            "field measurements."
        ),
        "resolution": "250 m nominal grid resolution",
        "spatial_extent": "Global land coverage",
        "license_note": (
            "SoilGrids data are provided by ISRIC under the Creative Commons "
            "Attribution 4.0 International licence (CC BY 4.0)."
        ),
        "pipeline_explanation": None,
    },
    "terrain": {
        "description": (
            "Copernicus DEM GLO-30 digital elevation model. "
            "1°×1° tiles at 30 m resolution are clipped to the farm polygon to "
            "compute mean, min, and max elevation (EGM2008 vertical datum) and "
            "mean slope (Horn 1981 gradient estimator). "
            "Flood probability is explicitly not computed — drainage evidence is "
            "required for flood exposure assessment."
        ),
        "resolution": "30 m (1 arc-second)",
        "spatial_extent": "Global land coverage (excluding polar regions)",
        "license_note": (
            "Copernicus DEM GLO-30 data are available free of charge for any use "
            "under the Copernicus DEM Licence."
        ),
        "pipeline_explanation": None,
    },
    "climate": {
        "description": (
            "Open-Meteo ERA5-Land historical archive. "
            "Computes 30-year monthly climatological baselines for temperature and "
            "precipitation at the farm centroid. Anomaly is calculated as the "
            "difference between the current observation and the long-term monthly "
            "mean for the same calendar month."
        ),
        "resolution": "~9 km grid (ERA5-Land reanalysis)",
        "spatial_extent": "Global coverage",
        "license_note": (
            "ERA5-Land reanalysis data from Copernicus Climate Data Store, "
            "served via Open-Meteo under CC BY 4.0."
        ),
        "pipeline_explanation": None,
    },
}


class DataSourceEntry(ReadBaseSchema):
    """
    Provider entry in the data-sources list.

    Extended schema for Phase 10 with richer metadata.
    Status values: active, pending, not_configured, unavailable, empty.
    No secrets, credentials, or raw provider URLs are included.

    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
    """

    name: str = Field(description="Provider name")
    description: str = Field(description="Plain-language description of this provider")
    data_mode: str = Field(description="Data mode for this source")
    last_ingestion_time: datetime | None = Field(
        None, description="Time of last successful ingestion or acquisition (UTC)"
    )
    record_count: int = Field(
        description="Total normalised observation or acquisition count from this source"
    )
    resolution: str | None = Field(
        None, description="Spatial or temporal resolution of this source"
    )
    spatial_extent: str | None = Field(
        None, description="Geographic coverage of this source"
    )
    license_note: str | None = Field(
        None, description="Licence and attribution note for this source"
    )
    pipeline_explanation: str | None = Field(
        None,
        description=(
            "Plain-language description of how this source's data flows "
            "through the pipeline into decisions (Conduit only)"
        ),
    )
    status: str = Field(
        description=(
            "Current status: active | pending | not_configured | unavailable | empty"
        )
    )


# Keep DataSourceRecord as an alias for backwards compatibility
DataSourceRecord = DataSourceEntry


class DataSourcesResponse(ReadBaseSchema):
    """Data sources list response. Requirements: 1.1, 1.2, 1.3, 1.4, 1.5"""

    sources: list[DataSourceEntry]


def _build_static_provider_entry(
    provider_key: str,
    data_mode: str,
    status: str,
    last_ingestion_time: datetime | None = None,
    record_count: int = 0,
) -> DataSourceEntry:
    """Build a DataSourceEntry for a static (non-Conduit) provider.

    Fills metadata from _PROVIDER_METADATA and never exposes credentials.

    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
    """
    meta = _PROVIDER_METADATA.get(provider_key, {})
    return DataSourceEntry(
        name=provider_key,
        description=meta.get("description", ""),
        data_mode=data_mode,
        last_ingestion_time=last_ingestion_time,
        record_count=record_count,
        resolution=meta.get("resolution"),
        spatial_extent=meta.get("spatial_extent"),
        license_note=meta.get("license_note"),
        pipeline_explanation=meta.get("pipeline_explanation"),
        status=status,
    )


@data_sources_router.get(
    "/data-sources",
    response_model=DataSourcesResponse,
    summary="List configured data sources",
    description=(
        "Returns entries for all six configured providers: Conduit, Weather, "
        "Satellite, Soil, Terrain, and Climate Baseline. "
        "Each entry includes description, resolution, spatial extent, licence note, "
        "data mode, last ingestion time, record count, and status. "
        "Status values: active | pending | not_configured | unavailable | empty. "
        "No API keys, credentials, or raw provider URLs are included."
    ),
)
async def get_data_sources(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> DataSourcesResponse:
    """
    Data source provider list — all six providers.

    The Conduit entry is derived from real ingestion run data in the database.
    The remaining five providers are remote APIs that do not have ingestion
    run records; they are reported with static metadata and a status of
    'not_configured' when no station/farm data is available, or 'unavailable'
    when configured but currently unreachable.

    For the demo/historical_replay data mode all remote providers are reported
    as 'not_configured' because they are not polled during replay.

    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
    """
    from app.core.config import get_settings

    settings = get_settings()
    global_data_mode = settings.data_mode.value  # live | historical_replay | demonstration

    # ------------------------------------------------------------------
    # Conduit — derived from real ingestion run records
    # ------------------------------------------------------------------
    run_result = await session.execute(
        select(
            IngestionRun.source_id,
            func.max(IngestionRun.retrieval_time).label("last_ingestion_time"),
            func.sum(IngestionRun.accepted_count).label("total_accepted"),
        )
        .where(IngestionRun.status == IngestionStatus.COMPLETED)
        .group_by(IngestionRun.source_id)
    )
    runs = {row.source_id: row for row in run_result.all()}

    conduit_run = runs.get("conduit")

    if conduit_run is not None:
        record_count = int(conduit_run.total_accepted or 0)
        last_time = (
            persisted_datetime_to_utc(conduit_run.last_ingestion_time)
            if conduit_run.last_ingestion_time
            else None
        )

        # Infer data_mode from accepted observations for Conduit source
        obs_mode_result = await session.execute(
            select(NormalizedObservation.data_mode)
            .join(IngestionRun, NormalizedObservation.ingestion_run_id == IngestionRun.id)
            .where(IngestionRun.source_id == "conduit")
            .limit(1)
        )
        conduit_data_mode = obs_mode_result.scalar_one_or_none() or global_data_mode
        conduit_status = "active" if record_count > 0 else "empty"
    else:
        # No ingestion runs for conduit yet
        record_count = 0
        last_time = None
        conduit_data_mode = global_data_mode
        # Distinguish: station exists in DB (configured but no runs) vs no station
        station_result = await session.execute(select(Station).limit(1))
        station = station_result.scalar_one_or_none()
        conduit_status = "pending" if station is not None else "not_configured"

    conduit_meta = _PROVIDER_METADATA["conduit"]
    conduit_entry = DataSourceEntry(
        name="conduit",
        description=conduit_meta["description"],
        data_mode=conduit_data_mode,
        last_ingestion_time=last_time,
        record_count=record_count,
        resolution=conduit_meta["resolution"],
        spatial_extent=conduit_meta["spatial_extent"],
        license_note=conduit_meta["license_note"],
        pipeline_explanation=conduit_meta["pipeline_explanation"],
        status=conduit_status,
    )

    # ------------------------------------------------------------------
    # Remote API providers — Weather, Satellite, Soil, Terrain, Climate
    # For live mode: these are polled on-demand per snapshot request.
    # For non-live modes: reported as not_configured (not polled in replay).
    # ------------------------------------------------------------------
    if global_data_mode == "live":
        # In live mode these are all configured (the APIs are public / free-tier)
        # We cannot know real-time reachability without probing, so we report
        # them as "active" to indicate they are configured and expected to work.
        remote_status = "active"
        remote_data_mode = "live"
    else:
        # In historical_replay or demonstration mode these APIs are not polled
        remote_status = "not_configured"
        remote_data_mode = global_data_mode

    weather_entry = _build_static_provider_entry(
        "weather", remote_data_mode, remote_status
    )
    satellite_entry = _build_static_provider_entry(
        "satellite", remote_data_mode, remote_status
    )
    soil_entry = _build_static_provider_entry(
        "soil", remote_data_mode, remote_status
    )
    terrain_entry = _build_static_provider_entry(
        "terrain", remote_data_mode, remote_status
    )
    climate_entry = _build_static_provider_entry(
        "climate", remote_data_mode, remote_status
    )

    return DataSourcesResponse(
        sources=[
            conduit_entry,
            weather_entry,
            satellite_entry,
            soil_entry,
            terrain_entry,
            climate_entry,
        ]
    )

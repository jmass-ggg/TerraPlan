"""
Conduit pipeline SQLAlchemy models.

Requirements: 1.2, 4.7, 5.1, 6.4
"""

import uuid
from enum import Enum as PyEnum

from sqlalchemy import (
    Enum,
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, TimestampMixin, UUIDMixin, UTCTimestamp


# ---------------------------------------------------------------------------
# Quality flag enum
# ---------------------------------------------------------------------------

class QualityFlag(str, PyEnum):
    """Per-field quality flag values.

    Requirements: 5.1
    """

    ACCEPTED = "accepted"
    SUSPECT = "suspect"
    MISSING = "missing"
    STALE = "stale"
    INVALID = "invalid"
    SINGLE_CHANNEL = "single_channel"
    UNCONFIRMED = "unconfirmed"


class IngestionStatus(str, PyEnum):
    """Status of an ingestion run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DUPLICATE_RUN = "duplicate_run"


# SQLAlchemy Enum type for QualityFlag — stored as VARCHAR
QualityFlagType = Enum(
    QualityFlag,
    native_enum=False,
    values_callable=lambda enum: [item.value for item in enum],
    length=20,
    name="qualityflag",
)

IngestionStatusType = Enum(
    IngestionStatus,
    native_enum=False,
    values_callable=lambda enum: [item.value for item in enum],
    length=20,
    name="ingestionstatus",
)


# ---------------------------------------------------------------------------
# Station
# ---------------------------------------------------------------------------

class Station(Base, UUIDMixin, TimestampMixin):
    """Named Conduit sensor location.

    Requirements: 1.2
    """

    __tablename__ = "stations"

    provider_station_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, default="conduit")

    observations: Mapped[list["NormalizedObservation"]] = relationship(
        "NormalizedObservation", back_populates="station"
    )
    hourly_aggregates: Mapped[list["HourlyAggregate"]] = relationship(
        "HourlyAggregate", back_populates="station"
    )
    daily_aggregates: Mapped[list["DailyAggregate"]] = relationship(
        "DailyAggregate", back_populates="station"
    )


# ---------------------------------------------------------------------------
# IngestionRun
# ---------------------------------------------------------------------------

class IngestionRun(Base, UUIDMixin, CreatedAtMixin):
    """Provenance record for each ingestion run.

    Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
    """

    __tablename__ = "ingestion_runs"

    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    retrieval_time: Mapped[UTCTimestamp]
    payload_size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    sha256_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    parser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    normalizer_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    accepted_count: Mapped[int | None] = mapped_column(nullable=True)
    rejected_count: Mapped[int | None] = mapped_column(nullable=True)
    duplicate_count: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[IngestionStatus] = mapped_column(
        IngestionStatusType, nullable=False, default=IngestionStatus.RUNNING
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    observations: Mapped[list["NormalizedObservation"]] = relationship(
        "NormalizedObservation", back_populates="ingestion_run"
    )


# ---------------------------------------------------------------------------
# NormalizedObservation
# ---------------------------------------------------------------------------

class NormalizedObservation(Base, UUIDMixin, CreatedAtMixin):
    """Normalized, quality-flagged observation row.

    Requirements: 1.2, 4.7, 5.1, 6.4
    """

    __tablename__ = "normalized_observations"

    station_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )

    valid_time_utc: Mapped[UTCTimestamp]
    data_mode: Mapped[str] = mapped_column(String(30), nullable=False)

    # --- Temperature channels ---
    temp_bmx_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_bmx_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    temp_mcp_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_mcp_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    temp_sht_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_sht_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    # --- Temperature consensus ---
    temperature_consensus: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_consensus_quality: Mapped[QualityFlag] = mapped_column(
        QualityFlagType, nullable=False
    )
    temperature_channel_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # --- Humidity ---
    humidity_sht_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_sht_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    # --- VPD ---
    vpd_kpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    vpd_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    # --- Wind ---
    wind_spd_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_spd_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    wind_gust_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_gust_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    wind_dir_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    # --- Pressure ---
    press_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    press_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    # --- Light / UV (uncalibrated raw counts) ---
    si1145_vis_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    si1145_vis_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    si1145_ir_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    si1145_ir_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    si1145_uv_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    si1145_uv_quality: Mapped[QualityFlag] = mapped_column(QualityFlagType, nullable=False)

    # --- Rainfall (unconfirmed semantics) — Requirements 4.7 ---
    rg1_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rg1_quality: Mapped[QualityFlag] = mapped_column(
        QualityFlagType, nullable=False, default=QualityFlag.UNCONFIRMED
    )

    rg2_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rg2_quality: Mapped[QualityFlag] = mapped_column(
        QualityFlagType, nullable=False, default=QualityFlag.UNCONFIRMED
    )

    rg1tt_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rg1tt_quality: Mapped[QualityFlag] = mapped_column(
        QualityFlagType, nullable=False, default=QualityFlag.UNCONFIRMED
    )

    rg2tt_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rg2tt_quality: Mapped[QualityFlag] = mapped_column(
        QualityFlagType, nullable=False, default=QualityFlag.UNCONFIRMED
    )

    rg1tp_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rg1tp_quality: Mapped[QualityFlag] = mapped_column(
        QualityFlagType, nullable=False, default=QualityFlag.UNCONFIRMED
    )

    rg2tp_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    rg2tp_quality: Mapped[QualityFlag] = mapped_column(
        QualityFlagType, nullable=False, default=QualityFlag.UNCONFIRMED
    )

    # Relationships
    station: Mapped["Station"] = relationship("Station", back_populates="observations")
    ingestion_run: Mapped["IngestionRun"] = relationship(
        "IngestionRun", back_populates="observations"
    )

    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "valid_time_utc",
            name="uq_observation_station_valid_time",
        ),
        Index("ix_normalized_observations_station_valid_time", "station_id", "valid_time_utc"),
        Index("ix_normalized_observations_ingestion_run", "ingestion_run_id"),
    )


# ---------------------------------------------------------------------------
# HourlyAggregate
# ---------------------------------------------------------------------------

class HourlyAggregate(Base, UUIDMixin, TimestampMixin):
    """Hourly aggregate of accepted observations.

    Requirements: 8.1, 8.3, 8.4, 8.5, 8.8
    """

    __tablename__ = "hourly_aggregates"

    station_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    window_start_utc: Mapped[UTCTimestamp]
    data_mode: Mapped[str] = mapped_column(String(30), nullable=False)

    # Temperature stats
    temp_mean_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_min_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_max_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Humidity stats
    humidity_mean_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_min_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_max_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Wind stats
    wind_spd_mean_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_spd_min_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_spd_max_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # VPD mean (from per-observation VPD, not recomputed)
    vpd_mean_kpa: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Coverage
    observation_count: Mapped[int] = mapped_column(nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Source observation IDs (JSONB array)
    source_observation_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )

    station: Mapped["Station"] = relationship("Station", back_populates="hourly_aggregates")

    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "window_start_utc",
            name="uq_hourly_aggregates_station_window",
        ),
        Index("ix_hourly_aggregates_station_window", "station_id", "window_start_utc"),
    )


# ---------------------------------------------------------------------------
# DailyAggregate
# ---------------------------------------------------------------------------

class DailyAggregate(Base, UUIDMixin, TimestampMixin):
    """Daily aggregate of accepted observations.

    Requirements: 8.2, 8.3, 8.4, 8.5, 8.8
    """

    __tablename__ = "daily_aggregates"

    station_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    window_start_utc: Mapped[UTCTimestamp]
    data_mode: Mapped[str] = mapped_column(String(30), nullable=False)

    # Temperature stats
    temp_mean_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_min_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_max_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Humidity stats
    humidity_mean_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_min_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_max_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Wind stats
    wind_spd_mean_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_spd_min_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_spd_max_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # VPD mean (from per-observation VPD, not recomputed)
    vpd_mean_kpa: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Coverage
    observation_count: Mapped[int] = mapped_column(nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Source observation IDs (JSONB array)
    source_observation_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )

    station: Mapped["Station"] = relationship("Station", back_populates="daily_aggregates")

    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "window_start_utc",
            name="uq_daily_aggregates_station_window",
        ),
        Index("ix_daily_aggregates_station_window", "station_id", "window_start_utc"),
    )

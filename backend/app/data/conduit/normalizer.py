"""
Conduit Normalizer and QC Engine.

Converts a ``ParsedObservation`` (output of the parser) into a
``NormalizedObservation`` ORM object ready for persistence.

Responsibilities:
- Map raw fixture fields to canonical column names and units (Requirements 4.1–4.7).
- Apply per-field quality rules (Requirements 5.1–5.9).
- Compute temperature consensus from accepted channels (Requirements 7.1–7.4).
- Compute VPD from accepted consensus temperature and humidity (Requirements 9.1–9.5).
- Unconditionally mark rainfall fields ``unconfirmed`` (Requirement 4.7).

The returned ``NormalizedObservation`` is NOT yet attached to a session or
persisted; the Ingestion_Service owns that step.

Normalizer version — increment when normalisation logic changes.
"""

from __future__ import annotations

import math
import statistics
import uuid
from datetime import datetime

from app.data.conduit.parser import ParsedObservation
from app.models.conduit import NormalizedObservation, QualityFlag, Station

NORMALIZER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# QC thresholds
# ---------------------------------------------------------------------------

# Temperature (Requirements 5.2)
_TEMP_MIN_C = -10.0
_TEMP_MAX_C = 60.0

# Temperature inter-sensor spread threshold (Requirements 5.6)
_TEMP_SPREAD_THRESHOLD_C = 3.0

# Humidity (Requirements 5.3)
_HUMIDITY_MIN_PCT = 0.0
_HUMIDITY_MAX_PCT = 100.0

# Wind direction (Requirements 5.5)
_WIND_DIR_MIN_DEG = 0.0
_WIND_DIR_MAX_DEG = 360.0


# ---------------------------------------------------------------------------
# VPD formula helper  (Requirements 9.1, 9.4)
# ---------------------------------------------------------------------------

def compute_vpd_kpa(temp_celsius: float, humidity_pct: float) -> float:
    """Compute VPD in kPa from temperature (°C) and relative humidity (%).

    Formula: VPD = 0.6108 × exp((17.27 × T) / (T + 237.3)) × (1 − RH / 100)
    Stored rounded to 4 decimal places.

    Requirements: 9.1, 9.4
    """
    es = 0.6108 * math.exp((17.27 * temp_celsius) / (temp_celsius + 237.3))
    vpd = es * (1.0 - humidity_pct / 100.0)
    return round(vpd, 4)


# ---------------------------------------------------------------------------
# Internal QC helpers
# ---------------------------------------------------------------------------

def _qc_temperature(value: float | None) -> QualityFlag:
    """Apply temperature range QC (Requirements 5.1, 5.2).

    Returns MISSING if value is None, SUSPECT if outside [-10, 60] °C,
    ACCEPTED otherwise.
    """
    if value is None:
        return QualityFlag.MISSING
    if value < _TEMP_MIN_C or value > _TEMP_MAX_C:
        return QualityFlag.SUSPECT
    return QualityFlag.ACCEPTED


def _qc_humidity(value: float | None) -> QualityFlag:
    """Apply humidity range QC (Requirements 5.1, 5.3).

    Returns MISSING if None, INVALID if outside [0, 100] (no clipping),
    ACCEPTED otherwise.
    """
    if value is None:
        return QualityFlag.MISSING
    if value < _HUMIDITY_MIN_PCT or value > _HUMIDITY_MAX_PCT:
        return QualityFlag.INVALID
    return QualityFlag.ACCEPTED


def _qc_wind_speed(value: float | None) -> QualityFlag:
    """Apply wind speed QC (Requirements 5.1, 5.4).

    Negative values are INVALID; None is MISSING; otherwise ACCEPTED.
    """
    if value is None:
        return QualityFlag.MISSING
    if value < 0.0:
        return QualityFlag.INVALID
    return QualityFlag.ACCEPTED


def _qc_wind_direction(value: float | None) -> QualityFlag:
    """Apply wind direction QC (Requirements 5.1, 5.5).

    Outside [0, 360] is INVALID; None is MISSING; otherwise ACCEPTED.
    """
    if value is None:
        return QualityFlag.MISSING
    if value < _WIND_DIR_MIN_DEG or value > _WIND_DIR_MAX_DEG:
        return QualityFlag.INVALID
    return QualityFlag.ACCEPTED


def _qc_generic(value: float | None) -> QualityFlag:
    """Generic QC: MISSING if None, ACCEPTED otherwise.

    Used for fields without domain-specific range checks (pressure,
    light/UV raw counts).
    """
    if value is None:
        return QualityFlag.MISSING
    return QualityFlag.ACCEPTED


# ---------------------------------------------------------------------------
# Temperature consensus  (Requirements 7.1–7.4)
# ---------------------------------------------------------------------------

def _compute_temperature_consensus(
    temp_bmx: float | None,
    temp_mcp: float | None,
    temp_sht: float | None,
    bmx_q: QualityFlag,
    mcp_q: QualityFlag,
    sht_q: QualityFlag,
) -> tuple[float | None, QualityFlag, int]:
    """Compute consensus temperature and its quality flag.

    Returns ``(consensus_value, consensus_quality, channel_count)``.

    - 0 accepted channels → (None, MISSING, 0)  [Requirement 7.3]
    - 1 accepted channel  → (value, SINGLE_CHANNEL, 1)  [Requirement 7.2]
    - 2–3 accepted channels → (median, ACCEPTED, n)  [Requirement 7.1]

    Only channels with quality == ACCEPTED contribute.
    """
    accepted_values: list[float] = []
    for value, flag in [
        (temp_bmx, bmx_q),
        (temp_mcp, mcp_q),
        (temp_sht, sht_q),
    ]:
        if flag == QualityFlag.ACCEPTED and value is not None:
            accepted_values.append(value)

    count = len(accepted_values)
    if count == 0:
        return None, QualityFlag.MISSING, 0
    if count == 1:
        return accepted_values[0], QualityFlag.SINGLE_CHANNEL, 1

    # 2 or 3 channels: use median
    return statistics.median(accepted_values), QualityFlag.ACCEPTED, count


# ---------------------------------------------------------------------------
# Inter-sensor spread check  (Requirements 5.6)
# ---------------------------------------------------------------------------

def _apply_spread_check(
    temp_bmx: float | None,
    temp_mcp: float | None,
    temp_sht: float | None,
    bmx_q: QualityFlag,
    mcp_q: QualityFlag,
    sht_q: QualityFlag,
) -> tuple[QualityFlag, QualityFlag, QualityFlag]:
    """Flag the highest-deviation channel as SUSPECT if spread > threshold.

    Only triggered when all three channels are ACCEPTED (Requirement 5.6).
    Returns updated (bmx_q, mcp_q, sht_q).
    """
    all_accepted = (
        bmx_q == QualityFlag.ACCEPTED
        and mcp_q == QualityFlag.ACCEPTED
        and sht_q == QualityFlag.ACCEPTED
        and temp_bmx is not None
        and temp_mcp is not None
        and temp_sht is not None
    )
    if not all_accepted:
        return bmx_q, mcp_q, sht_q

    values = [temp_bmx, temp_mcp, temp_sht]
    spread = max(values) - min(values)
    if spread <= _TEMP_SPREAD_THRESHOLD_C:
        return bmx_q, mcp_q, sht_q

    mean = sum(values) / len(values)
    deviations = [abs(v - mean) for v in values]
    max_idx = deviations.index(max(deviations))

    flags = [bmx_q, mcp_q, sht_q]
    flags[max_idx] = QualityFlag.SUSPECT
    return flags[0], flags[1], flags[2]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_and_qc(
    obs: ParsedObservation,
    station: Station,
    ingestion_run_id: uuid.UUID,
    data_mode: str = "historical_replay",
) -> NormalizedObservation:
    """Convert a ``ParsedObservation`` into a ``NormalizedObservation`` ORM object.

    The returned object is detached (not added to any session).

    Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 5.1–5.9, 7.1–7.4, 9.1–9.5
    """
    f = obs.fields  # shorthand

    # ------------------------------------------------------------------
    # 1. Temperature channels — Requirements 4.1, 5.2
    # ------------------------------------------------------------------
    temp_bmx = f.get("temp_bmx")
    temp_mcp = f.get("temp_mcp")
    temp_sht = f.get("temp_sht")

    bmx_q = _qc_temperature(temp_bmx)
    mcp_q = _qc_temperature(temp_mcp)
    sht_temp_q = _qc_temperature(temp_sht)  # temp channel QC for temp_sht

    # Inter-sensor spread check — Requirements 5.6
    bmx_q, mcp_q, sht_temp_q = _apply_spread_check(
        temp_bmx, temp_mcp, temp_sht, bmx_q, mcp_q, sht_temp_q
    )

    # ------------------------------------------------------------------
    # 2. Temperature consensus — Requirements 7.1–7.4
    # ------------------------------------------------------------------
    consensus, consensus_q, channel_count = _compute_temperature_consensus(
        temp_bmx, temp_mcp, temp_sht, bmx_q, mcp_q, sht_temp_q
    )

    # ------------------------------------------------------------------
    # 3. Humidity — Requirements 4.2, 5.3
    # ------------------------------------------------------------------
    humidity_sht = f.get("humidity_sht")
    humidity_q = _qc_humidity(humidity_sht)

    # ------------------------------------------------------------------
    # 4. VPD — Requirements 9.1–9.5
    # ------------------------------------------------------------------
    vpd_accepted = (
        consensus_q in (QualityFlag.ACCEPTED, QualityFlag.SINGLE_CHANNEL)
        and consensus is not None
        and humidity_q == QualityFlag.ACCEPTED
        and humidity_sht is not None
    )
    if vpd_accepted:
        vpd_kpa: float | None = compute_vpd_kpa(consensus, humidity_sht)
        vpd_q = QualityFlag.ACCEPTED
    else:
        vpd_kpa = None
        vpd_q = QualityFlag.MISSING

    # ------------------------------------------------------------------
    # 5. Wind — Requirements 4.4, 4.5, 5.4, 5.5
    # ------------------------------------------------------------------
    wind_spd = f.get("wind_spd")
    wind_gust = f.get("wind_gust")
    wind_dir = f.get("wind_dir")

    wind_spd_q = _qc_wind_speed(wind_spd)
    wind_gust_q = _qc_wind_speed(wind_gust)  # same rules as wind speed
    wind_dir_q = _qc_wind_direction(wind_dir)

    # ------------------------------------------------------------------
    # 6. Pressure — Requirements 4.3
    # ------------------------------------------------------------------
    press_hpa = f.get("press_bmx")
    press_q = _qc_generic(press_hpa)

    # ------------------------------------------------------------------
    # 7. Light / UV (uncalibrated raw counts) — Requirements 4.6
    # ------------------------------------------------------------------
    si1145_vis = f.get("si1145_vis")
    si1145_ir = f.get("si1145_ir")
    si1145_uv = f.get("si1145_uv")

    vis_q = _qc_generic(si1145_vis)
    ir_q = _qc_generic(si1145_ir)
    uv_q = _qc_generic(si1145_uv)

    # ------------------------------------------------------------------
    # 8. Rainfall — Requirements 4.7 (always UNCONFIRMED)
    # ------------------------------------------------------------------
    rg1 = f.get("rg1")
    rg2 = f.get("rg2")
    rg1tt = f.get("rg1tt")
    rg2tt = f.get("rg2tt")
    rg1tp = f.get("rg1tp")
    rg2tp = f.get("rg2tp")

    # ------------------------------------------------------------------
    # 9. Assemble NormalizedObservation
    # ------------------------------------------------------------------
    return NormalizedObservation(
        station_id=station.id,
        ingestion_run_id=ingestion_run_id,
        valid_time_utc=obs.ts,
        data_mode=data_mode,
        # Temperature channels
        temp_bmx_celsius=temp_bmx,
        temp_bmx_quality=bmx_q,
        temp_mcp_celsius=temp_mcp,
        temp_mcp_quality=mcp_q,
        temp_sht_celsius=temp_sht,
        temp_sht_quality=sht_temp_q,
        # Consensus
        temperature_consensus=consensus,
        temperature_consensus_quality=consensus_q,
        temperature_channel_count=channel_count,
        # Humidity
        humidity_sht_pct=humidity_sht,
        humidity_sht_quality=humidity_q,
        # VPD
        vpd_kpa=vpd_kpa,
        vpd_quality=vpd_q,
        # Wind
        wind_spd_ms=wind_spd,
        wind_spd_quality=wind_spd_q,
        wind_gust_ms=wind_gust,
        wind_gust_quality=wind_gust_q,
        wind_dir_deg=wind_dir,
        wind_dir_quality=wind_dir_q,
        # Pressure
        press_hpa=press_hpa,
        press_quality=press_q,
        # Light / UV
        si1145_vis_raw=si1145_vis,
        si1145_vis_quality=vis_q,
        si1145_ir_raw=si1145_ir,
        si1145_ir_quality=ir_q,
        si1145_uv_raw=si1145_uv,
        si1145_uv_quality=uv_q,
        # Rainfall (always unconfirmed)
        rg1_raw=rg1,
        rg1_quality=QualityFlag.UNCONFIRMED,
        rg2_raw=rg2,
        rg2_quality=QualityFlag.UNCONFIRMED,
        rg1tt_raw=rg1tt,
        rg1tt_quality=QualityFlag.UNCONFIRMED,
        rg2tt_raw=rg2tt,
        rg2tt_quality=QualityFlag.UNCONFIRMED,
        rg1tp_raw=rg1tp,
        rg1tp_quality=QualityFlag.UNCONFIRMED,
        rg2tp_raw=rg2tp,
        rg2tp_quality=QualityFlag.UNCONFIRMED,
    )

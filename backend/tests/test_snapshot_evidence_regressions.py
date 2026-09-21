from datetime import date
from types import SimpleNamespace

import pytest

from app.domain.snapshot_context import seasonal_values, context_from_snapshot
from app.data.providers.climate import _compute_monthly_means


def test_monthly_totals_do_not_require_version_and_require_complete_season():
    climate = {'all_monthly_means': {'temperature_2m_mean': {'10': 20, '11': 22},
                                    'precipitation_sum': {'10': 62, '11': 30}}}
    temperature, rain = seasonal_values(climate, date(2026, 10, 1), 2)
    assert rain == pytest.approx(92)
    assert temperature == pytest.approx((20 * 31 + 22 * 30) / 61)
    del climate['all_monthly_means']['precipitation_sum']['11']
    assert seasonal_values(climate, date(2026, 10, 1), 2)[1] is None


def test_daily_archive_rainfall_is_accumulated_to_monthly_total():
    dates = [f'2026-10-{day:02}' for day in range(1, 32)]
    assert _compute_monthly_means(dates, [2.0] * 31, accumulation=True)[10] == 62


def snapshot_with_weather(humidity=50, quality='accepted', mode='live', conduit=None):
    def env(value):
        return {'value': value, 'quality': quality, 'data_mode': mode, 'source': 'open-meteo'}
    return SimpleNamespace(id='test', data_mode='live', climate_baseline=None, soil=None,
                          satellite=None, terrain=None, conduit=conduit,
                          weather={'fields': {'temperature_2m': env(25), 'relative_humidity_2m': env(humidity)}})


def test_weather_vpd_fallback_and_conduit_priority():
    result = context_from_snapshot(snapshot_with_weather(), 10, 4)
    assert result.vpd_kpa == pytest.approx(1.5839, abs=0.001)
    assert 'vpd_kpa' in result.real_input_fields
    result = context_from_snapshot(snapshot_with_weather(conduit={'vpd_mean_kpa': {'value': 0.8, 'quality': 'accepted'}}), 10, 4)
    assert result.vpd_kpa == 0.8


@pytest.mark.parametrize('humidity,quality,mode', [(0,'accepted','live'), (100,'accepted','live'), (50,'unavailable','live'), (50,'accepted','demonstration')])
def test_vpd_rejects_unsupported_weather(humidity, quality, mode):
    assert context_from_snapshot(snapshot_with_weather(humidity, quality, mode), 10, 4).vpd_kpa is None


@pytest.mark.asyncio
async def test_satellite_search_error_is_not_reported_as_no_scene(monkeypatch):
    import httpx
    from shapely.geometry import box
    from app.data.providers import satellite
    async def rejected(self, url, **kwargs):
        return httpx.Response(400, request=httpx.Request('POST', url), json={'detail': 'invalid collection'})
    monkeypatch.setattr(httpx.AsyncClient, 'post', rejected)
    result = await satellite.fetch(box(36.82, -1.22, 36.83, -1.21))
    assert result.evidence_status == 'unavailable'
    assert result.error_message == 'STAC search returned HTTP 400'
    assert result.payload['unavailability_reason'] == result.error_message

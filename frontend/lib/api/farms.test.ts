import { describe, expect, it } from 'vitest';

import { normaliseFarmTwin, type FarmTwinResult } from './farms';

const envelope = {
  value: 21.5,
  unit: '°C',
  source: 'open-meteo',
  acquired_at: '2026-09-09T00:00:00Z',
  retrieved_at: '2026-09-09T00:01:00Z',
  data_mode: 'live' as const,
  quality: 'accepted',
  resolution_m: 1000,
};

describe('normaliseFarmTwin', () => {
  it('maps provider payloads into the UI contract', () => {
    const raw = {
      status: 'ready',
      weather: {
        fields: { temperature_2m: envelope },
        model_metadata: { source: 'open-meteo', forecast_horizon_days: 7 },
      },
      soil: {
        depths: {
          '0_5cm': {
            phh2o: { mean: { ...envelope, value: 6.4, unit: 'pH' } },
          },
        },
        source_resolution_m: 250,
        smaller_than_grid_cell: true,
        modelled_estimate: true,
      },
      terrain: {
        mean_elevation_m: { ...envelope, value: 1521, unit: 'metres' },
        dem_resolution_m: 30,
      },
    } as unknown as FarmTwinResult;

    const result = normaliseFarmTwin(raw);
    expect(result.weather?.temperature_2m?.value).toBe(21.5);
    expect(result.weather?.forecast_horizon_hours).toBe(168);
    expect(result.soil?.depth_0_5cm?.phh2o?.value).toBe(6.4);
    expect(result.soil?.small_farm_flag).toBe(true);
    expect(result.terrain?.resolution_m).toBe(30);
  });
});

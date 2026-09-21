import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ClimateOverview } from './ClimateOverview';
import type { ClimateBaselinePayload, EnvironmentalValue, WeatherPayload } from '@/lib/api/farms';

function environmentalValue(value: number, unit: string): EnvironmentalValue {
  return {
    value,
    unit,
    source: 'open-meteo',
    acquired_at: '2026-09-18T06:00:00Z',
    retrieved_at: '2026-09-18T06:05:00Z',
    data_mode: 'live',
    quality: 'accepted',
    resolution_m: 11_000,
  };
}

function makeWeather(): WeatherPayload {
  return {
    temperature_2m: environmentalValue(24, '°C'),
    precipitation: environmentalValue(0.2, 'mm'),
    relative_humidity_2m: environmentalValue(72, '%'),
    wind_speed_10m: environmentalValue(3.8, 'm/s'),
    wind_direction_10m: environmentalValue(180, '°'),
    cloud_cover: environmentalValue(36, '%'),
    weather_code: environmentalValue(2, 'wmo code'),
    model_name: 'open-meteo',
    valid_time: '2026-09-18T06:00:00Z',
    forecast_horizon_hours: 168,
    daily: {
      time: ['2026-09-18', '2026-09-19', '2026-09-20', '2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24'],
      temperature_2m_min: [16, 17, 17, 16, 18, 17, 16],
      temperature_2m_max: [27, 28, 26, 24, 26, 27, 26],
      temperature_2m_mean: [21, 22, 21, 20, 22, 22, 21],
      precipitation_sum: [0, 0.4, 8.2, 22, 1.1, 0, 0],
      wind_speed_10m_max: [5, 6, 7, 8, 6, 5, 5],
      weather_code: [1, 2, 61, 95, 80, 1, 2],
    },
  };
}

function makeClimateBaseline(): ClimateBaselinePayload {
  const values = (items: number[]) => Object.fromEntries(items.map((value, index) => [String(index + 1), value]));
  return {
    baseline_source: 'ERA5 reanalysis via Open-Meteo historical archive',
    baseline_period: '1996–2025',
    temperature_anomaly: null,
    rainfall_anomaly: null,
    all_monthly_means: {
      temperature_2m_mean: values([18, 19, 20, 21, 20, 19, 18, 18, 19, 20, 20, 19]),
      precipitation_sum: values([25, 32, 68, 112, 88, 44, 31, 37, 49, 91, 105, 42]),
    },
  };
}

describe('ClimateOverview', () => {
  it('shows all dashboard sections together', () => {
    render(<ClimateOverview weather={makeWeather()} climateBaseline={makeClimateBaseline()} />);

    const navigation = screen.getByRole('navigation', { name: 'Climate sections' });
    expect(within(navigation).getByRole('link', { name: 'Today' })).toHaveAttribute('href', '#climate-today');
    expect(within(navigation).getByRole('link', { name: '7 Days' })).toHaveAttribute('href', '#climate-7-days');
    expect(screen.getByRole('heading', { name: 'Next 7 Days' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Monthly Climate' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Annual Climate' })).toBeInTheDocument();
  });

  it('renders current provider values, WMO condition, and provenance', async () => {
    const user = userEvent.setup();
    render(<ClimateOverview weather={makeWeather()} climateBaseline={null} />);

    expect(screen.getByRole('heading', { name: '24°C' })).toBeInTheDocument();
    expect(screen.getByText('Partly Cloudy')).toBeInTheDocument();
    expect(screen.getByText('72 %')).toBeInTheDocument();
    expect(screen.getByText('3.8 m/s')).toBeInTheDocument();

    await user.click(screen.getByText('Data details'));
    expect(screen.getAllByText('Open-Meteo').length).toBeGreaterThan(0);
    expect(screen.getByText('168 h')).toBeInTheDocument();
  });

  it('renders seven forecast cards from daily provider data', () => {
    const { container } = render(<ClimateOverview weather={makeWeather()} climateBaseline={null} />);

    expect(container.querySelectorAll('.climate-forecast-card')).toHaveLength(7);
    expect(screen.getAllByText('27°')).toHaveLength(2);
    expect(screen.getByText('22 mm rain')).toBeInTheDocument();
  });

  it('switches the monthly chart between rainfall and temperature', async () => {
    const user = userEvent.setup();
    render(<ClimateOverview weather={null} climateBaseline={makeClimateBaseline()} />);

    const rainfall = screen.getByRole('button', { name: 'Rainfall' });
    const temperature = screen.getByRole('button', { name: 'Temperature' });
    expect(rainfall).toHaveAttribute('aria-pressed', 'true');
    await user.click(temperature);
    expect(temperature).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Monthly temperature chart')).toBeInTheDocument();
  });

  it('derives annual conditions from real monthly rainfall and links to the farm plan', () => {
    render(
      <ClimateOverview
        weather={null}
        climateBaseline={makeClimateBaseline()}
        farmId="farm-123"
      />,
    );

    expect(screen.getAllByText('Dry').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Wet').length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: /View Annual Crop Plan/ })).toHaveAttribute(
      'href',
      '/app/farms/farm-123/annual-plan',
    );
  });

  it('shows explicit unavailable states without leaking invalid values', () => {
    const { container } = render(<ClimateOverview weather={null} climateBaseline={null} />);

    expect(screen.getByText('Conditions unavailable')).toBeInTheDocument();
    expect(screen.getByText('Seven-day forecast data unavailable')).toBeInTheDocument();
    expect(screen.getByText('Monthly climate data unavailable')).toBeInTheDocument();
    expect(screen.getByText('Annual climate data unavailable')).toBeInTheDocument();
    expect(container.textContent).not.toContain('undefined');
    expect(container.textContent).not.toContain('NaN');
  });
});

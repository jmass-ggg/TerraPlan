'use client';

import type { LucideIcon } from 'lucide-react';
import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSun,
  Droplets,
  ExternalLink,
  Info,
  Snowflake,
  Sun,
  ThermometerSun,
  Wind,
} from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from 'recharts';

import type {
  ClimateBaselinePayload,
  EnvironmentalValue,
  SatellitePayload,
  WeatherDailyForecast,
  WeatherPayload,
} from '@/lib/api/farms';
import { useI18n } from '@/lib/i18n/context';

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

type WeatherSeverity = 'normal' | 'rain' | 'warning';

interface WeatherCondition {
  label: string;
  icon: LucideIcon;
  severity: WeatherSeverity;
}

function weatherCondition(code: number | null | undefined): WeatherCondition {
  if (code === 0) return { label: 'Clear', icon: Sun, severity: 'normal' };
  if (code === 1) return { label: 'Mainly Clear', icon: CloudSun, severity: 'normal' };
  if (code === 2) return { label: 'Partly Cloudy', icon: CloudSun, severity: 'normal' };
  if (code === 3) return { label: 'Overcast', icon: Cloud, severity: 'normal' };
  if (code === 45 || code === 48) return { label: 'Fog', icon: CloudFog, severity: 'normal' };
  if ([51, 53, 55].includes(code ?? -1)) return { label: 'Drizzle', icon: CloudDrizzle, severity: 'rain' };
  if ([56, 57].includes(code ?? -1)) return { label: 'Freezing Drizzle', icon: CloudDrizzle, severity: 'warning' };
  if ([61, 63, 65].includes(code ?? -1)) return { label: 'Rain', icon: CloudRain, severity: 'rain' };
  if ([66, 67].includes(code ?? -1)) return { label: 'Freezing Rain', icon: CloudRain, severity: 'warning' };
  if ([71, 73, 75, 77, 85, 86].includes(code ?? -1)) return { label: code === 77 ? 'Snow Grains' : 'Snow', icon: Snowflake, severity: 'warning' };
  if ([80, 81, 82].includes(code ?? -1)) return { label: 'Rain Showers', icon: CloudRain, severity: 'rain' };
  if (code === 95) return { label: 'Thunderstorm', icon: CloudLightning, severity: 'warning' };
  if (code === 96 || code === 99) return { label: 'Thunderstorm with Hail', icon: CloudLightning, severity: 'warning' };
  return { label: 'Conditions unavailable', icon: Cloud, severity: 'normal' };
}

function precipitationSeverity(amount: number | null | undefined): WeatherSeverity {
  if (amount === null || amount === undefined) return 'normal';
  if (amount >= 20) return 'warning';
  if (amount >= 0.5) return 'rain';
  return 'normal';
}

/** Interface indicator only; it is not a medical heat-index assessment. */
function heatStress(temperature: number | null, humidity: number | null): 'Low' | 'Moderate' | 'High' | '—' {
  if (temperature === null || humidity === null) return '—';
  if (temperature >= 32 || (temperature >= 29 && humidity >= 70)) return 'High';
  if (temperature >= 27 || (temperature >= 25 && humidity >= 80)) return 'Moderate';
  return 'Low';
}

function valueOf(value: EnvironmentalValue | null | undefined): number | null {
  return value?.value !== null && value?.value !== undefined ? value.value : null;
}

function formatNumber(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? '—'
    : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function sourceLabel(source: string | undefined): string {
  if (!source) return 'Provider unavailable';
  return source.toLowerCase().includes('open-meteo') ? 'Open-Meteo' : source;
}

function anomalyLabel(value: EnvironmentalValue | null | undefined, threshold: number): string {
  const amount = valueOf(value);
  if (amount === null) return 'Unavailable';
  if (amount > threshold) return 'Above Normal';
  if (amount < -threshold) return 'Below Normal';
  return 'Normal';
}

function forecastRows(daily: WeatherDailyForecast | undefined) {
  if (!daily?.time.length) return [];
  return daily.time.slice(0, 7).map((date, index) => ({
    date,
    min: daily.temperature_2m_min[index] ?? null,
    max: daily.temperature_2m_max[index] ?? null,
    precipitation: daily.precipitation_sum[index] ?? null,
    code: daily.weather_code[index] ?? null,
  }));
}

function CurrentWeather({ weather }: { weather: WeatherPayload | null | undefined }) {
  const { t } = useI18n();
  const temperature = valueOf(weather?.temperature_2m);
  const humidity = valueOf(weather?.relative_humidity_2m);
  const precipitation = valueOf(weather?.precipitation);
  const wind = valueOf(weather?.wind_speed_10m);
  const condition = weatherCondition(valueOf(weather?.weather_code));
  const WeatherIcon = condition.icon;
  const provenance = weather?.temperature_2m ?? weather?.relative_humidity_2m ?? weather?.precipitation;
  const stress = heatStress(temperature, humidity);

  return (
    <section id="climate-today" className="climate-dashboard-section" aria-labelledby="climate-current-title">
      <article className="climate-current-card">
        <div className="climate-current-primary">
          <span className="climate-current-icon" data-severity={condition.severity}><WeatherIcon aria-hidden="true" /></span>
          <div>
            <h2 id="climate-current-title">{formatNumber(temperature)}{temperature !== null ? '°C' : ''}</h2>
            <p>{condition.label}</p>
          </div>
          <div className="climate-current-source">
            <span>{t('climate.live')} · {sourceLabel(provenance?.source ?? weather?.model_name)}</span>
            <small>{t('climate.updated', { date: formatDate(provenance?.retrieved_at ?? weather?.valid_time) })}</small>
          </div>
        </div>

        <div className="climate-current-metrics">
          <div><Droplets aria-hidden="true" /><span><small>{t('twin.humidity')}</small><strong>{formatNumber(humidity, 0)}{humidity !== null ? ' %' : ''}</strong></span></div>
          <div><CloudRain aria-hidden="true" /><span><small>{t('climate.rain')}</small><strong>{formatNumber(precipitation)}{precipitation !== null ? ' mm' : ''}</strong></span></div>
          <div><Wind aria-hidden="true" /><span><small>{t('climate.wind')}</small><strong>{formatNumber(wind)}{wind !== null ? ' m/s' : ''}</strong></span></div>
          <div data-stress={stress.toLowerCase()}><ThermometerSun aria-hidden="true" /><span><small>{t('climate.heatStress')}</small><strong>{stress}</strong></span></div>
        </div>

        <details className="climate-data-details">
          <summary><Info aria-hidden="true" /> {t('climate.dataDetails')}</summary>
          <dl>
            <div><dt>{t('climate.source')}</dt><dd>{sourceLabel(provenance?.source ?? weather?.model_name)}</dd></div>
            <div><dt>{t('climate.acquired')}</dt><dd>{formatDate(provenance?.acquired_at ?? weather?.valid_time)}</dd></div>
            <div><dt>{t('climate.retrieved')}</dt><dd>{formatDate(provenance?.retrieved_at)}</dd></div>
            <div><dt>{t('climate.quality')}</dt><dd>{provenance?.quality ?? t('common.unavailable')}</dd></div>
            <div><dt>{t('climate.mode')}</dt><dd>{provenance?.data_mode ?? t('common.unavailable')}</dd></div>
            <div><dt>{t('climate.forecastHorizon')}</dt><dd>{weather?.forecast_horizon_hours != null ? `${weather.forecast_horizon_hours} h` : t('common.unavailable')}</dd></div>
          </dl>
        </details>
      </article>
    </section>
  );
}

function SevenDayForecast({ weather }: { weather: WeatherPayload | null | undefined }) {
  const { t } = useI18n();
  const rows = forecastRows(weather?.daily);
  return (
    <section id="climate-7-days" className="climate-dashboard-section" aria-labelledby="climate-forecast-title">
      <h2 id="climate-forecast-title" className="climate-section-title">{t('climate.next7')}</h2>
      {rows.length === 0 ? (
        <div className="climate-section-empty">{t('climate.forecastUnavailable')}</div>
      ) : (
        <div className="climate-forecast-row">
          {rows.map((day) => {
            const rainSeverity = precipitationSeverity(day.precipitation);
            const condition = weatherCondition(day.code);
            const Icon = day.code === null && rainSeverity !== 'normal' ? CloudRain : condition.icon;
            const severity = rainSeverity === 'warning' ? 'warning' : rainSeverity === 'rain' ? 'rain' : condition.severity;
            const date = new Date(`${day.date}T12:00:00Z`);
            return (
              <article className="climate-forecast-card" data-severity={severity} key={day.date}>
                <span>{Number.isNaN(date.getTime()) ? day.date : date.toLocaleDateString(undefined, { weekday: 'short' }).toUpperCase()}</span>
                <Icon aria-hidden="true" />
                <strong>{formatNumber(day.max, 0)}{day.max !== null ? '°' : ''}</strong>
                <small>{day.min !== null ? `${formatNumber(day.min, 0)}° ${t('climate.min')}` : `${t('climate.min')} —`}</small>
                <p>{day.precipitation !== null ? `${formatNumber(day.precipitation)} mm ${t('climate.rain').toLowerCase()}` : `${t('climate.rain')} —`}</p>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function MonthlyClimate({ climateBaseline }: { climateBaseline: ClimateBaselinePayload | null | undefined }) {
  const { t } = useI18n();
  const [metric, setMetric] = useState<'rainfall' | 'temperature'>('rainfall');
  const monthly = climateBaseline?.all_monthly_means;
  const values = metric === 'rainfall' ? monthly?.precipitation_sum : monthly?.temperature_2m_mean;
  const chartData = MONTHS.map((month, index) => ({ month, value: values?.[String(index + 1)] ?? null }));
  const hasData = chartData.some((item) => item.value !== null);

  return (
    <section id="climate-monthly" className="climate-dashboard-section" aria-labelledby="climate-monthly-title">
      <h2 id="climate-monthly-title" className="climate-section-title">{t('climate.monthly')}</h2>
      <article className="climate-chart-card">
        <div className="climate-chart-toolbar" aria-label={t('climate.monthlyMetric')}>
          <button type="button" aria-pressed={metric === 'rainfall'} onClick={() => setMetric('rainfall')}>{t('twin.rainfall')}</button>
          <button type="button" aria-pressed={metric === 'temperature'} onClick={() => setMetric('temperature')}>{t('crops.temperature')}</button>
        </div>
        {hasData ? (
          <div className="climate-chart-wrap" aria-label={`Monthly ${metric} chart`}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 10, right: 8, left: 8, bottom: 0 }}>
                <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#718077' }} />
                <Tooltip cursor={{ fill: '#f1f6f2' }} contentStyle={{ border: '1px solid #dce6df', borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="value" fill={metric === 'rainfall' ? '#4b9cf5' : '#ee9b45'} radius={[5, 5, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="climate-section-empty">{t('climate.monthlyUnavailable')}</div>
        )}
        <div className="climate-monthly-summary">
          <span><small>{t('climate.baseline')}</small><strong>{climateBaseline?.baseline_period ?? t('common.unavailable')}</strong></span>
          <span><small>{t('twin.rainfall')}</small><strong>{anomalyLabel(climateBaseline?.rainfall_anomaly, 10)}</strong></span>
          <span><small>{t('crops.temperature')}</small><strong>{anomalyLabel(climateBaseline?.temperature_anomaly, 1)}</strong></span>
        </div>
      </article>
    </section>
  );
}

function AnnualClimate({ climateBaseline, farmId }: { climateBaseline: ClimateBaselinePayload | null | undefined; farmId?: string }) {
  const { t } = useI18n();
  const rainfall = climateBaseline?.all_monthly_means?.precipitation_sum;
  const available = MONTHS.map((_, index) => rainfall?.[String(index + 1)] ?? null)
    .filter((value): value is number => value !== null);
  const sorted = [...available].sort((a, b) => a - b);
  const low = sorted[Math.floor((sorted.length - 1) * 0.33)];
  const high = sorted[Math.floor((sorted.length - 1) * 0.67)];

  return (
    <section id="climate-annual" className="climate-dashboard-section" aria-labelledby="climate-annual-title">
      <h2 id="climate-annual-title" className="climate-section-title">{t('climate.annual')}</h2>
      <article className="climate-annual-card">
        {available.length === 0 ? (
          <div className="climate-section-empty">{t('climate.annualUnavailable')}</div>
        ) : (
          <div className="climate-annual-months">
            {MONTHS.map((month, index) => {
              const value = rainfall?.[String(index + 1)] ?? null;
              const condition = value === null ? 'unavailable' : value <= low ? 'dry' : value >= high ? 'wet' : 'normal';
              const label = condition === 'unavailable' ? t('common.unavailable') : condition === 'dry' ? t('climate.dry') : condition === 'wet' ? t('climate.wet') : t('climate.normal');
              return <div key={month} data-condition={condition}><strong>{month}</strong><span>{label}</span><small>{value === null ? '—' : `${formatNumber(value)} mm`}</small></div>;
            })}
          </div>
        )}
        <div className="climate-growing-periods">
          <div><strong>{t('climate.bestPeriods')}</strong><p>{t('climate.windowsHelp')}</p></div>
          {farmId && <Link href={`/app/farms/${farmId}/annual-plan`}>{t('climate.viewPlan')} <ExternalLink aria-hidden="true" /></Link>}
        </div>
      </article>
    </section>
  );
}

export interface ClimateOverviewProps {
  weather: WeatherPayload | null | undefined;
  satellite?: SatellitePayload | null | undefined;
  climateBaseline: ClimateBaselinePayload | null | undefined;
  farmId?: string;
}

export function ClimateOverview({ weather, climateBaseline, farmId }: ClimateOverviewProps) {
  const { t } = useI18n();
  return (
    <section className="climate-overview" aria-label={t('climate.title')}>
      <nav className="climate-section-nav" aria-label={t('climate.sections')}>
        <a href="#climate-today">{t('climate.today')}</a>
        <a href="#climate-7-days">{t('climate.days7')}</a>
        <a href="#climate-monthly">{t('climate.monthlyNav')}</a>
        <a href="#climate-annual">{t('climate.annualNav')}</a>
      </nav>
      <CurrentWeather weather={weather} />
      <SevenDayForecast weather={weather} />
      <MonthlyClimate climateBaseline={climateBaseline} />
      <AnnualClimate climateBaseline={climateBaseline} farmId={farmId} />
    </section>
  );
}

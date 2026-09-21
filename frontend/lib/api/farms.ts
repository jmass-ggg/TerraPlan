import { FarmTwinApiError, requestApi } from './client';
import type {
  ApiErrorDetail,
  DataMode,
  DecisionSupport,
  FarmDetail,
  FarmListResponse,
} from './types';

// --- Environmental Twin types ---

export interface EnvironmentalValue {
  value: number | null;
  unit: string;
  source: string;
  acquired_at: string | null;
  retrieved_at: string;
  data_mode: DataMode;
  quality: string;
  resolution_m: number | null;
}

export interface WeatherPayload {
  temperature_2m: EnvironmentalValue | null;
  precipitation: EnvironmentalValue | null;
  relative_humidity_2m: EnvironmentalValue | null;
  wind_speed_10m: EnvironmentalValue | null;
  wind_direction_10m: EnvironmentalValue | null;
  cloud_cover: EnvironmentalValue | null;
  weather_code?: EnvironmentalValue | null;
  hourly?: WeatherHourlyForecast;
  daily?: WeatherDailyForecast;
  model_name?: string;
  issue_time?: string;
  valid_time?: string;
  forecast_horizon_hours?: number;
}

export interface WeatherHourlyForecast {
  time: string[];
  temperature_2m: Array<number | null>;
  precipitation: Array<number | null>;
  relative_humidity_2m: Array<number | null>;
  wind_speed_10m: Array<number | null>;
  wind_direction_10m: Array<number | null>;
  cloud_cover: Array<number | null>;
  weather_code: Array<number | null>;
}

export interface WeatherDailyForecast {
  time: string[];
  temperature_2m_min: Array<number | null>;
  temperature_2m_max: Array<number | null>;
  temperature_2m_mean: Array<number | null>;
  precipitation_sum: Array<number | null>;
  wind_speed_10m_max: Array<number | null>;
  weather_code: Array<number | null>;
}

export interface SatellitePayload {
  ndvi: EnvironmentalValue | null;
  ndmi: EnvironmentalValue | null;
  valid_pixel_pct: number | null;
  acquisition_date: string | null;
  cloud_cover_pct: number | null;
  scene_id: string | null;
  last_scene_age_days: number | null;
}

export interface SoilDepthLayer {
  bdod: EnvironmentalValue | null;
  clay: EnvironmentalValue | null;
  sand: EnvironmentalValue | null;
  silt: EnvironmentalValue | null;
  phh2o: EnvironmentalValue | null;
  soc: EnvironmentalValue | null;
}

export interface SoilPayload {
  depth_0_5cm: SoilDepthLayer | null;
  depth_5_15cm: SoilDepthLayer | null;
  source_resolution_m: number;
  small_farm_flag: boolean;
  modelled_estimate: boolean;
}

export interface TerrainPayload {
  mean_elevation_m: EnvironmentalValue | null;
  min_elevation_m: EnvironmentalValue | null;
  max_elevation_m: EnvironmentalValue | null;
  mean_slope_deg: EnvironmentalValue | null;
  dem_source: string | null;
  resolution_m: number | null;
  vertical_reference: string | null;
  flood_probability: null;
}

export interface ConduitPayload {
  eligible: boolean;
  eligibility_reason: string;
  station_distance_km: number | null;
  elevation_difference_m: number | null;
  latest_aggregate: Record<string, EnvironmentalValue> | null;
}

export interface ClimateBaselinePayload {
  baseline_source: string | null;
  baseline_period: string | null;
  temperature_anomaly: EnvironmentalValue | null;
  rainfall_anomaly: EnvironmentalValue | null;
  all_monthly_means?: {
    temperature_2m_mean: Record<string, number | null>;
    precipitation_sum: Record<string, number | null>;
  };
}

export interface JobStage {
  status: 'queued' | 'running' | 'completed' | 'failed';
  started_at: string | null;
  completed_at: string | null;
}

export interface JobProgress {
  job_id: string;
  farm_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  geometry_revision: number;
  stages: Record<string, JobStage>;
  error_message: string | null;
  snapshot_id: string | null;
  created_at: string;
  updated_at: string;
}

export type FarmTwinStatus = 'ready' | 'pending' | 'unavailable';

export interface FarmTwinResult {
  status: FarmTwinStatus;
  snapshot_id?: string;
  valid_time?: string;
  geometry_revision?: number;
  data_mode?: DataMode;
  model_version?: string;
  evidence_statuses?: Record<string, string>;
  weather?: WeatherPayload | null;
  satellite?: SatellitePayload | null;
  soil?: SoilPayload | null;
  terrain?: TerrainPayload | null;
  conduit?: ConduitPayload | null;
  climate_baseline?: ClimateBaselinePayload | null;
  job_id?: string;
}

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : null;
}

function envelope(value: unknown): EnvironmentalValue | null {
  const item = record(value);
  return item && ('value' in item) && typeof item.unit === 'string'
    ? item as unknown as EnvironmentalValue
    : null;
}

function numberArray(value: unknown): Array<number | null> {
  return Array.isArray(value)
    ? value.map((item) => typeof item === 'number' && Number.isFinite(item) ? item : null)
    : [];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function normaliseHourly(value: unknown): WeatherHourlyForecast | undefined {
  const hourly = record(value);
  if (!hourly) return undefined;
  return {
    time: stringArray(hourly.time),
    temperature_2m: numberArray(hourly.temperature_2m),
    precipitation: numberArray(hourly.precipitation),
    relative_humidity_2m: numberArray(hourly.relative_humidity_2m),
    wind_speed_10m: numberArray(hourly.wind_speed_10m),
    wind_direction_10m: numberArray(hourly.wind_direction_10m),
    cloud_cover: numberArray(hourly.cloud_cover),
    weather_code: numberArray(hourly.weather_code),
  };
}

function normaliseDaily(value: unknown): WeatherDailyForecast | undefined {
  const daily = record(value);
  if (!daily) return undefined;
  return {
    time: stringArray(daily.time),
    temperature_2m_min: numberArray(daily.temperature_2m_min),
    temperature_2m_max: numberArray(daily.temperature_2m_max),
    temperature_2m_mean: numberArray(daily.temperature_2m_mean),
    precipitation_sum: numberArray(daily.precipitation_sum),
    wind_speed_10m_max: numberArray(daily.wind_speed_10m_max),
    weather_code: numberArray(daily.weather_code),
  };
}

function normaliseWeather(value: unknown): WeatherPayload | null {
  const payload = record(value);
  if (!payload) return null;
  const fields = record(payload.fields) ?? payload;
  const metadata = record(payload.model_metadata);
  return {
    temperature_2m: envelope(fields.temperature_2m),
    precipitation: envelope(fields.precipitation),
    relative_humidity_2m: envelope(fields.relative_humidity_2m),
    wind_speed_10m: envelope(fields.wind_speed_10m),
    wind_direction_10m: envelope(fields.wind_direction_10m),
    cloud_cover: envelope(fields.cloud_cover),
    weather_code: envelope(fields.weather_code),
    hourly: normaliseHourly(payload.hourly),
    daily: normaliseDaily(payload.daily),
    model_name: typeof metadata?.source === 'string' ? metadata.source : undefined,
    issue_time: typeof metadata?.issue_time === 'string' ? metadata.issue_time : undefined,
    valid_time: typeof metadata?.valid_time === 'string' ? metadata.valid_time : undefined,
    forecast_horizon_hours:
      typeof metadata?.forecast_horizon_hours === 'number'
        ? metadata.forecast_horizon_hours
        : typeof metadata?.forecast_horizon_days === 'number'
          ? metadata.forecast_horizon_days * 24
          : undefined,
  };
}

function normaliseSoilDepth(value: unknown): SoilDepthLayer | null {
  const layer = record(value);
  if (!layer) return null;
  const mean = (key: keyof SoilDepthLayer) => envelope(record(layer[key])?.mean ?? layer[key]);
  return {
    bdod: mean('bdod'), clay: mean('clay'), sand: mean('sand'),
    silt: mean('silt'), phh2o: mean('phh2o'), soc: mean('soc'),
  };
}

function normaliseSoil(value: unknown): SoilPayload | null {
  const payload = record(value);
  if (!payload) return null;
  const depths = record(payload.depths) ?? payload;
  return {
    depth_0_5cm: normaliseSoilDepth(depths['0_5cm'] ?? depths.depth_0_5cm),
    depth_5_15cm: normaliseSoilDepth(depths['5_15cm'] ?? depths.depth_5_15cm),
    source_resolution_m: typeof payload.source_resolution_m === 'number' ? payload.source_resolution_m : 250,
    small_farm_flag: Boolean(payload.smaller_than_grid_cell ?? payload.small_farm_flag),
    modelled_estimate: payload.modelled_estimate !== false,
  };
}

function normaliseTerrain(value: unknown): TerrainPayload | null {
  const payload = record(value);
  if (!payload) return null;
  return {
    mean_elevation_m: envelope(payload.mean_elevation_m),
    min_elevation_m: envelope(payload.min_elevation_m),
    max_elevation_m: envelope(payload.max_elevation_m),
    mean_slope_deg: envelope(payload.mean_slope_deg),
    dem_source: typeof payload.dem_source === 'string' ? payload.dem_source : null,
    resolution_m: typeof payload.dem_resolution_m === 'number' ? payload.dem_resolution_m : null,
    vertical_reference: typeof payload.vertical_reference === 'string' ? payload.vertical_reference : null,
    flood_probability: null,
  };
}

function normaliseConduit(value: unknown): ConduitPayload | null {
  const payload = record(value);
  if (!payload) return null;
  const aggregates = Object.fromEntries(
    Object.entries(payload).flatMap(([key, item]) => {
      const field = envelope(item);
      return field ? [[key, field]] : [];
    }),
  );
  return {
    eligible: payload.ineligible !== true,
    eligibility_reason: typeof payload.eligibility_reason === 'string'
      ? payload.eligibility_reason
      : 'No usable nearby station evidence is available.',
    station_distance_km: typeof payload.distance_km === 'number' ? payload.distance_km : null,
    elevation_difference_m: typeof payload.elevation_diff_m === 'number' ? payload.elevation_diff_m : null,
    latest_aggregate: Object.keys(aggregates).length > 0 ? aggregates : null,
  };
}

function normaliseClimate(value: unknown): ClimateBaselinePayload | null {
  const payload = record(value);
  if (!payload) return null;
  const temperature = record(payload.temperature_2m_mean);
  const rainfall = record(payload.precipitation_sum);
  const monthly = record(payload.all_monthly_means);
  const monthlyTemperature = record(monthly?.temperature_2m_mean);
  const monthlyRainfall = record(monthly?.precipitation_sum);
  const monthlyRecord = (source: UnknownRecord | null): Record<string, number | null> =>
    Object.fromEntries(
      Array.from({ length: 12 }, (_, index) => {
        const key = String(index + 1);
        const item = source?.[key];
        return [key, typeof item === 'number' && Number.isFinite(item) ? item : null];
      }),
    );
  return {
    baseline_source: typeof payload.baseline_source === 'string' ? payload.baseline_source : null,
    baseline_period: typeof payload.baseline_period === 'string' ? payload.baseline_period : null,
    temperature_anomaly: envelope(temperature?.anomaly ?? payload.temperature_anomaly),
    rainfall_anomaly: envelope(rainfall?.anomaly ?? payload.rainfall_anomaly),
    all_monthly_means: monthly ? {
      temperature_2m_mean: monthlyRecord(monthlyTemperature),
      precipitation_sum: monthlyRecord(monthlyRainfall),
    } : undefined,
  };
}

export function normaliseFarmTwin(result: FarmTwinResult): FarmTwinResult {
  if (result.status !== 'ready') return result;
  return {
    ...result,
    weather: normaliseWeather(result.weather),
    soil: normaliseSoil(result.soil),
    terrain: normaliseTerrain(result.terrain),
    conduit: normaliseConduit(result.conduit),
    climate_baseline: normaliseClimate(result.climate_baseline),
  };
}

export interface GeoJSONPolygon {
  type: 'Polygon';
  coordinates: number[][][];
}

export interface FarmCreate {
  name: string;
  geometry: GeoJSONPolygon;
  idempotency_key?: string;
}

export interface FarmUpdate {
  name?: string;
  geometry?: GeoJSONPolygon;
  expected_revision?: number;
}

export type FarmResponse = FarmDetail;

export class FarmApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'FarmApiError';
  }
}

export class ValidationError extends FarmApiError {
  constructor(
    message: string,
    public readonly details: ApiErrorDetail[],
    requestId?: string,
  ) {
    super(message, 422, requestId);
    this.name = 'ValidationError';
  }

  fieldMessage(field: string): string | undefined {
    return this.details.find((detail) => detail.field.endsWith(field))?.message;
  }
}

export class ConflictError extends FarmApiError {
  constructor(message: string, requestId?: string) {
    super(message, 409, requestId);
    this.name = 'ConflictError';
  }
}

export class StaleRevisionError extends ConflictError {
  constructor(message: string, requestId?: string) {
    super(message, requestId);
    this.name = 'StaleRevisionError';
  }
}

function mapFarmError(error: unknown): never {
  if (!(error instanceof FarmTwinApiError)) throw error;
  if (error.status === 422) {
    throw new ValidationError(error.message, error.details, error.requestId);
  }
  if (error.status === 409) {
    if (error.details.some((detail) => detail.code === 'STALE_REVISION')) {
      throw new StaleRevisionError(error.message, error.requestId);
    }
    throw new ConflictError(error.message, error.requestId);
  }
  throw new FarmApiError(error.message, error.status, error.requestId);
}

export async function createFarm(data: FarmCreate): Promise<FarmResponse> {
  const { idempotency_key, ...body } = data;
  try {
    return (
      await requestApi<FarmResponse>('/api/v1/farms', {
        method: 'POST',
        headers: idempotency_key ? { 'Idempotency-Key': idempotency_key } : {},
        body: JSON.stringify(body),
      })
    ).data;
  } catch (error) {
    return mapFarmError(error);
  }
}

export async function getFarm(
  farmId: string,
  signal?: AbortSignal,
): Promise<FarmResponse> {
  try {
    return (
      await requestApi<FarmResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}`,
        { signal },
      )
    ).data;
  } catch (error) {
    return mapFarmError(error);
  }
}

export async function listFarms(
  signal?: AbortSignal,
): Promise<FarmListResponse> {
  try {
    return (await requestApi<FarmListResponse>('/api/v1/farms', { signal }))
      .data;
  } catch (error) {
    return mapFarmError(error);
  }
}

export async function updateFarm(
  farmId: string,
  data: FarmUpdate,
): Promise<FarmResponse> {
  try {
    return (
      await requestApi<FarmResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}`,
        {
          method: 'PATCH',
          body: JSON.stringify(data),
        },
      )
    ).data;
  } catch (error) {
    return mapFarmError(error);
  }
}

export async function deleteFarm(farmId: string): Promise<void> {
  try {
    await requestApi<void>(`/api/v1/farms/${encodeURIComponent(farmId)}`, {
      method: 'DELETE',
    });
  } catch (error) {
    return mapFarmError(error);
  }
}

export interface DecisionScenario {
  selected_month: number;
  rainfall_change_pct: number;
  temperature_change_c: number;
}

export async function calculateDecisionSupport(
  farmId: string,
  scenario: DecisionScenario,
  signal?: AbortSignal,
): Promise<DecisionSupport> {
  try {
    return (
      await requestApi<DecisionSupport>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/decision-support`,
        { method: 'POST', body: JSON.stringify(scenario), signal },
      )
    ).data;
  } catch (error) {
    return mapFarmError(error);
  }
}

export async function triggerAnalysis(
  farmId: string,
): Promise<{ jobId: string }> {
  try {
    const result = await requestApi<{ job_id: string }>(
      `/api/v1/farms/${encodeURIComponent(farmId)}/analysis-jobs`,
      { method: 'POST' },
    );
    return { jobId: result.data.job_id };
  } catch (error) {
    return mapFarmError(error);
  }
}

export async function getJobProgress(jobId: string): Promise<JobProgress> {
  try {
    return (
      await requestApi<JobProgress>(
        `/api/v1/jobs/${encodeURIComponent(jobId)}`,
      )
    ).data;
  } catch (error) {
    return mapFarmError(error);
  }
}

export async function getFarmTwin(
  farmId: string,
  signal?: AbortSignal,
): Promise<FarmTwinResult> {
  try {
    const response = await requestApi<FarmTwinResult>(
      `/api/v1/farms/${encodeURIComponent(farmId)}/digital-twin`,
      { signal },
    );
    return normaliseFarmTwin(response.data);
  } catch (error) {
    return mapFarmError(error);
  }
}

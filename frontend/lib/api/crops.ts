import { FarmTwinApiError, requestApi } from './client';
import type { ApiErrorDetail, DataMode } from './types';

// --- Crop Simulator types ---

export interface SimulateRequest {
  crop_name?: string;
  planting_date: string; // ISO date "YYYY-MM-DD"
  cultivation_mode: 'rain_fed' | 'irrigated';
  irrigation_mm?: number;
}

export interface ComponentScores {
  temperature: number;
  water: number;
  soil: number;
  heat_safety: number;
  drought_flood_safety: number;
  environmental_condition: number;
}

export interface SimulationResult {
  crop_name: string;
  suitability_index: number;
  label: 'Good match' | 'Possible match' | 'Higher caution';
  components: ComponentScores;
  limiting_factor: string;
  reason: string;
  hard_exclusion: boolean;
  hard_exclusion_reason: string | null;
  engine_version: string;
  snapshot_id: string | null;
  data_mode: DataMode;
  input_completeness: Record<string, string>;
}

export interface SimulationResponse {
  farm_id: string;
  selected: SimulationResult;
  alternatives: SimulationResult[];
  engine_version: string;
  snapshot_id: string | null;
  data_mode: DataMode;
}

export interface CropRankingResponse {
  farm_id: string;
  ranked: SimulationResult[];
  engine_version: string;
  snapshot_id: string | null;
  data_mode: DataMode;
}

export interface CropEntry {
  name: string;
  category: string;
  data_version: string;
  last_updated: string;
}

export interface CropListResponse {
  crops: CropEntry[];
}

// --- AI Explanation types ---

export interface CropExplanationFactor {
  factor: string;
  message: string;
}

export interface CropExplanation {
  headline: string;
  summary: string;
  strengths: CropExplanationFactor[];
  concerns: CropExplanationFactor[];
  action: string | null;
  data_note: string | null;
  source: 'ai' | 'deterministic_fallback';
  cached: boolean;
}

export interface CropExplanationFullResponse {
  crop_name: string;
  suitability_index: number;
  label: string;
  explanation: CropExplanation;
}

// --- Error classes ---

export class CropApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'CropApiError';
  }
}

export class CropValidationError extends CropApiError {
  constructor(
    message: string,
    public readonly details: ApiErrorDetail[],
    requestId?: string,
  ) {
    super(message, 422, requestId);
    this.name = 'CropValidationError';
  }

  fieldMessage(field: string): string | undefined {
    return this.details.find((d) => d.field.endsWith(field))?.message;
  }
}

function mapCropError(error: unknown): never {
  if (!(error instanceof FarmTwinApiError)) throw error;
  if (error.status === 422) {
    throw new CropValidationError(error.message, error.details, error.requestId);
  }
  throw new CropApiError(error.message, error.status, error.requestId);
}

// --- API functions ---

export async function getCrops(signal?: AbortSignal): Promise<CropListResponse> {
  try {
    return (
      await requestApi<CropListResponse>('/api/v1/crops', { signal })
    ).data;
  } catch (error) {
    return mapCropError(error);
  }
}

export async function simulateCrop(
  farmId: string,
  request: SimulateRequest,
  signal?: AbortSignal,
): Promise<SimulationResponse | CropRankingResponse> {
  try {
    return (
      await requestApi<SimulationResponse | CropRankingResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/simulate-crop`,
        {
          method: 'POST',
          body: JSON.stringify(request),
          signal,
        },
      )
    ).data;
  } catch (error) {
    return mapCropError(error);
  }
}

export async function getCropExplanation(
  farmId: string,
  cropName: string,
  request: SimulateRequest,
  signal?: AbortSignal,
): Promise<CropExplanationFullResponse> {
  try {
    return (
      await requestApi<CropExplanationFullResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/crops/${encodeURIComponent(cropName)}/explanation`,
        {
          method: 'POST',
          body: JSON.stringify(request),
          signal,
        },
      )
    ).data;
  } catch (error) {
    return mapCropError(error);
  }
}

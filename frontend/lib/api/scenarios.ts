/**
 * Scenario Explorer API client.
 *
 * Requirements: 2.5, 2.6
 */

import { FarmTwinApiError, requestApi } from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ScenarioDelta {
  /** Rainfall percentage change: −50 to +50 */
  rainfall_change_pct: number;
  /** Temperature change in °C: −5 to +5 */
  temperature_change_c: number;
  /** Optional irrigation override in mm */
  irrigation_mm_override?: number | null;
}

export interface CropScenarioResult {
  crop_name: string;
  baseline_index: number;
  scenario_index: number;
  baseline_label: string;
  scenario_label: string;
}

export interface HazardScenarioResult {
  hazard: string;
  baseline_level: string;
  scenario_level: string;
  baseline_index: number;
  scenario_index: number;
}

export interface ScenarioResponse {
  id: string;
  farm_id: string;
  name: string;
  baseline_snapshot_id: string | null;
  delta: ScenarioDelta;
  crops: CropScenarioResult[];
  hazards: HazardScenarioResult[];
  engine_version: string;
  created_at: string;
}

export interface ScenarioSummary {
  id: string;
  farm_id: string;
  name: string;
  baseline_snapshot_id: string | null;
  delta: ScenarioDelta;
  engine_version: string;
  created_at: string;
}

export interface ScenarioListResponse {
  farm_id: string;
  items: ScenarioSummary[];
}

// ---------------------------------------------------------------------------
// Error classes
// ---------------------------------------------------------------------------

export class ScenarioApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ScenarioApiError';
  }
}

function mapScenarioError(error: unknown): never {
  if (!(error instanceof FarmTwinApiError)) throw error;
  throw new ScenarioApiError(error.message, error.status, error.requestId);
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/**
 * Compute and save a named scenario.
 * POST /api/v1/farms/{farm_id}/scenario
 *
 * Requirements: 2.5
 */
export async function computeScenario(
  farmId: string,
  name: string,
  delta: ScenarioDelta,
  signal?: AbortSignal,
): Promise<ScenarioResponse> {
  try {
    return (
      await requestApi<ScenarioResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/scenario`,
        {
          method: 'POST',
          body: JSON.stringify({ name, delta }),
          signal,
        },
      )
    ).data;
  } catch (error) {
    return mapScenarioError(error);
  }
}

/**
 * List saved scenarios for a farm.
 * GET /api/v1/farms/{farm_id}/scenarios
 *
 * Requirements: 2.6
 */
export async function listScenarios(
  farmId: string,
  signal?: AbortSignal,
): Promise<ScenarioListResponse> {
  try {
    return (
      await requestApi<ScenarioListResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/scenarios`,
        { signal },
      )
    ).data;
  } catch (error) {
    return mapScenarioError(error);
  }
}

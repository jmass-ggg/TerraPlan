import { FarmTwinApiError, requestApi } from './client';
import type { ApiErrorDetail } from './types';

// ---------------------------------------------------------------------------
// Planner types
// ---------------------------------------------------------------------------

export interface PlanEntryResponse {
  id: string;
  farm_id: string;
  crop_name: string;
  planting_date: string;
  harvest_date: string;
  cultivation_mode: 'rain_fed' | 'irrigated';
  irrigation_mm: number | null;
  area_ha: number;
  snapshot_id: string | null;
  data_mode: string;
  suitability_index: number;
  engine_version: string;
  created_at: string;
  updated_at: string | null;
}

export interface MonthRecommendation {
  crop_name: string;
  suitability_index: number;
  label: string;
  limiting_factor: string;
}

export interface MonthRecommendationResponse {
  month: number;
  month_name: string;
  data_mode: string;
  snapshot_id: string | null;
  recommendations: MonthRecommendation[];
}

export interface ChangeProposalResponse {
  id: string;
  farm_id: string;
  entry_id: string;
  old_suitability_index: number;
  new_suitability_index: number;
  changed_inputs: Record<string, { old: unknown; new: unknown }>;
  new_snapshot_id: string;
  issue_date: string;
  status: 'pending' | 'accepted' | 'dismissed';
  created_at: string;
}

export interface AnnualPlanResponse {
  farm_id: string;
  year: number;
  months: MonthRecommendationResponse[];
  timeline: PlanTimelineItem[];
  perennial_opportunities: PerennialOpportunity[];
  entries: PlanEntryResponse[];
  proposals: ChangeProposalResponse[];
  solver_status?: string;
  fallback_used?: boolean;
  explanation?: string;
}

export interface PlanTimelineItem {
  month: number;
  month_name: string;
  crop_name: string | null;
  stage: 'planting' | 'growing' | 'flowering' | 'maturing' | 'harvest' | 'recovery';
  action: 'plant' | 'continue' | 'harvest' | 'recover';
  season_id: string | null;
  suitability_index: number | null;
  planning_score: number | null;
  plant_month: number | null;
  harvest_month: number | null;
  duration_months: number | null;
  previous_crop: string | null;
  rotation_effect: string | null;
  reason: string | null;
  limiting_factor: string | null;
  continues_next_year: boolean;
  data_mode: string;
  snapshot_id: string | null;
  saved?: boolean;
}

export interface PerennialOpportunity {
  crop_name: string;
  suitability_index: number;
  label: string;
  limiting_factor: string;
  reason: string;
}

export interface AnnualPlanScenario {
  rainfall_change_pct?: number;
  temperature_change_c?: number;
  irrigation_mm?: number | null;
}

export interface PlanEntryCreate {
  crop_name: string;
  planting_date: string;
  cultivation_mode: 'rain_fed' | 'irrigated';
  irrigation_mm?: number;
  area_ha: number;
}

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export class PlannerApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'PlannerApiError';
  }
}

export class PlannerConflictError extends PlannerApiError {
  constructor(
    message: string,
    public readonly details: ApiErrorDetail[],
    requestId?: string,
  ) {
    super(message, 422, requestId);
    this.name = 'PlannerConflictError';
  }
}

function mapPlannerError(error: unknown): never {
  if (!(error instanceof FarmTwinApiError)) throw error;
  if (error.status === 422) {
    throw new PlannerConflictError(error.message, error.details, error.requestId);
  }
  throw new PlannerApiError(error.message, error.status, error.requestId);
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function getAnnualPlan(
  farmId: string,
  year: number,
  scenario: AnnualPlanScenario = {},
  signal?: AbortSignal,
): Promise<AnnualPlanResponse> {
  try {
    const params = new URLSearchParams({ year: String(year) });
    if (scenario.rainfall_change_pct) params.set('rainfall_change_pct', String(scenario.rainfall_change_pct));
    if (scenario.temperature_change_c) params.set('temperature_change_c', String(scenario.temperature_change_c));
    if (scenario.irrigation_mm != null) params.set('irrigation_mm', String(scenario.irrigation_mm));
    return (
      await requestApi<AnnualPlanResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/crop-plan?${params.toString()}`,
        { signal },
      )
    ).data;
  } catch (error) {
    return mapPlannerError(error);
  }
}

export async function createPlanEntry(
  farmId: string,
  data: PlanEntryCreate,
): Promise<PlanEntryResponse> {
  try {
    return (
      await requestApi<PlanEntryResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/crop-plan/entries`,
        { method: 'POST', body: JSON.stringify(data) },
      )
    ).data;
  } catch (error) {
    return mapPlannerError(error);
  }
}

export async function deletePlanEntry(
  farmId: string,
  entryId: string,
): Promise<void> {
  try {
    await requestApi<void>(
      `/api/v1/farms/${encodeURIComponent(farmId)}/crop-plan/entries/${encodeURIComponent(entryId)}`,
      { method: 'DELETE' },
    );
  } catch (error) {
    return mapPlannerError(error);
  }
}

export async function acceptChangeProposal(
  farmId: string,
  proposalId: string,
): Promise<PlanEntryResponse> {
  try {
    return (
      await requestApi<PlanEntryResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/crop-plan/proposals/${encodeURIComponent(proposalId)}/accept`,
        { method: 'POST' },
      )
    ).data;
  } catch (error) {
    return mapPlannerError(error);
  }
}

export async function dismissChangeProposal(
  farmId: string,
  proposalId: string,
): Promise<void> {
  try {
    await requestApi<void>(
      `/api/v1/farms/${encodeURIComponent(farmId)}/crop-plan/proposals/${encodeURIComponent(proposalId)}/dismiss`,
      { method: 'POST' },
    );
  } catch (error) {
    return mapPlannerError(error);
  }
}

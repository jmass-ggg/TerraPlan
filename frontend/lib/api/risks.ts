import { FarmTwinApiError, requestApi } from './client';

// --- Risk Center types ---

export interface ActionRule {
  id: string;
  priority: number;
  text: string;
  source: string;
  review_date: string;
  completed: boolean;
  completed_at: string | null;
}

export interface HazardAssessment {
  hazard: string;
  index: number;
  level: 'Low' | 'Medium' | 'High' | 'Unknown';
  driver: string;
  explanation: string;
  horizon: string;
  at_risk_crops: string[];
  actions: ActionRule[];
  evidence_used: Record<string, string>;
  engine_version: string;
  snapshot_id: string | null;
  data_mode: string;
}

export interface RiskResponse {
  farm_id: string;
  assessments: HazardAssessment[];
  engine_version: string;
  snapshot_id: string | null;
  data_mode: string;
}

export interface ActionCompletionResponse {
  farm_id: string;
  action_id: string;
  completed: boolean;
  completed_at: string;
}

// --- Timeline types ---

export interface TimelineHazard {
  index: number | null;
  level: 'Low' | 'Medium' | 'High' | 'Unknown';
}

export interface RiskTimelinePoint {
  date: string;
  drought: TimelineHazard;
  heat: TimelineHazard;
  heavy_rainfall: TimelineHazard;
  flood_exposure: TimelineHazard;
  wind: TimelineHazard;
}

export interface RiskTimelineResponse {
  farm_id: string;
  snapshot_id: string | null;
  horizon_days: number;
  generated_at: string;
  points: RiskTimelinePoint[];
}

// --- Error class ---

export class RiskApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'RiskApiError';
  }
}

function mapRiskError(error: unknown): never {
  if (!(error instanceof FarmTwinApiError)) throw error;
  throw new RiskApiError(error.message, error.status, error.requestId);
}

// --- API functions ---

export async function getFarmRisks(
  farmId: string,
  signal?: AbortSignal,
): Promise<RiskResponse> {
  try {
    return (
      await requestApi<RiskResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/risks`,
        { signal },
      )
    ).data;
  } catch (error) {
    return mapRiskError(error);
  }
}

export async function completeAction(
  farmId: string,
  actionId: string,
): Promise<ActionCompletionResponse> {
  try {
    return (
      await requestApi<ActionCompletionResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/actions/${encodeURIComponent(actionId)}`,
        { method: 'PATCH' },
      )
    ).data;
  } catch (error) {
    return mapRiskError(error);
  }
}

export async function getFarmRiskTimeline(
  farmId: string,
  days: number = 7,
  signal?: AbortSignal,
): Promise<RiskTimelineResponse> {
  try {
    return (
      await requestApi<RiskTimelineResponse>(
        `/api/v1/farms/${encodeURIComponent(farmId)}/risks/timeline?days=${days}`,
        { signal },
      )
    ).data;
  } catch (error) {
    return mapRiskError(error);
  }
}

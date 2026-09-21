export type DataMode = 'live' | 'historical_replay' | 'demonstration';

export interface FarmSummary {
  id: string;
  name: string;
  current_geometry_revision: number;
  hectares: number;
  created_at: string;
  updated_at: string;
}

export interface FarmListResponse {
  items: FarmSummary[];
  limit: number;
  offset: number;
  total: number;
}

export interface GeometryRevision {
  id: string;
  revision: number;
  geometry: { type: 'Polygon'; coordinates: number[][][] };
  centroid: { type: 'Point'; coordinates: [number, number] };
  label_point: { type: 'Point'; coordinates: [number, number] };
  hectares: number;
  created_at: string;
}

export interface FarmDetail {
  id: string;
  name: string;
  current_geometry_revision: number;
  current_geometry: GeometryRevision;
  created_at: string;
  updated_at: string;
  analysis_job_id?: string | null;
}

export interface DataSourceRecord {
  name: string;
  description: string;
  data_mode: DataMode;
  last_ingestion_time: string | null;
  record_count: number;
  resolution: string | null;
  spatial_extent: string | null;
  license_note: string | null;
  pipeline_explanation: string | null;
  status: string;
}

export interface DataSourcesResponse {
  sources: DataSourceRecord[];
}

export interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    details?: ApiErrorDetail[];
  };
}

export interface ApiErrorDetail {
  field: string;
  code: string;
  message: string;
}

export interface ApiResult<T> {
  data: T;
  dataMode: DataMode | null;
  authMode: string | null;
}

export interface UserProfile {
  id: string;
  issuer: string;
  subject: string;
  email: string | null;
  display_name: string | null;
  preferences: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface UpdateProfileRequest {
  display_name?: string | null;
}

export interface CropRecommendation {
  crop: string;
  score: number;
  label: string;
  temperature_score: number;
  water_score: number;
  soil_score: number;
  climate_safety_score: number;
  environmental_score: number;
  reason: string;
  growing_months: number;
}

export interface MonthPlan {
  month: string;
  month_number: number;
  expected_temperature_c: number;
  expected_rainfall_mm: number;
  planting_window: string;
  main_risk: string;
  reason: string;
  recommendations: CropRecommendation[];
}

export interface ClimateRisk {
  slug: string;
  name: string;
  level: 'Low' | 'Medium' | 'High' | 'Unknown';
  why: string;
  affected_crops: string[];
  resilient_options: string[];
  recommended_action: string;
  evidence_note: string;
}

export interface ScenarioComparison {
  crop: string;
  baseline_score: number;
  scenario_score: number;
  delta: number;
}

export interface DecisionSupport {
  farm_id: string;
  farm_name: string;
  centroid: { latitude: number; longitude: number };
  data_mode: DataMode;
  model_version: string;
  disclaimer: string;
  assumptions: string[];
  scenario: {
    selected_month: number;
    rainfall_change_pct: number;
    temperature_change_c: number;
  };
  months: MonthPlan[];
  selected_month: MonthPlan;
  risks: ClimateRisk[];
  comparison: ScenarioComparison[];
}

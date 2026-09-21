import type {
  ApiErrorEnvelope,
  ApiResult,
  DataMode,
  DataSourcesResponse,
  ApiErrorDetail,
  FarmDetail,
  FarmListResponse,
  UserProfile,
  UpdateProfileRequest,
} from './types';

export type ApiErrorKind =
  | 'configuration'
  | 'network'
  | 'http'
  | 'invalid-response';

export class FarmTwinApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind,
    public readonly status?: number,
    public readonly code?: string,
    public readonly requestId?: string,
    public readonly details: ApiErrorDetail[] = [],
  ) {
    super(message);
    this.name = 'FarmTwinApiError';
  }
}

function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_FARMTWIN_API_URL?.trim();
  if (configured) return configured.replace(/\/$/, '');
  if (process.env.NODE_ENV === 'development') return 'http://127.0.0.1:8000';
  throw new FarmTwinApiError(
    'TerraPlan backend is not configured. Set NEXT_PUBLIC_FARMTWIN_API_URL.',
    'configuration',
  );
}

export async function requestApi<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiResult<T>> {
  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (init.body && !headers.has('Content-Type'))
    headers.set('Content-Type', 'application/json');
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      headers,
    });
  } catch (error) {
    if (
      error instanceof FarmTwinApiError ||
      (error instanceof DOMException && error.name === 'AbortError')
    ) {
      throw error;
    }
    throw new FarmTwinApiError(
      'The API could not be reached. Check the backend and try again.',
      'network',
    );
  }

  if (!response.ok) {
    let envelope: ApiErrorEnvelope = {};
    try {
      envelope = (await response.json()) as ApiErrorEnvelope;
    } catch {
      // The status and generic message remain authoritative.
    }
    throw new FarmTwinApiError(
      envelope.error?.message ?? `The API returned HTTP ${response.status}.`,
      'http',
      response.status,
      envelope.error?.code,
      envelope.error?.request_id,
      envelope.error?.details ?? [],
    );
  }

  if (response.status === 204) {
    return {
      data: undefined as T,
      dataMode: response.headers.get('X-FarmTwin-Data-Mode') as DataMode | null,
      authMode: response.headers.get('X-FarmTwin-Auth-Mode'),
    };
  }

  try {
    return {
      data: (await response.json()) as T,
      dataMode: response.headers.get('X-FarmTwin-Data-Mode') as DataMode | null,
      authMode: response.headers.get('X-FarmTwin-Auth-Mode'),
    };
  } catch {
    throw new FarmTwinApiError(
      'The API response was not valid JSON.',
      'invalid-response',
    );
  }
}

export const farmTwinApi = {
  listFarms: (signal?: AbortSignal) =>
    requestApi<FarmListResponse>('/api/v1/farms', { signal }),
  getFarm: (farmId: string, signal?: AbortSignal) =>
    requestApi<FarmDetail>(`/api/v1/farms/${encodeURIComponent(farmId)}`, {
      signal,
    }),
  listDataSources: (signal?: AbortSignal) =>
    requestApi<DataSourcesResponse>('/api/v1/data-sources', { signal }),
  getProfile: (signal?: AbortSignal) =>
    requestApi<UserProfile>('/api/v1/me', { signal }),
  updateProfile: (body: UpdateProfileRequest, signal?: AbortSignal) =>
    requestApi<UserProfile>('/api/v1/me', {
      method: 'PATCH',
      body: JSON.stringify(body),
      signal,
    }),
  getHealth: (signal?: AbortSignal) =>
    requestApi<{ status: string; non_live: boolean }>('/health', { signal }),
};

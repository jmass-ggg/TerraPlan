'use client';

import { useCallback, useEffect, useState } from 'react';

import type { ApiResult } from '@/lib/api/types';

type ResourceState<T> =
  | { status: 'loading'; result: null; error: null }
  | { status: 'success'; result: ApiResult<T>; error: null }
  | { status: 'error'; result: null; error: Error };

export function useApiResource<T>(loader: (signal: AbortSignal) => Promise<ApiResult<T>>) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<ResourceState<T>>({
    status: 'loading',
    result: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    loader(controller.signal).then(
      (result) => setState({ status: 'success', result, error: null }),
      (error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setState({
          status: 'error',
          result: null,
          error: error instanceof Error ? error : new Error('Unknown API error'),
        });
      },
    );
    return () => controller.abort();
  }, [attempt, loader]);

  const retry = useCallback(() => {
    setState({ status: 'loading', result: null, error: null });
    setAttempt((value) => value + 1);
  }, []);
  return { ...state, retry };
}

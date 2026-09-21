import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

Object.defineProperty(globalThis, 'crypto', {
  value: { randomUUID: () => '11111111-1111-4111-8111-111111111111' },
  configurable: true,
});

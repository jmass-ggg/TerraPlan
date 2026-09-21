import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import NewFarmPage from './page';

const { push, createFarm } = vi.hoisted(() => ({
  push: vi.fn(),
  createFarm: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/api/farms', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/farms')>();
  return { ...actual, createFarm };
});
vi.mock('maplibre-gl', () => {
  class MockMap {
    doubleClickZoom = { disable: vi.fn() };
    addControl() {}
    addSource() {}
    addLayer() {}
    getSource() { return { setData: vi.fn() }; }
    isStyleLoaded() { return true; }
    flyTo() {}
    remove() {}
    on(event: string, handler: () => void) { if (event === 'load') handler(); }
  }
  return { default: { Map: MockMap, AttributionControl: class {}, NavigationControl: class {} } };
});

const boundary = JSON.stringify({
  type: 'Polygon',
  coordinates: [[[36.8, -1.3], [36.805, -1.3], [36.805, -1.295], [36.8, -1.3]]],
});

describe('farm creation route', () => {
  beforeEach(() => {
    push.mockReset();
    createFarm.mockReset();
    createFarm.mockResolvedValue({ id: 'farm-123' });
  });

  it('creates a farm from an imported boundary and navigates to it', async () => {
    render(<NewFarmPage />);
    const nameInput = screen.getByLabelText('Farm name');
    await userEvent.type(nameInput, 'Upper Field');
    expect(nameInput).toHaveValue('Upper Field');
    expect(screen.getAllByRole('button', { name: 'Save farm' })).toHaveLength(1);
    await userEvent.tab();
    expect(createFarm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText('Import GeoJSON'));
    fireEvent.change(screen.getByLabelText('GeoJSON Polygon'), { target: { value: boundary } });
    await userEvent.click(screen.getByRole('button', { name: 'Use pasted boundary' }));
    await userEvent.click(screen.getByRole('checkbox', {
      name: 'I confirm this boundary represents land I own or manage.',
    }));
    await userEvent.click(screen.getByRole('button', { name: 'Save farm' }));
    await waitFor(() => expect(createFarm).toHaveBeenCalledTimes(1));
    expect(createFarm.mock.calls[0][0]).toMatchObject({
      name: 'Upper Field',
      idempotency_key: '11111111-1111-4111-8111-111111111111',
    });
    expect(push).toHaveBeenCalledWith('/app/farms/farm-123/twin');
  });
});

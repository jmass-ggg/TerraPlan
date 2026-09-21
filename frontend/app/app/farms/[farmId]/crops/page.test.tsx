'use client';

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CropSimulatorPage from './page';

// --- Hoisted mocks ---
const { simulateCrop, getCrops } = vi.hoisted(() => ({
  simulateCrop: vi.fn(),
  getCrops: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ farmId: 'farm-abc' }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/lib/api/crops', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/crops')>();
  return { ...actual, simulateCrop, getCrops };
});

// --- Fixtures ---

function makeCrop(name: string, index: number, overrides: Partial<import('@/lib/api/crops').SimulationResult> = {}): import('@/lib/api/crops').SimulationResult {
  return {
    crop_name: name,
    suitability_index: index,
    label: index >= 82 ? 'Good match' : index >= 68 ? 'Possible match' : 'Higher caution',
    components: {
      temperature: 80,
      water: 75,
      soil: 70,
      heat_safety: 85,
      drought_flood_safety: 72,
      environmental_condition: 65,
    },
    limiting_factor: 'water',
    reason: `${name} grows well under these conditions.`,
    hard_exclusion: false,
    hard_exclusion_reason: null,
    engine_version: 'farmtwin-crop-v1',
    snapshot_id: null,
    data_mode: 'demonstration',
    input_completeness: {
      temperature: 'demonstration',
      water: 'demonstration',
      soil: 'demonstration',
      heat_safety: 'demonstration',
      drought_flood_safety: 'demonstration',
      environmental_condition: 'demonstration',
    },
    ...overrides,
  };
}

const TWELVE_CROP_NAMES = [
  'Maize', 'Wheat', 'Rice', 'Potato', 'Tomato', 'Onion',
  'Beans', 'Soybean', 'Sorghum', 'Cabbage', 'Carrot', 'Sweet Potato',
];

function makeRankingResponse(overrides: Partial<import('@/lib/api/crops').SimulationResult>[] = []): import('@/lib/api/crops').CropRankingResponse {
  return {
    farm_id: 'farm-abc',
    ranked: TWELVE_CROP_NAMES.map((name, i) =>
      makeCrop(name, 90 - i * 2, overrides[i] ?? {}),
    ),
    engine_version: 'farmtwin-crop-v1',
    snapshot_id: null,
    data_mode: 'demonstration',
  };
}

function makeCropListResponse(): import('@/lib/api/crops').CropListResponse {
  return {
    crops: TWELVE_CROP_NAMES.map((name, i) => ({
      name,
      category: i < 3 ? 'cereal' : i < 5 ? 'legume' : 'vegetable',
      data_version: '1.0',
      last_updated: '2026-09-07',
    })),
  };
}

describe('CropSimulatorPage', () => {
  beforeEach(() => {
    simulateCrop.mockReset();
    getCrops.mockReset();
    getCrops.mockResolvedValue(makeCropListResponse());
    simulateCrop.mockResolvedValue(makeRankingResponse());
  });

  // ----------------------------------------------------------------
  // 1. Crop grid renders all 12 crops after initial load
  // ----------------------------------------------------------------
  it('renders all 12 crop cards after initial load', async () => {
    render(<CropSimulatorPage />);

    await waitFor(() => {
      for (const name of TWELVE_CROP_NAMES) {
        expect(screen.getByText(name)).toBeInTheDocument();
      }
    });
  });

  // ----------------------------------------------------------------
  // 2. Selecting a card calls simulateCrop with crop_name and shows detail panel
  // ----------------------------------------------------------------
  it('calls simulateCrop with crop_name and shows detail panel on card click', async () => {
    const user = userEvent.setup();

    // Second call (after initial ranking load) returns a SimulationResponse
    simulateCrop
      .mockResolvedValueOnce(makeRankingResponse())  // initial ranked load
      .mockResolvedValueOnce({                        // single crop detail
        farm_id: 'farm-abc',
        selected: makeCrop('Maize', 88),
        alternatives: [makeCrop('Sorghum', 82)],
        engine_version: 'farmtwin-crop-v1',
        snapshot_id: null,
        data_mode: 'demonstration',
      } satisfies import('@/lib/api/crops').SimulationResponse);

    render(<CropSimulatorPage />);

    // Wait for cards to appear
    await waitFor(() => expect(screen.getByText('Maize')).toBeInTheDocument());

    // Click the Maize card
    await user.click(screen.getByText('Maize').closest('button')!);

    // simulateCrop should have been called with crop_name: 'Maize'
    await waitFor(() =>
      expect(simulateCrop).toHaveBeenCalledWith(
        'farm-abc',
        expect.objectContaining({ crop_name: 'Maize' }),
      ),
    );

    // Detail panel heading should be visible
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Maize' })).toBeInTheDocument(),
    );
  });

  // ----------------------------------------------------------------
  // 3. Hard exclusion crop shows red badge and reason in detail panel
  // ----------------------------------------------------------------
  it('shows "Not suitable" badge and reason for a hard exclusion crop', async () => {
    const user = userEvent.setup();

    const exclusionCrop = makeCrop('Maize', 0, {
      hard_exclusion: true,
      hard_exclusion_reason: 'Temperature exceeds lethal threshold by more than 8°C',
      suitability_index: 0,
    });

    const otherCrops = TWELVE_CROP_NAMES.filter((n) => n !== 'Maize').map((n, i) =>
      makeCrop(n, 80 - i),
    );

    const rankingResponse = {
      farm_id: 'farm-abc',
      ranked: [exclusionCrop, ...otherCrops],
      engine_version: 'farmtwin-crop-v1',
      snapshot_id: null,
      data_mode: 'demonstration',
    };

    const detailResponse = {
      farm_id: 'farm-abc',
      selected: exclusionCrop,
      alternatives: [],
      engine_version: 'farmtwin-crop-v1',
      snapshot_id: null,
      data_mode: 'demonstration',
    };

    // First call (no crop_name) → ranking; subsequent calls (with crop_name) → detail
    simulateCrop.mockImplementation((_farmId: string, req: { crop_name?: string }) => {
      if (req.crop_name) return Promise.resolve(detailResponse);
      return Promise.resolve(rankingResponse);
    });

    render(<CropSimulatorPage />);
    await waitFor(() => expect(screen.getByText('Maize')).toBeInTheDocument());

    // The crop card should show "Not suitable" text
    const card = screen.getByText('Maize').closest<HTMLElement>('button')!;
    expect(within(card).getByText(/Not suitable/)).toBeInTheDocument();

    // Click the card to open detail panel
    await user.click(card);

    // The detail panel heading should appear
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Maize' })).toBeInTheDocument(),
    );

    // Click the Risks tab to see hard exclusion details
    await user.click(screen.getByRole('tab', { name: 'Risks' }));

    // The detail panel should show the hard exclusion alert
    await waitFor(() =>
      expect(screen.getByText(/Hard exclusion — not suitable/i)).toBeInTheDocument(),
    );

    // And the reason text — should appear in the Risks panel (not just in the card)
    await waitFor(() => {
      const riskPanel = screen.getByText(/Hard exclusion — not suitable/i).closest('[role="alert"]');
      expect(riskPanel).toBeTruthy();
      expect(riskPanel!.textContent).toContain('Temperature exceeds lethal threshold');
    });
  });

  // ----------------------------------------------------------------
  // 4a. Demonstration mode badge is visible
  // ----------------------------------------------------------------
  it('shows "Demonstration index" badge in demonstration mode', async () => {
    simulateCrop.mockResolvedValue(makeRankingResponse());

    render(<CropSimulatorPage />);

    await waitFor(() =>
      expect(screen.getByText('Demonstration index')).toBeInTheDocument(),
    );
  });

  // ----------------------------------------------------------------
  // 4b. Snapshot date badge visible when snapshot_id is present
  // ----------------------------------------------------------------
  it('shows snapshot-backed badge when snapshot_id is present', async () => {
    simulateCrop.mockResolvedValue({
      ...makeRankingResponse(),
      snapshot_id: 'snap-1234-abcd-5678',
      data_mode: 'historical_replay',
    });

    render(<CropSimulatorPage />);

    await waitFor(() =>
      expect(screen.getByText(/Snapshot-backed/)).toBeInTheDocument(),
    );
  });

  // ----------------------------------------------------------------
  // 5. Irrigated toggle reveals irrigation quantity input
  // ----------------------------------------------------------------
  it('reveals irrigation quantity input when irrigated mode is selected', async () => {
    const user = userEvent.setup();

    render(<CropSimulatorPage />);

    // Irrigation input should NOT be visible initially
    expect(screen.queryByLabelText(/Irrigation quantity/i)).not.toBeInTheDocument();

    // Click the "Irrigated" radio button
    const irrigatedBtn = screen.getByRole('radio', { name: 'Irrigated' });
    await user.click(irrigatedBtn);

    // Irrigation input should now be visible
    expect(screen.getByLabelText(/Irrigation quantity in millimetres/i)).toBeInTheDocument();
  });

  // ----------------------------------------------------------------
  // 6. 422 missing inputs: error message shown, link to trigger analysis displayed
  // ----------------------------------------------------------------
  it('shows missing-inputs error message and analysis link on 422 validation error', async () => {
    const { CropValidationError } = await import('@/lib/api/crops');

    simulateCrop.mockRejectedValue(
      new CropValidationError(
        'Required scoring inputs are absent and no fallback is available.',
        [{
          field: 'body.planting_date',
          code: 'INSUFFICIENT_EVIDENCE',
          message: 'Planting date is required',
        }],
        'req-xyz',
      ),
    );

    render(<CropSimulatorPage />);

    await waitFor(() =>
      expect(screen.getByRole('alert')).toBeInTheDocument(),
    );

    expect(screen.getByText(/Missing analysis inputs/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Required scoring inputs are absent/i),
    ).toBeInTheDocument();

    // Link to trigger analysis
    expect(screen.getByRole('link', { name: /Run analysis to get real data/i })).toBeInTheDocument();
  });
});

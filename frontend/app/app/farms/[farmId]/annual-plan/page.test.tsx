'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AnnualPlanPage from './page';

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// ---------------------------------------------------------------------------
// Hoisted mocks
// ---------------------------------------------------------------------------

const {
  getAnnualPlan,
  createPlanEntry,
  acceptChangeProposal,
  dismissChangeProposal,
  deletePlanEntry,
  calculateDecisionSupport,
  PlannerConflictError,
} = vi.hoisted(() => ({
  getAnnualPlan: vi.fn(),
  createPlanEntry: vi.fn(),
  acceptChangeProposal: vi.fn(),
  dismissChangeProposal: vi.fn(),
  deletePlanEntry: vi.fn(),
  calculateDecisionSupport: vi.fn(),
  PlannerConflictError: class PlannerConflictError extends Error {
    status = 422;
    details: unknown[];
    constructor(message: string, details: unknown[], requestId?: string) {
      super(message);
      this.name = 'PlannerConflictError';
      this.details = details;
      void requestId;
    }
  },
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ farmId: 'farm-001' }),
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

vi.mock('@/lib/api/planner', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/planner')>();
  return {
    ...actual,
    getAnnualPlan,
    createPlanEntry,
    acceptChangeProposal,
    dismissChangeProposal,
    deletePlanEntry,
    PlannerConflictError,
  };
});

vi.mock('@/lib/api/farms', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/farms')>();
  return { ...actual, calculateDecisionSupport };
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function makeDecisionSupportResponse() {
  return {
    farm_id: 'farm-001',
    farm_name: 'Test Farm',
    disclaimer: 'Planning estimate only.',
    model_version: 'v1',
    centroid: { latitude: -1.3, longitude: 36.8 },
    assumptions: ['Assumes uniform soil.'],
    selected_month: {
      month: 'January',
      month_number: 1,
      planting_window: 'Early January',
      expected_temperature_c: 22,
      expected_rainfall_mm: 60,
      main_risk: 'Drought',
      recommendations: [
        { crop: 'Maize', score: 85, label: 'Good match', reason: 'Grows well here.', temperature_score: 80, water_score: 75, climate_safety_score: 90 },
        { crop: 'Beans', score: 72, label: 'Possible match', reason: 'Adequate conditions.', temperature_score: 70, water_score: 68, climate_safety_score: 80 },
        { crop: 'Sorghum', score: 65, label: 'Higher caution', reason: 'Water limited.', temperature_score: 65, water_score: 60, climate_safety_score: 70 },
      ],
    },
    months: MONTH_NAMES.map((name, i) => ({
      month: name,
      month_number: i + 1,
      recommendations: [
        { crop: 'Maize', score: 80, label: 'Good match' },
        { crop: 'Beans', score: 70, label: 'Possible match' },
      ],
      main_risk: 'Drought',
    })),
    comparison: [
      { crop: 'Maize', baseline_score: 85, scenario_score: 80, delta: -5 },
    ],
  };
}

function makeAnnualPlanResponse(
  entries: import('@/lib/api/planner').PlanEntryResponse[] = [],
  proposals: import('@/lib/api/planner').ChangeProposalResponse[] = [],
): import('@/lib/api/planner').AnnualPlanResponse {
  const timeline: import('@/lib/api/planner').PlanTimelineItem[] = Array.from({ length: 12 }, (_, index) => {
    const month = index + 1;
    const offset = index % 4;
    return {
      month,
      month_name: MONTH_NAMES[index],
      crop_name: 'Maize',
      stage: offset === 0 ? 'planting' : offset === 3 ? 'harvest' : offset === 2 ? 'maturing' : 'growing',
      action: offset === 0 ? 'plant' : offset === 3 ? 'harvest' : 'continue',
      season_id: `maize-${Math.floor(index / 4) + 1}`,
      suitability_index: offset === 0 ? 85 : null,
      planning_score: offset === 0 ? 85 : null,
      plant_month: month - offset,
      harvest_month: month - offset + 3,
      duration_months: 4,
      previous_crop: null,
      rotation_effect: 'neutral',
      reason: offset === 0 ? 'Maize is suitable now.' : null,
      limiting_factor: offset === 0 ? 'water' : null,
      continues_next_year: false,
      data_mode: 'demonstration',
      snapshot_id: null,
    };
  });
  return {
    farm_id: 'farm-001',
    year: new Date().getFullYear(),
    months: [],
    timeline,
    perennial_opportunities: [],
    entries,
    proposals,
  };
}

function makePlanEntry(overrides: Partial<import('@/lib/api/planner').PlanEntryResponse> = {}): import('@/lib/api/planner').PlanEntryResponse {
  const year = new Date().getFullYear();
  return {
    id: 'entry-1',
    farm_id: 'farm-001',
    crop_name: 'Maize',
    planting_date: `${year}-01-01`,
    harvest_date: `${year}-04-01`,
    cultivation_mode: 'rain_fed',
    irrigation_mm: null,
    area_ha: 2.5,
    snapshot_id: null,
    data_mode: 'demonstration',
    suitability_index: 85,
    engine_version: 'v1',
    created_at: `${year}-01-01T00:00:00Z`,
    updated_at: null,
    ...overrides,
  };
}

function makeProposal(overrides: Partial<import('@/lib/api/planner').ChangeProposalResponse> = {}): import('@/lib/api/planner').ChangeProposalResponse {
  return {
    id: 'proposal-1',
    farm_id: 'farm-001',
    entry_id: 'entry-1',
    old_suitability_index: 85,
    new_suitability_index: 72,
    changed_inputs: { rainfall_mm: { old: 60, new: 40 } },
    new_snapshot_id: 'snap-2',
    issue_date: new Date().toISOString(),
    status: 'pending',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

describe('AnnualPlanPage', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/app/farms/farm-001/annual-plan');
    calculateDecisionSupport.mockResolvedValue(makeDecisionSupportResponse());
    getAnnualPlan.mockResolvedValue(makeAnnualPlanResponse());
    createPlanEntry.mockResolvedValue(makePlanEntry());
    acceptChangeProposal.mockResolvedValue(makePlanEntry());
    dismissChangeProposal.mockResolvedValue(undefined);
    deletePlanEntry.mockResolvedValue(undefined);
  });

  // -------------------------------------------------------------------------
  // Requirement 5.2 — Saved badge on month card
  // -------------------------------------------------------------------------

  describe('month card saved indicator (Req 5.2)', () => {
    it('shows a "Saved" badge on a month card when a plan entry exists for that month', async () => {
      const entry = makePlanEntry({ planting_date: `${new Date().getFullYear()}-01-15` });
      getAnnualPlan.mockResolvedValue(makeAnnualPlanResponse([entry]));

      renderWithClient(<AnnualPlanPage />);

      await waitFor(() =>
        expect(screen.getByLabelText('Saved entry')).toBeInTheDocument(),
      );
    });

    it('does not show a "Saved" badge when no plan entries exist', async () => {
      getAnnualPlan.mockResolvedValue(makeAnnualPlanResponse([]));

      renderWithClient(<AnnualPlanPage />);

      // Wait for the page to finish loading (month grid renders)
      await waitFor(() =>
        expect(screen.getByText('Jan')).toBeInTheDocument(),
      );

      expect(screen.queryByLabelText('Saved entry')).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Requirement 5.3 — Add-to-plan form
  // -------------------------------------------------------------------------

  describe('"Add to plan" form (Req 5.3)', () => {
    it('calls createPlanEntry on form submission', async () => {
      const user = userEvent.setup();
      renderWithClient(<AnnualPlanPage />);

      // Wait for crop recommendations to load
      await waitFor(() =>
        expect(screen.getByLabelText('Add Maize to plan')).toBeInTheDocument(),
      );

      await user.click(screen.getByLabelText('Add Maize to plan'));

      // Dialog should open with the form
      await waitFor(() =>
        expect(screen.getByRole('dialog')).toBeInTheDocument(),
      );

      await user.click(screen.getByRole('button', { name: 'Add to plan' }));

      await waitFor(() => expect(createPlanEntry).toHaveBeenCalledOnce());
      expect(createPlanEntry).toHaveBeenCalledWith(
        'farm-001',
        expect.objectContaining({ crop_name: 'Maize', cultivation_mode: 'rain_fed' }),
      );
    });

    it('keeps the dialog open and does not call onSuccess when createPlanEntry returns 422', async () => {
      const user = userEvent.setup();
      createPlanEntry.mockRejectedValue(
        new PlannerConflictError('Overlap detected', [{ field: 'planting_date', message: 'Overlaps with existing entry' }]),
      );

      renderWithClient(<AnnualPlanPage />);

      await waitFor(() =>
        expect(screen.getByLabelText('Add Maize to plan')).toBeInTheDocument(),
      );

      await user.click(screen.getByLabelText('Add Maize to plan'));
      await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
      await user.click(screen.getByRole('button', { name: 'Add to plan' }));

      // createPlanEntry was called with the conflict
      await waitFor(() => expect(createPlanEntry).toHaveBeenCalledOnce());
      // Dialog stays open (form still visible after error)
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Requirement 5.5 — Change proposal dialog
  // -------------------------------------------------------------------------

  describe('change proposal dialog (Req 5.5)', () => {
    it('shows a notification badge when proposals are pending', async () => {
      const entry = makePlanEntry();
      const proposal = makeProposal();
      getAnnualPlan.mockResolvedValue(makeAnnualPlanResponse([entry], [proposal]));

      renderWithClient(<AnnualPlanPage />);

      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Review change proposals' })).toBeInTheDocument(),
      );

      const trigger = screen.getByRole('button', { name: 'Review change proposals' });
      expect(within(trigger).getByText('1')).toBeInTheDocument();
    });

    it('shows accept and reject buttons inside the proposal dialog', async () => {
      const user = userEvent.setup();
      const entry = makePlanEntry();
      const proposal = makeProposal();
      getAnnualPlan.mockResolvedValue(makeAnnualPlanResponse([entry], [proposal]));

      renderWithClient(<AnnualPlanPage />);

      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Review change proposals' })).toBeInTheDocument(),
      );

      await user.click(screen.getByRole('button', { name: 'Review change proposals' }));

      await waitFor(() =>
        expect(screen.getByRole('dialog')).toBeInTheDocument(),
      );

      expect(screen.getByRole('button', { name: /Accept/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Reject/i })).toBeInTheDocument();
    });

    it('calls acceptChangeProposal when the Accept button is clicked', async () => {
      const user = userEvent.setup();
      const entry = makePlanEntry();
      const proposal = makeProposal();
      getAnnualPlan.mockResolvedValue(makeAnnualPlanResponse([entry], [proposal]));

      renderWithClient(<AnnualPlanPage />);

      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Review change proposals' })).toBeInTheDocument(),
      );

      await user.click(screen.getByRole('button', { name: 'Review change proposals' }));
      await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
      await user.click(screen.getByRole('button', { name: /Accept/i }));

      await waitFor(() => expect(acceptChangeProposal).toHaveBeenCalledWith('farm-001', 'proposal-1'));
    });
  });

  describe('crop cycle status and navigation', () => {
    it('uses a distinct season and month in every crop-plan link', async () => {
      renderWithClient(<AnnualPlanPage />);

      const links = await screen.findAllByRole('link', { name: 'View Maize plan →' });
      expect(links).toHaveLength(3);
      expect(links.map((link) => link.getAttribute('href'))).toEqual([
        '/app/farms/farm-001/annual-plan?season=maize-1&month=1',
        '/app/farms/farm-001/annual-plan?season=maize-2&month=5',
        '/app/farms/farm-001/annual-plan?season=maize-3&month=9',
      ]);
    });

    it('treats an unsaved recommendation as the next crop instead of currently growing', async () => {
      renderWithClient(<AnnualPlanPage />);

      await screen.findByRole('heading', { name: 'Your crop sequence' });
      expect(screen.getByText('Next crop')).toBeInTheDocument();
      expect(screen.queryByText('Currently growing')).not.toBeInTheDocument();
      expect(screen.getByText('Prepare the field for Maize')).toBeInTheDocument();
    });

    it('shows the calculated cross-year harvest date without overflowing the current year', async () => {
      const response = makeAnnualPlanResponse();
      response.timeline = [9, 10, 11, 12].map((month, index) => ({
        ...response.timeline[month - 1],
        month,
        month_name: MONTH_NAMES[month - 1],
        crop_name: 'Wheat',
        season_id: 'wheat-autumn',
        stage: index === 0 ? 'planting' : 'growing',
        action: index === 0 ? 'plant' : 'continue',
        suitability_index: index === 0 ? 91 : null,
        planning_score: index === 0 ? 91 : null,
        plant_month: 9,
        harvest_month: null,
        duration_months: 5,
        continues_next_year: true,
      }));
      getAnnualPlan.mockResolvedValue(response);

      renderWithClient(<AnnualPlanPage />);

      const year = new Date().getFullYear();
      expect(await screen.findByText(`Sep ${year} → Jan ${year + 1}`)).toBeInTheDocument();
      expect(screen.getByLabelText(`Continues into ${year + 1}`)).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'View Wheat plan →' })).toHaveAttribute(
        'href',
        '/app/farms/farm-001/annual-plan?season=wheat-autumn&month=9',
      );
    });
  });

  describe('Growing Plan mode', () => {
    it('opens the exact cycle from a direct season URL and returns to the overview', async () => {
      window.history.replaceState({}, '', '/app/farms/farm-001/annual-plan?season=maize-2&month=5');
      renderWithClient(<AnnualPlanPage />);

      expect(await screen.findByRole('heading', { name: 'Your Maize Growing Plan' })).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Back to Annual Plan' })).toHaveAttribute(
        'href',
        '/app/farms/farm-001/annual-plan',
      );
      expect(screen.getByText(`May ${new Date().getFullYear()}`, { selector: 'dd' })).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: 'Your Annual Farm Plan' })).not.toBeInTheDocument();
    });

    it.each(['Soybean', 'Tomato'])('reuses the Growing Plan UI for %s', async (cropName) => {
      const response = makeAnnualPlanResponse();
      response.timeline = response.timeline.slice(0, 4).map((item) => ({
        ...item,
        crop_name: cropName,
        season_id: `${cropName.toLowerCase()}-spring`,
      }));
      getAnnualPlan.mockResolvedValue(response);
      window.history.replaceState({}, '', `/app/farms/farm-001/annual-plan?season=${cropName.toLowerCase()}-spring&month=1`);

      renderWithClient(<AnnualPlanPage />);

      expect(await screen.findByRole('heading', { name: `Your ${cropName} Growing Plan` })).toBeInTheDocument();
      expect(screen.getByLabelText(`${cropName} cycle summary`)).toBeInTheDocument();
      const summary = screen.getByLabelText(`${cropName} cycle summary`);
      expect(summary.querySelector('img')).toHaveAttribute('src', expect.stringContaining(cropName === 'Tomato' ? 'tomatos.png' : 'soyabean.png'));
      expect(screen.getByRole('heading', { name: 'What happens next' })).toBeInTheDocument();
    });

    it('shows a safe not-found state for an invalid season', async () => {
      window.history.replaceState({}, '', '/app/farms/farm-001/annual-plan?season=missing-cycle&month=9');
      renderWithClient(<AnnualPlanPage />);

      expect(await screen.findByRole('heading', { name: 'Crop plan not found.' })).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Back to Annual Plan' })).toHaveAttribute(
        'href',
        '/app/farms/farm-001/annual-plan',
      );
    });
  });
});

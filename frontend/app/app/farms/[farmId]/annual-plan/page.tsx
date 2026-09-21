'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Bell,
  CalendarDays,
  CheckCircle,
  CloudRain,
  Info,
  Plus,
  ThermometerSun,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import { ApiErrorState } from '@/components/api-state';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import { ScenarioControls } from '@/features/decision/ScenarioControls';
import { CropVisual } from '@/features/crops/CropCard';
import {
  acceptChangeProposal,
  createPlanEntry,
  deletePlanEntry,
  dismissChangeProposal,
  getAnnualPlan,
  type PlanEntryCreate,
} from '@/lib/api/planner';
import { calculateDecisionSupport } from '@/lib/api/farms';
import {
  computeScenario,
  type CropScenarioResult,
  type HazardScenarioResult,
} from '@/lib/api/scenarios';
import { useI18n } from '@/lib/i18n/context';

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function monthDate(year: number, month: number) {
  return new Date(Date.UTC(year, month - 1, 1));
}

function addUtcMonths(value: Date, months: number) {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth() + months, 1));
}

function formatMonthYear(value: Date, compact = false) {
  return new Intl.DateTimeFormat(undefined, {
    month: compact ? 'short' : 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(value);
}

function farmerFriendlyRisk(value: string) {
  return value.trim().toLowerCase() === 'no dominant climate signal'
    ? 'No major climate risk'
    : value;
}

interface CropCycleView {
  seasonId: string;
  cropName: string;
  startMonth: number;
  endMonth: number;
  score: number | null;
  reason: string;
  saved: boolean;
  continuesNextYear: boolean;
  durationMonths: number;
  plantingDate: Date;
  harvestDate: Date;
  status: 'upcoming' | 'current' | 'completed';
}

interface GrowingPlanProps {
  farmId: string;
  cycle: CropCycleView;
  nextCycle: CropCycleView | null;
  risk: string;
  onNavigateCycle: (month: number) => void;
}

function GrowingPlan({ farmId, cycle, nextCycle, risk, onNavigateCycle }: GrowingPlanProps) {
  const { t } = useI18n();
  const today = new Date();
  const prepareDate = addUtcMonths(cycle.plantingDate, -1);
  const growDate = addUtcMonths(cycle.plantingDate, 1);
  const matureDate = addUtcMonths(cycle.harvestDate, -1);
  const growEndDate = addUtcMonths(matureDate, -1);
  const stages = [
    { key: 'prepare', label: 'Prepare', start: prepareDate, end: cycle.plantingDate, display: formatMonthYear(prepareDate, true) },
    { key: 'plant', label: 'Plant', start: cycle.plantingDate, end: growDate, display: formatMonthYear(cycle.plantingDate, true) },
    { key: 'grow', label: 'Grow', start: growDate, end: matureDate, display: growDate < growEndDate ? `${formatMonthYear(growDate, true)}–${formatMonthYear(growEndDate, true)}` : formatMonthYear(growDate, true) },
    { key: 'mature', label: 'Mature', start: matureDate, end: cycle.harvestDate, display: formatMonthYear(matureDate, true) },
    { key: 'harvest', label: 'Harvest', start: cycle.harvestDate, end: addUtcMonths(cycle.harvestDate, 1), display: formatMonthYear(cycle.harvestDate, true) },
  ].map((stage) => ({
    ...stage,
    status: today >= stage.end ? 'complete' : today >= stage.start ? 'current' : 'upcoming',
  }));
  const activeStage = stages.find((stage) => stage.status === 'current')
    ?? stages.find((stage) => stage.status === 'upcoming')
    ?? stages[stages.length - 1];
  const tasks: Record<string, string[]> = {
    prepare: ['Check the field', 'Clear old crop residue', 'Prepare the soil'],
    plant: [`Plant ${cycle.cropName}`, 'Check soil moisture', 'Review expected rainfall'],
    grow: ['Monitor crop health', 'Monitor water conditions', 'Check for visible pest or disease signs'],
    mature: ['Monitor crop maturity', 'Avoid unnecessary intervention', 'Prepare for harvest'],
    harvest: ['Check crop maturity', 'Prepare for harvest', 'Review the next crop'],
  };
  const stageTasks = tasks[activeStage.key];
  const remainingStages = stages.filter((stage) => stage.status !== 'complete');
  const nextStages = remainingStages.length > 0 ? remainingStages : [stages[stages.length - 1]];
  const stableConditions = farmerFriendlyRisk(risk) === 'No major climate risk';
  const qualityLabel = cycle.score == null
    ? 'Match pending'
    : cycle.score >= 85
      ? 'Excellent match'
      : cycle.score >= 70
        ? 'Good match'
        : 'Possible match';
  const recoveryMonths = nextCycle
    ? Math.max(0, (nextCycle.plantingDate.getUTCFullYear() - cycle.harvestDate.getUTCFullYear()) * 12
      + nextCycle.plantingDate.getUTCMonth() - cycle.harvestDate.getUTCMonth())
    : 0;

  return (
    <main className="growing-plan">
      <Link className="back-link" href={`/app/farms/${farmId}/annual-plan`}>
        <ArrowLeft /> {t('plan.back')}
      </Link>
      <header className="growing-plan-heading">
        <h1>{t('plan.growingTitle', { crop: cycle.cropName })}</h1>
        <p>{t('plan.growingIntro')}</p>
      </header>

      <section className="workspace-card growing-plan-summary" aria-label={`${cycle.cropName} cycle summary`}>
        <CropVisual cropName={cycle.cropName} />
        <div><strong>{cycle.cropName}</strong><b>{cycle.score ?? '—'}% match</b><span>{qualityLabel}</span></div>
        <dl><div><dt>{t('plan.plant')}</dt><dd>{formatMonthYear(cycle.plantingDate)}</dd></div><div><dt>{t('plan.harvest')}</dt><dd>{formatMonthYear(cycle.harvestDate)}</dd></div></dl>
      </section>

      <section className="growing-plan-section" aria-labelledby="journey-title">
        <div className="growing-plan-section-heading"><div><h2 id="journey-title">{t('plan.journey')}</h2><p>{t('plan.journeyHelp')}</p></div></div>
        <ol className="workspace-card growing-plan-journey">
          {stages.map((stage) => <li key={stage.key} data-status={stage.status}><i>{stage.status === 'complete' ? '✓' : stage.status === 'current' ? '●' : '○'}</i><strong>{stage.label}</strong><span>{stage.display}</span><small>{stage.status === 'complete' ? 'Complete' : stage.status === 'current' ? 'Current' : 'Upcoming'}</small></li>)}
        </ol>
      </section>

      <div className="growing-plan-now-grid">
        <section className="workspace-card growing-plan-tasks">
          <p className="section-kicker">{t('plan.doNow')}</p>
          <h2>{activeStage.label === 'Grow' ? `Help ${cycle.cropName} grow` : `${activeStage.label} ${cycle.cropName}`}</h2>
          <span>{activeStage.display}</span>
          <ul>{stageTasks.map((task, index) => <li key={task} data-done={index === 0 || undefined}><i>{index === 0 ? '✓' : '○'}</i>{task}</li>)}</ul>
          <b>{Math.max(0, stageTasks.length - 1)} tasks left</b>
        </section>
        <aside className="workspace-card growing-plan-field">
          <h2>{t('plan.fieldLooks')}</h2>
          <strong data-good={stableConditions || undefined}>{stableConditions ? t('plan.lookingGood') : t('plan.needsAttention')}</strong>
          <p>{stableConditions ? t('plan.conditionsSuitable') : `${farmerFriendlyRisk(risk)} is the main condition to watch.`}</p>
          <Link href={`/app/farms/${farmId}/twin`}>{t('plan.viewConditions')}</Link>
        </aside>
      </div>

      <div className="growing-plan-two-column">
        <section className="workspace-card growing-plan-next-steps">
          <h2>{t('plan.happensNext')}</h2>
          <ol>{nextStages.map((stage) => <li key={stage.key}><span>{stage.display}</span><strong>{stage.label === 'Grow' ? `Help ${cycle.cropName} grow` : stage.label === 'Plant' ? `Plant ${cycle.cropName}` : stage.label}</strong><small>{stage.status === 'complete' ? 'Complete' : stage.status === 'current' ? 'Current' : 'Next'}</small></li>)}</ol>
        </section>
        <section className="workspace-card growing-plan-watch">
          <h2>{t('plan.thingsWatch')}</h2>
          <div data-good={stableConditions || undefined}><strong>{stableConditions ? t('plan.conditionsStable') : `⚠ ${farmerFriendlyRisk(risk)}`}</strong><span>{stableConditions ? t('plan.lowConcern') : t('plan.watchClosely')}</span><p>{stableConditions ? t('plan.monitorConditions') : `Keep an eye on ${farmerFriendlyRisk(risk).toLowerCase()} during growth.`}</p></div>
          <Link href={`/app/farms/${farmId}/risks`}>{t('plan.viewRisks')}</Link>
        </section>
      </div>

      <section className="growing-plan-harvest">
        <p className="section-kicker">{t('plan.expectedHarvest')}</p>
        <h2>{formatMonthYear(cycle.harvestDate)}</h2>
        <p>{t('plan.harvestReady', { crop: cycle.cropName, date: formatMonthYear(cycle.harvestDate) })}</p>
        <small>{t('plan.estimateNote')}</small>
      </section>

      {nextCycle && <section className="growing-plan-following" aria-labelledby="following-title"><h2 id="following-title">{t('plan.whatsNext')}</h2><div><article><span>1</span><strong>{recoveryMonths > 0 ? t('plan.restSoil') : `${t('plan.harvest')} ${cycle.cropName}`}</strong><small>{recoveryMonths > 0 ? t('plan.aboutMonths', { count: recoveryMonths }) : formatMonthYear(cycle.harvestDate)}</small></article><b>→</b><article><span>2</span><strong>🌿 {t('plan.plant')} {nextCycle.cropName}</strong><small>{t('plan.recommendedNext', { score: nextCycle.score ?? '—' })}</small><Link href={`/app/farms/${farmId}/annual-plan?season=${encodeURIComponent(nextCycle.seasonId)}&month=${nextCycle.startMonth}`} onClick={() => onNavigateCycle(nextCycle.startMonth)}>{t('plan.viewCropPlan', { crop: nextCycle.cropName })}</Link></article></div></section>}

      <details className="workspace-card growing-plan-why"><summary>{t('plan.whyCrop', { crop: cycle.cropName })}</summary><p>{cycle.reason}</p></details>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Add-to-plan form
// ---------------------------------------------------------------------------

interface AddToPlanFormProps {
  farmId: string;
  cropName: string;
  monthNumber: number;
  monthName: string;
  onSuccess: () => void;
  onClose: () => void;
}

function AddToPlanForm({
  farmId,
  cropName,
  monthNumber,
  monthName,
  onSuccess,
  onClose,
}: AddToPlanFormProps) {
  const { t } = useI18n();
  const year = new Date().getFullYear();
  const defaultDate = `${year}-${String(monthNumber).padStart(2, '0')}-01`;

  const [plantingDate, setPlantingDate] = useState(defaultDate);
  const [cultivationMode, setCultivationMode] = useState<'rain_fed' | 'irrigated'>('rain_fed');
  const [irrigationMm, setIrrigationMm] = useState('');
  const [areaHa, setAreaHa] = useState('1');

  const mutation = useMutation({
    mutationFn: (data: PlanEntryCreate) => createPlanEntry(farmId, data),
    onSuccess: () => {
      toast.add({ title: `${cropName} added to plan`, type: 'success' });
      onSuccess();
    },
    onError: (err: Error) => {
      toast.add({
        title: 'Could not add entry',
        description: err.message,
        type: 'error',
      });
    },
  });

  const handleSubmit = (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault();
    const data: PlanEntryCreate = {
      crop_name: cropName,
      planting_date: plantingDate,
      cultivation_mode: cultivationMode,
      area_ha: Number(areaHa),
    };
    if (cultivationMode === 'irrigated' && irrigationMm) {
      data.irrigation_mm = Number(irrigationMm);
    }
    mutation.mutate(data);
  };

  return (
    <form onSubmit={handleSubmit} className="planner-form">
      <div className="planner-form-field">
        <label htmlFor="crop-name-display">{t('plan.crop')}</label>
        <p id="crop-name-display" className="planner-form-value">
          {cropName}
        </p>
      </div>

      <div className="planner-form-field">
        <label htmlFor="planting-date">{t('crops.plantingDate')}</label>
        <input
          id="planting-date"
          type="date"
          value={plantingDate}
          onChange={(e) => setPlantingDate(e.target.value)}
          required
          aria-label={`Planting date for ${monthName}`}
        />
      </div>

      <fieldset className="planner-form-field">
        <legend>{t('plan.cultivationMode')}</legend>
        <div className="planner-radio-group">
          <label>
            <input
              type="radio"
              name="cultivation-mode"
              value="rain_fed"
              checked={cultivationMode === 'rain_fed'}
              onChange={() => setCultivationMode('rain_fed')}
            />
            {t('crops.rainFed')}
          </label>
          <label>
            <input
              type="radio"
              name="cultivation-mode"
              value="irrigated"
              checked={cultivationMode === 'irrigated'}
              onChange={() => setCultivationMode('irrigated')}
            />
            {t('crops.irrigated')}
          </label>
        </div>
      </fieldset>

      {cultivationMode === 'irrigated' && (
        <div className="planner-form-field">
          <label htmlFor="irrigation-mm">{t('plan.irrigationMonthly')}</label>
          <input
            id="irrigation-mm"
            type="number"
            min="0"
            step="any"
            value={irrigationMm}
            onChange={(e) => setIrrigationMm(e.target.value)}
            required
            aria-label={t('plan.irrigationMonthly')}
          />
        </div>
      )}

      <div className="planner-form-field">
        <label htmlFor="area-ha">{t('plan.areaHectares')}</label>
        <input
          id="area-ha"
          type="number"
          min="0.01"
          step="any"
          value={areaHa}
          onChange={(e) => setAreaHa(e.target.value)}
          required
          aria-label={t('plan.areaAria')}
        />
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          {t('common.cancel')}
        </Button>
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? t('common.saving') : t('plan.add')}
        </Button>
      </DialogFooter>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Delete-entry confirmation dialog
// ---------------------------------------------------------------------------

interface DeleteEntryDialogProps {
  farmId: string;
  entryId: string;
  cropName: string;
  onDeleted: () => void;
}

function DeleteEntryDialog({
  farmId,
  entryId,
  cropName,
  onDeleted,
}: DeleteEntryDialogProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: () => deletePlanEntry(farmId, entryId),
    onSuccess: () => {
      toast.add({ title: `${cropName} removed from plan`, type: 'success' });
      setOpen(false);
      onDeleted();
    },
    onError: (err: Error) => {
      toast.add({ title: 'Delete failed', description: err.message, type: 'error' });
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete ${cropName} entry`}
          />
        }
      >
        <Trash2 />
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('plan.removeEntry')}</DialogTitle>
          <DialogDescription>
            This will permanently remove <strong>{cropName}</strong> from your
            saved schedule.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter showCloseButton>
          <Button
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? 'Removing…' : 'Remove'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Change-proposal review dialog
// ---------------------------------------------------------------------------

interface ProposalDialogProps {
  farmId: string;
  proposals: import('@/lib/api/planner').ChangeProposalResponse[];
  entries: import('@/lib/api/planner').PlanEntryResponse[];
  onAccepted: () => void;
}

function ProposalDialog({ farmId, proposals, entries, onAccepted }: ProposalDialogProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  const acceptMutation = useMutation({
    mutationFn: (proposalId: string) => acceptChangeProposal(farmId, proposalId),
    onSuccess: () => {
      toast.add({ title: 'Proposal accepted', type: 'success' });
      setOpen(false);
      onAccepted();
    },
    onError: (err: Error) => {
      toast.add({ title: 'Accept failed', description: err.message, type: 'error' });
    },
  });

  const dismissMutation = useMutation({
    mutationFn: (proposalId: string) => dismissChangeProposal(farmId, proposalId),
    onSuccess: () => {
      toast.add({ title: 'Proposal dismissed', type: 'success' });
      setOpen(false);
      onAccepted();
    },
    onError: (err: Error) => {
      toast.add({ title: 'Dismiss failed', description: err.message, type: 'error' });
    },
  });

  const isBusy = acceptMutation.isPending || dismissMutation.isPending;

  const pendingProposals = proposals.filter((p) => p.status === 'pending');
  if (pendingProposals.length === 0) return null;
  const featuredProposal = pendingProposals[0];
  const featuredEntry = entries.find((entry) => entry.id === featuredProposal.entry_id);
  const featuredDelta = featuredProposal.new_suitability_index - featuredProposal.old_suitability_index;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" className="planner-change-banner" aria-label={t('plan.reviewProposals')} />
        }
      >
        <Bell />
        <span className="planner-change-copy">
          <strong>{t('plan.updateAvailable')}</strong>
          <span>
            {featuredEntry?.crop_name ?? 'A saved crop'} suitability changed from{' '}
            {featuredProposal.old_suitability_index} to {featuredProposal.new_suitability_index}
            {featuredDelta === 0 ? '.' : ` (${featuredDelta > 0 ? '+' : ''}${featuredDelta}).`}
          </span>
        </span>
        <span className="planner-change-link">{t('plan.viewDetails')}</span>
        <span className="proposal-badge" aria-label={`${pendingProposals.length} pending proposals`}>
          {pendingProposals.length}
        </span>
      </DialogTrigger>

      <DialogContent className="proposal-dialog-content" showCloseButton>
        <DialogHeader>
          <DialogTitle>{t('plan.changeProposals')}</DialogTitle>
          <DialogDescription>
            {t('plan.proposalHelp')}
          </DialogDescription>
        </DialogHeader>

        <div className="proposal-list">
          {pendingProposals.map((proposal) => {
            const entry = entries.find((e) => e.id === proposal.entry_id);
            const delta = proposal.new_suitability_index - proposal.old_suitability_index;
            return (
              <div key={proposal.id} className="proposal-item">
                <div className="proposal-item-header">
                  <strong>{entry?.crop_name ?? 'Unknown crop'}</strong>
                  <span className="proposal-delta" data-direction={delta > 0 ? 'up' : 'down'}>
                    {delta > 0 ? '+' : ''}{delta} pts
                  </span>
                </div>
                <div className="proposal-scores">
                  <span>
                    Was <b>{proposal.old_suitability_index}</b>
                  </span>
                  <span>→</span>
                  <span>
                    Now <b>{proposal.new_suitability_index}</b>
                  </span>
                </div>
                {Object.entries(proposal.changed_inputs).length > 0 && (
                  <dl className="proposal-changes">
                    {Object.entries(proposal.changed_inputs).map(([field, vals]) => (
                      <div key={field}>
                        <dt>{field}</dt>
                        <dd>
                          {String((vals as { old: unknown; new: unknown }).old)} →{' '}
                          {String((vals as { old: unknown; new: unknown }).new)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
                <div className="proposal-item-actions">
                  <Button
                    size="sm"
                    onClick={() => acceptMutation.mutate(proposal.id)}
                    disabled={isBusy}
                  >
                    <CheckCircle />
                    {t('plan.accept')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => dismissMutation.mutate(proposal.id)}
                    disabled={isBusy}
                    aria-label={`Reject proposal for ${entry?.crop_name ?? 'crop'}`}
                  >
                    <X />
                    {t('plan.reject')}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function AnnualPlanPage() {
  const { farmId } = useParams<{ farmId: string }>();
  const { language, t } = useI18n();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const year = new Date().getFullYear();
  const localizedMonths = Array.from({ length: 12 }, (_, index) =>
    new Intl.DateTimeFormat(language === 'sw' ? 'sw-KE' : 'en-KE', { month: 'long', timeZone: 'UTC' })
      .format(new Date(Date.UTC(year, index, 1))),
  );
  const requestedSeason = searchParams.get('season')?.trim() ?? '';
  const requestedMonth = Number(searchParams.get('month'));

  const [month, setMonth] = useState(() => {
    if (typeof window === 'undefined') return new Date().getMonth() + 1;
    const requestedMonth = Number(new URLSearchParams(window.location.search).get('month'));
    return Number.isInteger(requestedMonth) && requestedMonth >= 1 && requestedMonth <= 12
      ? requestedMonth
      : new Date().getMonth() + 1;
  });
  const [draftRainfall, setDraftRainfall] = useState(0);
  const [draftTemperature, setDraftTemperature] = useState(0);
  const [draftIrrigationMm, setDraftIrrigationMm] = useState('');
  const [scenario, setScenario] = useState<{ rainfall: number; temperature: number; irrigation: number | null }>({ rainfall: 0, temperature: 0, irrigation: null });
  const [addToPlanOpen, setAddToPlanOpen] = useState(false);
  const [addToPlanCrop, setAddToPlanCrop] = useState<{ name: string; monthNumber: number; monthName: string } | null>(null);
  const activeMonth = requestedSeason && Number.isInteger(requestedMonth) && requestedMonth >= 1 && requestedMonth <= 12
    ? requestedMonth
    : month;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const cropName = params.get('crop')?.trim();
    const requestedMonth = Number(params.get('month'));
    if (!cropName) return;
    const monthNumber = Number.isInteger(requestedMonth) && requestedMonth >= 1 && requestedMonth <= 12
      ? requestedMonth
      : new Date().getMonth() + 1;
    const monthName = new Intl.DateTimeFormat(undefined, { month: 'long' }).format(
      new Date(Date.UTC(year, monthNumber - 1, 1)),
    );
    queueMicrotask(() => {
      setMonth(monthNumber);
      setAddToPlanCrop({ name: cropName, monthNumber, monthName });
      setAddToPlanOpen(true);
    });
  }, [year]);

  // Real scenario results (populated when snapshot is available)
  const [scenarioResult, setScenarioResult] = useState<{
    crops: CropScenarioResult[];
    hazards: HazardScenarioResult[];
  } | null>(null);
  const [scenarioBusy, setScenarioBusy] = useState(false);

  const plan = useQuery({
    queryKey: [
      'decision-support',
      farmId,
      activeMonth,
      scenario.rainfall,
      scenario.temperature,
    ],
    queryFn: ({ signal }) =>
      calculateDecisionSupport(
        farmId,
        {
          selected_month: activeMonth,
          rainfall_change_pct: scenario.rainfall,
          temperature_change_c: scenario.temperature,
        },
        signal,
      ),
  });

  const cropPlan = useQuery({
    queryKey: ['crop-plan', farmId, year, scenario.rainfall, scenario.temperature, scenario.irrigation],
    queryFn: ({ signal }) => getAnnualPlan(farmId, year, {
      rainfall_change_pct: scenario.rainfall,
      temperature_change_c: scenario.temperature,
      irrigation_mm: scenario.irrigation,
    }, signal),
  });

  const invalidatePlan = () => {
    void queryClient.invalidateQueries({ queryKey: ['crop-plan', farmId, year] });
  };

  // Derive snapshotId from the decision-support response data_mode
  // (data_mode !== 'demonstration' means a snapshot is backing the response)
  const snapshotId =
    plan.data && plan.data.data_mode !== 'demonstration'
      ? (plan.data as { snapshot_id?: string }).snapshot_id ?? null
      : null;

  const apply = async () => {
    const hasChanges =
      draftRainfall !== 0 || draftTemperature !== 0 || draftIrrigationMm !== '';

    if (snapshotId && hasChanges) {
      // Use real scenario API when a snapshot backs the response
      setScenarioBusy(true);
      try {
        const result = await computeScenario(farmId, 'Planner scenario', {
          rainfall_change_pct: draftRainfall,
          temperature_change_c: draftTemperature,
          irrigation_mm_override: draftIrrigationMm !== '' ? parseFloat(draftIrrigationMm) : null,
        });
        setScenarioResult({ crops: result.crops, hazards: result.hazards });
        setScenario({ rainfall: draftRainfall, temperature: draftTemperature, irrigation: draftIrrigationMm !== '' ? parseFloat(draftIrrigationMm) : null });
      } catch {
        // fall through to demo engine on error
        setScenario({ rainfall: draftRainfall, temperature: draftTemperature, irrigation: draftIrrigationMm !== '' ? parseFloat(draftIrrigationMm) : null });
      } finally {
        setScenarioBusy(false);
      }
    } else {
      // Demo engine path — just pass params to decision-support query
      setScenarioResult(null);
      setScenario({ rainfall: draftRainfall, temperature: draftTemperature, irrigation: draftIrrigationMm !== '' ? parseFloat(draftIrrigationMm) : null });
    }
  };

  const reset = () => {
    setDraftRainfall(0);
    setDraftTemperature(0);
    setDraftIrrigationMm('');
    setScenario({ rainfall: 0, temperature: 0, irrigation: null });
    setScenarioResult(null);
  };

  const openAddToPlan = (cropName: string, monthNumber: number, monthName: string) => {
    setAddToPlanCrop({ name: cropName, monthNumber, monthName });
    setAddToPlanOpen(true);
  };

  const timeline = cropPlan.data?.timeline ?? [];
  const selectedActivity = timeline.find((item) => item.month === activeMonth) ?? null;
  const cycleMap = new Map<string, typeof timeline>();
  timeline.forEach((item) => {
    if (!item.season_id || !item.crop_name || item.stage === 'recovery') return;
    const cycle = cycleMap.get(item.season_id) ?? [];
    cycle.push(item);
    cycleMap.set(item.season_id, cycle);
  });
  const cycles = Array.from(cycleMap.entries()).map(([seasonId, items]) => {
    const ordered = [...items].sort((a, b) => a.month - b.month);
    const planting = ordered.find((item) => item.stage === 'planting') ?? ordered[0];
    const scoreItem = ordered.find((item) => item.suitability_index !== null) ?? planting;
    const savedEntry = (cropPlan.data?.entries ?? []).find((entry) => {
      if (seasonId === `saved-${entry.id}`) return true;
      if (entry.crop_name !== planting.crop_name) return false;
      const entryStart = new Date(entry.planting_date).getUTCMonth() + 1;
      const entryEnd = new Date(entry.harvest_date).getUTCMonth() + 1;
      return entryStart <= ordered[ordered.length - 1].month && entryEnd >= ordered[0].month;
    });
    const durationMonths = planting.duration_months ?? ordered.length;
    const plantingDate = savedEntry
      ? new Date(`${savedEntry.planting_date}T00:00:00Z`)
      : monthDate(year, planting.plant_month ?? ordered[0].month);
    const harvestDate = savedEntry
      ? new Date(`${savedEntry.harvest_date}T00:00:00Z`)
      : monthDate(year, (planting.plant_month ?? ordered[0].month) + durationMonths - 1);
    return {
      seasonId,
      cropName: planting.crop_name!,
      startMonth: ordered[0].month,
      endMonth: ordered[ordered.length - 1].month,
      score: scoreItem.suitability_index,
      reason: planting.reason ?? (planting.saved ? 'Saved in your farm calendar' : 'Strong seasonal match'),
      saved: ordered.some((item) => item.saved) || Boolean(savedEntry),
      continuesNextYear: ordered.some((item) => item.continues_next_year),
      durationMonths,
      plantingDate,
      harvestDate,
    };
  });
  const plannedCrops = cycles.length;
  const harvests = timeline.filter((item) => item.stage === 'harvest').length;
  const plannedMonths = new Set(timeline.map((item) => item.month)).size;
  const today = new Date();
  const currentMonth = today.getMonth() + 1;
  const datedCycles: CropCycleView[] = cycles.map((cycle) => ({
    ...cycle,
    status: cycle.saved
      ? today < cycle.plantingDate
        ? 'upcoming'
        : today <= cycle.harvestDate
          ? 'current'
          : 'completed'
      : 'upcoming',
  }));
  const statusCycle = datedCycles.find((cycle) => cycle.status === 'current')
    ?? datedCycles.find((cycle) => cycle.status === 'upcoming' && cycle.harvestDate >= today)
    ?? null;
  const selectedCycle = datedCycles.find((cycle) => activeMonth >= cycle.startMonth && activeMonth <= cycle.endMonth) ?? null;
  const statusHeading = statusCycle?.status === 'current' ? 'Currently growing' : 'Next crop';
  const statusAction = statusCycle?.status === 'current'
    ? today.getTime() === statusCycle.harvestDate.getTime()
      ? `Harvest ${statusCycle.cropName}`
      : `Monitor ${statusCycle.cropName} growth`
    : statusCycle
      ? `Prepare the field for ${statusCycle.cropName}`
      : 'Keep monitoring the field';
  const selectedScore = selectedCycle?.score ?? selectedActivity?.suitability_index;
  const selectedHarvestDate = selectedCycle?.harvestDate ?? null;
  const cycleHref = (seasonId: string, startMonth: number) =>
    `/app/farms/${farmId}/annual-plan?season=${encodeURIComponent(seasonId)}&month=${startMonth}`;

  if (requestedSeason) {
    if (plan.isPending || cropPlan.isPending) {
      return <div className="decision-page annual-planner-page"><Skeleton className="h-96 w-full rounded-2xl" /></div>;
    }
    if (plan.isError) {
      return <div className="decision-page annual-planner-page"><ApiErrorState error={plan.error} onRetry={() => void plan.refetch()} /></div>;
    }
    if (cropPlan.isError) {
      return <div className="decision-page annual-planner-page"><ApiErrorState error={cropPlan.error} onRetry={() => void cropPlan.refetch()} /></div>;
    }
    const growingCycle = datedCycles.find((cycle) => cycle.seasonId === requestedSeason);
    if (!growingCycle || !plan.data) {
      return <div className="decision-page annual-planner-page growing-plan-not-found"><h1>{t('plan.notFound')}</h1><Link href={`/app/farms/${farmId}/annual-plan`}><ArrowLeft /> {t('plan.back')}</Link></div>;
    }
    const orderedCycles = [...datedCycles].sort((a, b) => a.plantingDate.getTime() - b.plantingDate.getTime());
    const cycleIndex = orderedCycles.findIndex((cycle) => cycle.seasonId === growingCycle.seasonId);
    const nextCycle = cycleIndex >= 0 ? orderedCycles[cycleIndex + 1] ?? null : null;
    return <GrowingPlan farmId={farmId} cycle={growingCycle} nextCycle={nextCycle} risk={plan.data.selected_month.main_risk} onNavigateCycle={setMonth} />;
  }

  return (
    <div className="decision-page annual-planner-page content-stack">
      <Link className="back-link" href={`/app/farms/${farmId}/twin`}>
        <ArrowLeft /> {t('plan.backFarm')}
      </Link>
      <header className="page-heading decision-heading">
        <div>
          <p className="section-kicker">{plan.data?.farm_name ?? t('plan.kicker')}</p>
          <h1>{t('plan.title')}</h1>
          <p>{t('plan.intro')}</p>
        </div>
      </header>

      {cropPlan.data && (
        <ProposalDialog
          farmId={farmId}
          proposals={cropPlan.data.proposals}
          entries={cropPlan.data.entries}
          onAccepted={invalidatePlan}
        />
      )}

      {plan.isPending && (
        <div className="decision-loading">
          <Skeleton className="h-40 w-full rounded-2xl" />
          <Skeleton className="h-96 w-full rounded-2xl" />
        </div>
      )}
      {plan.isError && (
        <ApiErrorState error={plan.error} onRetry={() => void plan.refetch()} />
      )}
      {plan.data && (
        <>
          <details className="planner-secondary-tools">
            <summary>{t('plan.climateWhatIf')}</summary>
            <ScenarioControls
              rainfall={draftRainfall}
              temperature={draftTemperature}
              irrigationMm={draftIrrigationMm}
              busy={plan.isFetching || scenarioBusy}
              onRainfallChange={setDraftRainfall}
              onTemperatureChange={setDraftTemperature}
              onIrrigationChange={setDraftIrrigationMm}
              onApply={() => void apply()}
              onReset={reset}
            />
          </details>

          {cropPlan.isPending && <Skeleton className="h-96 w-full rounded-2xl" />}
          {cropPlan.isError && <ApiErrorState error={cropPlan.error} onRetry={() => void cropPlan.refetch()} />}
          {cropPlan.data && (
            <>
              <div className="planner-sequence-summary" aria-label={t('plan.summary')}>
                <span><strong>🌱 {plannedCrops}</strong>{plannedCrops === 1 ? t('plan.cropPlanned') : t('plan.cropsPlanned')}</span>
                <span><strong>🌾 {harvests}</strong>{t('plan.harvests')}</span>
                <span><strong>📅 {plannedMonths}/12</strong>{plannedMonths === 12 ? t('plan.fullYear') : t('plan.monthsCovered')}</span>
              </div>
              {statusCycle && (
                <section className="planner-current-status" aria-label={t('plan.currentStatus')}>
                  <div>
                    <span>{statusHeading}</span>
                    <strong>{statusCycle.cropName}{statusCycle.saved ? <em>{t('plan.saved')}</em> : null}</strong>
                    <small>{statusCycle.status === 'current' ? t('plan.started') : t('plan.plant')}: {formatMonthYear(statusCycle.plantingDate)}</small>
                    <small>Expected harvest: {formatMonthYear(statusCycle.harvestDate)}</small>
                  </div>
                  <div>
                    <span>{t('plan.nextAction')}</span>
                    <strong>{statusAction}</strong>
                    {statusCycle.status === 'current' && <small>{t('plan.expectedHarvest')}: {formatMonthYear(statusCycle.harvestDate)}</small>}
                  </div>
                </section>
              )}
              <div className="annual-plan-workspace">
                <section className="annual-months-grid" aria-labelledby="year-title">
                  <div className="section-heading-row">
                    <div>
                      <p className="section-kicker">{t('plan.calendar')}</p>
                      <h2 id="year-title">{t('plan.sequence')}</h2>
                    </div>
                    <p>{t('plan.sequenceHelp')}</p>
                  </div>
                  {cycles.length === 0 ? (
                    <div className="planner-sequence-empty">{t('plan.sequenceUnavailable')}</div>
                  ) : (
                    <>
                    <div className="planner-year-timeline workspace-card">
                      <div className="planner-year-months">
                        {localizedMonths.map((name, index) => <span key={name} data-current={index + 1 === currentMonth || undefined}>{name.slice(0, 3)}{index + 1 === currentMonth && <small>{t('plan.today')}</small>}</span>)}
                      </div>
                      <div className="planner-year-cycles">
                      {datedCycles.map((cycle) => (
                        <button
                          key={cycle.seasonId}
                          type="button"
                          className="planner-cycle-block"
                          data-active={month >= cycle.startMonth && month <= cycle.endMonth || undefined}
                          data-saved={cycle.saved || undefined}
                          style={{ gridColumn: `${cycle.startMonth} / span ${cycle.endMonth - cycle.startMonth + 1}` }}
                          onClick={() => setMonth(cycle.startMonth)}
                          aria-pressed={month >= cycle.startMonth && month <= cycle.endMonth}
                          aria-label={`${cycle.cropName}, ${MONTH_NAMES[cycle.startMonth - 1]} to ${MONTH_NAMES[cycle.endMonth - 1]}`}
                        >
                          <CropVisual cropName={cycle.cropName} />
                          <span><strong>{cycle.cropName}</strong><small>{formatMonthYear(cycle.plantingDate, true)} → {formatMonthYear(cycle.harvestDate, true)}</small><small>{cycle.score ?? '—'}% match</small></span>
                          {cycle.harvestDate.getUTCFullYear() > year && <b aria-label={`Continues into ${cycle.harvestDate.getUTCFullYear()}`}>→</b>}
                          {cycle.saved && <CheckCircle aria-label={t('plan.savedEntry')} />}
                        </button>
                      ))}
                      </div>
                    </div>
                    <section className="planner-crops-section" aria-labelledby="your-crops-title">
                      <div><h2 id="your-crops-title">{t('plan.yourCrops')}</h2><p>{t('plan.cyclesHelp')}</p></div>
                    <div className="planner-crop-cards">
                      {datedCycles.map((cycle) => (
                        <article className="workspace-card planner-crop-summary" key={`card-${cycle.seasonId}`}>
                          <CropVisual cropName={cycle.cropName} />
                          <div><strong>{cycle.cropName}</strong><span>{formatMonthYear(cycle.plantingDate)} – {formatMonthYear(cycle.harvestDate)}</span></div>
                          <b>{cycle.score ?? '—'}% match</b>
                          <p>{cycle.reason}</p>
                          <Link href={cycleHref(cycle.seasonId, cycle.startMonth)} onClick={() => setMonth(cycle.startMonth)}>View {cycle.cropName} plan →</Link>
                        </article>
                      ))}
                    </div>
                    </section>
                    </>
                  )}
                </section>

                <aside className="workspace-card planner-month-detail" aria-labelledby="month-detail-title">
                  <div className="planner-detail-hero">
                    <div className="planner-detail-visual-large"><CropVisual cropName={selectedActivity?.crop_name ?? ''} /></div>
                    <div className="planner-detail-header">
                      <p className="planner-detail-month-label">{selectedActivity?.month_name ?? plan.data.selected_month.month}</p>
                      <h2 id="month-detail-title" className="planner-detail-crop-title">{selectedActivity?.crop_name ?? t('plan.fieldRecovery')}</h2>
                      {selectedActivity && <span className="planner-stage-badge" data-stage={selectedActivity.stage}>{selectedActivity.stage}</span>}
                      {selectedScore !== null && selectedScore !== undefined && <div className="planner-detail-score"><span className="planner-score-value">{selectedScore}%</span><span className="planner-score-label">{t('plan.match')}</span></div>}
                    </div>
                  </div>
                  <div className="planner-metrics-grid">
                    <div className="planner-metric-item"><CloudRain className="planner-metric-icon" /><div className="planner-metric-content"><small>{t('plan.expectedRainfall')}</small><strong>{plan.data.selected_month.expected_rainfall_mm} mm</strong></div></div>
                    <div className="planner-metric-item"><ThermometerSun className="planner-metric-icon" /><div className="planner-metric-content"><small>{t('plan.expectedTemperature')}</small><strong>{plan.data.selected_month.expected_temperature_c}°C</strong></div></div>
                    <div className="planner-metric-item"><CalendarDays className="planner-metric-icon" /><div className="planner-metric-content"><small>{t('plan.expectedHarvest')}</small><strong>{selectedHarvestDate ? formatMonthYear(selectedHarvestDate, true) : '—'}</strong></div></div>
                    <div className="planner-metric-item"><TriangleAlert className="planner-metric-icon" /><div className="planner-metric-content"><small>{t('plan.mainRisk')}</small><strong>{farmerFriendlyRisk(plan.data.selected_month.main_risk)}</strong></div></div>
                    {selectedActivity?.duration_months && <div className="planner-metric-item"><CalendarDays className="planner-metric-icon" /><div className="planner-metric-content"><small>{t('plan.growingTime')}</small><strong>{t('plan.months', { count: selectedActivity.duration_months })}</strong></div></div>}
                    {selectedActivity?.previous_crop && <div className="planner-metric-item"><Info className="planner-metric-icon" /><div className="planner-metric-content"><small>{t('plan.previousCrop')}</small><strong>{selectedActivity.previous_crop}</strong></div></div>}
                  </div>
                  {selectedActivity?.reason && <div className="planner-tip-card"><Info className="planner-tip-icon" /><div className="planner-tip-content"><strong>{t('plan.whyNow')}</strong><p>{selectedActivity.reason}</p></div></div>}
                  {selectedActivity?.stage === 'planting' && selectedActivity.crop_name && (
                    <Dialog open={addToPlanOpen && addToPlanCrop !== null} onOpenChange={(open) => { if (!open) setAddToPlanOpen(false); }}>
                      <DialogTrigger render={<Button variant="default" className="planner-add-button" aria-label={t('plan.addCrop', { crop: selectedActivity.crop_name })} onClick={() => openAddToPlan(selectedActivity.crop_name!, selectedActivity.month, selectedActivity.month_name)} />}><Plus /> {t('plan.add')}</DialogTrigger>
                      {addToPlanCrop && <DialogContent showCloseButton={false}><DialogHeader><DialogTitle>{t('plan.add')}</DialogTitle><DialogDescription>{t('plan.confirm', { crop: addToPlanCrop.name, month: addToPlanCrop.monthName })}</DialogDescription></DialogHeader><AddToPlanForm farmId={farmId} cropName={addToPlanCrop.name} monthNumber={addToPlanCrop.monthNumber} monthName={addToPlanCrop.monthName} onSuccess={() => { setAddToPlanOpen(false); invalidatePlan(); }} onClose={() => setAddToPlanOpen(false)} /></DialogContent>}
                    </Dialog>
                  )}
                </aside>
              </div>

              <details className="workspace-card planner-why-plan">
                <summary><span>💡</span><strong>{t('plan.why')}</strong><small>{cropPlan.data.explanation ?? t('plan.defaultWhy')}</small></summary>
                <div><span>✓ Strong crop matches</span><span>✓ No calendar conflicts</span><span>✓ Better crop variety</span><span>⚠ Weather and water remain the main constraints to watch</span></div>
              </details>

              {cropPlan.data.perennial_opportunities.length > 0 && (
                <section className="workspace-card planner-perennial-section" aria-labelledby="perennial-title">
                  <p className="section-kicker">{t('plan.longTerm')}</p>
                  <h2 id="perennial-title">{t('plan.beyondRotation')}</h2>
                  <p>{t('plan.longTermHelp')}</p>
                  <div>{cropPlan.data.perennial_opportunities.map((item) => <article key={item.crop_name}><CropVisual cropName={item.crop_name} /><strong>{item.crop_name}</strong><span>{item.suitability_index}% land suitability</span></article>)}</div>
                </section>
              )}
            </>
          )}

          {(scenarioResult || scenario.rainfall !== 0 || scenario.temperature !== 0 || scenario.irrigation !== null) && (
          <section
            className="workspace-card comparison-card"
            aria-labelledby="comparison-title"
          >
            <div className="card-heading-row">
              <div>
                <p className="section-kicker">{t('plan.beforeAfter')}</p>
                <h2 id="comparison-title">
                  Scenario impact in {plan.data.selected_month.month}
                </h2>
              </div>
              <span>
                {scenario.rainfall}% rain ·{' '}
                {scenario.temperature > 0 ? '+' : ''}
                {scenario.temperature}°C
              </span>
            </div>
            <div className="comparison-table-wrap">
              <table className="comparison-table">
                <caption className="sr-only">
                  Baseline and climate scenario crop scores
                </caption>
                <thead>
                  <tr>
                    <th>{t('crops.crop')}</th>
                    <th>{t('crops.baseline')}</th>
                    <th>{t('crops.scenario')}</th>
                    <th>{t('crops.change')}</th>
                  </tr>
                </thead>
                <tbody>
                  {scenarioResult
                    ? scenarioResult.crops.map((item) => {
                        const delta = item.scenario_index - item.baseline_index;
                        return (
                          <tr key={item.crop_name}>
                            <td>{item.crop_name}</td>
                            <td>
                              {item.baseline_index}
                              <small className="comparison-label"> {item.baseline_label}</small>
                            </td>
                            <td>
                              {item.scenario_index}
                              <small className="comparison-label"> {item.scenario_label}</small>
                            </td>
                            <td>
                              <b
                                data-direction={
                                  delta === 0 ? 'same' : delta > 0 ? 'up' : 'down'
                                }
                              >
                                {delta > 0 ? '+' : ''}
                                {delta}
                              </b>
                            </td>
                          </tr>
                        );
                      })
                    : plan.data.comparison.map((item) => (
                        <tr key={item.crop}>
                          <td>{item.crop}</td>
                          <td>{item.baseline_score}</td>
                          <td>{item.scenario_score}</td>
                          <td>
                            <b
                              data-direction={
                                item.delta === 0
                                  ? 'same'
                                  : item.delta > 0
                                    ? 'up'
                                    : 'down'
                              }
                            >
                              {item.delta > 0 ? '+' : ''}
                              {item.delta}
                            </b>
                          </td>
                        </tr>
                      ))}
                </tbody>
              </table>
            </div>
            {scenarioResult && scenarioResult.hazards.length > 0 && (
              <div className="comparison-hazards">
                <h3>{t('crops.hazardLevels')}</h3>
                <table className="comparison-table">
                  <caption className="sr-only">
                    Baseline and scenario hazard levels
                  </caption>
                  <thead>
                    <tr>
                      <th>{t('crops.hazard')}</th>
                      <th>{t('crops.baseline')}</th>
                      <th>{t('crops.scenario')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scenarioResult.hazards.map((h) => (
                      <tr key={h.hazard}>
                        <td>{h.hazard}</td>
                        <td>
                          <span data-level={h.baseline_level.toLowerCase()}>
                            {h.baseline_level}
                          </span>
                        </td>
                        <td>
                          <span data-level={h.scenario_level.toLowerCase()}>
                            {h.scenario_level}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          )}
        </>
      )}

      {/* Task 5.3: My Schedule panel */}
      {cropPlan.data && cropPlan.data.entries.length > 0 && (
        <section
          className="workspace-card schedule-panel"
          aria-labelledby="schedule-title"
        >
          <div className="card-heading-row">
            <div>
              <p className="section-kicker">{t('plan.savedSchedule')}</p>
              <h2 id="schedule-title" className="schedule-heading">
                My Schedule
              </h2>
            </div>
            <span className="count-badge">
              {cropPlan.data.entries.length} entr
              {cropPlan.data.entries.length !== 1 ? 'ies' : 'y'}
            </span>
          </div>
          <ul className="schedule-list" aria-label={t('plan.savedEntries')}>
            {cropPlan.data.entries.map((entry) => (
              <li key={entry.id} className="schedule-row">
                <div className="schedule-row-main">
                  <strong>{entry.crop_name}</strong>
                  <span className="schedule-dates">
                    <CalendarDays aria-hidden="true" />
                    {entry.planting_date} → {entry.harvest_date}
                  </span>
                  <span className="schedule-mode">
                    {entry.cultivation_mode === 'irrigated' ? 'Irrigated' : 'Rain-fed'}
                  </span>
                </div>
                <div className="schedule-row-meta">
                  <span className="schedule-area">{entry.area_ha} ha</span>
                  <span className="schedule-score">
                    Suitability: <b>{entry.suitability_index}</b>
                  </span>
                  <DeleteEntryDialog
                    farmId={farmId}
                    entryId={entry.id}
                    cropName={entry.crop_name}
                    onDeleted={invalidatePlan}
                  />
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

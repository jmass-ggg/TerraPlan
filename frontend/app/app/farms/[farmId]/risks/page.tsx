'use client';

import {
  CalendarDays,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  Circle,
  Database,
  Search,
  ShieldCheck,
} from 'lucide-react';
import Image, { type StaticImageData } from 'next/image';
import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
} from 'recharts';

import droughtImage from '../../../../../photos/drought.png';
import floodImage from '../../../../../photos/flood.png';
import heatImage from '../../../../../photos/Heat.png';
import rainfallImage from '../../../../../photos/rainfall.png';
import windImage from '../../../../../photos/wind.png';

import { ApiErrorState } from '@/components/api-state';
import { Skeleton } from '@/components/ui/skeleton';
import {
  type ActionRule,
  type HazardAssessment,
  type RiskResponse,
  type RiskTimelineResponse,
  RiskApiError,
  completeAction,
  getFarmRisks,
  getFarmRiskTimeline,
} from '@/lib/api/risks';
import { useI18n } from '@/lib/i18n/context';
import type { TranslationKey } from '@/lib/i18n/translations';

const HAZARD_ORDER = ['flood_exposure', 'drought', 'heat', 'heavy_rainfall', 'wind'];
const HAZARD_IMAGES: Record<string, StaticImageData> = {
  flood_exposure: floodImage,
  drought: droughtImage,
  heat: heatImage,
  heavy_rainfall: rainfallImage,
  wind: windImage,
};

function hazardKey(hazard: string): TranslationKey | null {
  const labels: Record<string, TranslationKey> = {
    drought: 'risk.drought',
    heat: 'risk.heat',
    heavy_rainfall: 'risk.rainfall',
    flood_exposure: 'risk.flood',
    wind: 'risk.wind',
  };
  return labels[hazard] ?? null;
}

function horizonKey(horizon: string): TranslationKey | null {
  const labels: Record<string, TranslationKey> = {
    current: 'risk.current',
    short_term: 'risk.next7',
    seasonal: 'risk.seasonal',
  };
  return labels[horizon] ?? null;
}

function orderedAssessments(assessments: HazardAssessment[]): HazardAssessment[] {
  return [...assessments].sort((left, right) => {
    const leftIndex = HAZARD_ORDER.indexOf(left.hazard);
    const rightIndex = HAZARD_ORDER.indexOf(right.hazard);
    return (leftIndex === -1 ? HAZARD_ORDER.length : leftIndex) -
      (rightIndex === -1 ? HAZARD_ORDER.length : rightIndex);
  });
}

function HazardImage({ hazard, size = 24 }: { hazard: string; size?: number }) {
  return (
    <Image
      src={HAZARD_IMAGES[hazard] ?? HAZARD_IMAGES.flood_exposure}
      alt=""
      width={size}
      height={size}
      className="risk-hazard-image"
    />
  );
}

function LevelBadge({ level }: { level: HazardAssessment['level'] }) {
  const { t } = useI18n();
  return (
    <b className="risk-level-badge" data-level={level.toLowerCase()} aria-label={t('risk.level', { level })}>
      {level}
    </b>
  );
}

interface ActionRowProps {
  action: ActionRule;
  farmId: string;
  onComplete: (actionId: string, completedAt: string) => void;
}

function ActionRow({ action, farmId, onComplete }: ActionRowProps) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);

  const handleToggle = useCallback(async () => {
    if (action.completed || busy) return;
    setBusy(true);
    try {
      const result = await completeAction(farmId, action.id);
      onComplete(action.id, result.completed_at);
    } catch {
      // Keep the action available when the request fails.
    } finally {
      setBusy(false);
    }
  }, [action.completed, action.id, busy, farmId, onComplete]);

  return (
    <li className="risk-action-row" data-completed={action.completed || undefined}>
      <button
        type="button"
        className="action-checkbox"
        aria-label={action.completed ? t('risk.completed', { date: action.id }) : t('risk.markComplete', { id: action.id })}
        aria-pressed={action.completed}
        disabled={action.completed || busy}
        onClick={() => void handleToggle()}
      >
        {action.completed ? <CheckCircle2 aria-hidden="true" /> : <Circle aria-hidden="true" />}
      </button>
      <div className="action-content">
        <div className="action-meta">
          <ShieldCheck className="risk-action-icon" aria-hidden="true" />
          <span className="action-priority" aria-label={t('risk.priority', { priority: action.priority })}>P{action.priority}</span>
          {action.completed && action.completed_at && (
            <span className="action-completed-label">
              {t('risk.completed', { date: new Date(action.completed_at).toLocaleDateString() })}
            </span>
          )}
        </div>
        <p className="action-text">{action.text}</p>
        <small className="action-source">{t('risk.sourceReviewed', { source: action.source, date: action.review_date })}</small>
      </div>
    </li>
  );
}

function HazardDetailPanel({ assessment }: { assessment: HazardAssessment }) {
  const { t } = useI18n();
  const label = hazardKey(assessment.hazard) ? t(hazardKey(assessment.hazard)!) : assessment.hazard.replace(/_/g, ' ');
  const evidenceEntries = Object.entries(assessment.evidence_used);
  const hasEvidence = assessment.level !== 'Unknown';

  return (
    <section className="risk-detail-panel workspace-card" aria-label={label}>
      <div className="risk-detail-header">
        <div className="risk-detail-title">
          <span className="risk-detail-hazard-icon" data-hazard={assessment.hazard}>
            <HazardImage hazard={assessment.hazard} size={26} />
          </span>
          <div>
            <p className="section-kicker">{t('risk.selectedHazard')}</p>
            <h2>{label}</h2>
          </div>
        </div>
        <LevelBadge level={assessment.level} />
      </div>

      <p className="risk-detail-context">{t('risk.currentAssessment')} · {assessment.data_mode.replace(/_/g, ' ')}</p>

      <div className="risk-detail-metrics">
        {hasEvidence ? (
          <div className="risk-detail-severity">
            <span className="risk-metric-label">{t('risk.severity')}</span>
            <div className="risk-detail-index">
              <span className="risk-index-value" aria-label={t('risk.severityIndex', { index: assessment.index })}>
                {assessment.index}
              </span>
              <span className="risk-index-label">/ 100</span>
            </div>
            <div className="risk-severity-scale" aria-label={t('risk.severityOutOf', { index: assessment.index })}>
              <i data-level={assessment.level.toLowerCase()} style={{ width: `${assessment.index}%` }} />
            </div>
          </div>
        ) : (
          <div className="risk-insufficient-evidence">
            <Database aria-hidden="true" />
            <span><strong>{t('risk.insufficient')}</strong>{t('risk.notSafe')}</span>
          </div>
        )}

        <dl className="risk-detail-facts">
          <div><dt>{t('risk.horizon')}</dt><dd>{horizonKey(assessment.horizon) ? t(horizonKey(assessment.horizon)!) : assessment.horizon.replace(/_/g, ' ')}</dd></div>
          <div><dt>{t('risk.driver')}</dt><dd>{assessment.driver.replace(/_/g, ' ')}</dd></div>
        </dl>
      </div>

      <div className="risk-why-section">
        <h3>{t('risk.why')}</h3>
        {evidenceEntries.length > 0 ? (
          <dl className="risk-evidence-list">
            {evidenceEntries.map(([field, status]) => (
              <div key={field} className="risk-evidence-item" data-status={status}>
                <dt>{field.replace(/_/g, ' ')}</dt><dd>{status}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="risk-crops-empty">{t('risk.noEvidence')}</p>
        )}
      </div>

      {assessment.at_risk_crops.length > 0 && (
        <div className="risk-detail-section">
          <strong>{t('risk.atRiskCrops')}</strong>
          <ul className="risk-crops-list">
            {assessment.at_risk_crops.map((crop) => <li key={crop}>{crop}</li>)}
          </ul>
        </div>
      )}

      <div className="risk-observations">
        <h3>{t('risk.observations')}</h3>
        <p>{assessment.explanation}</p>
      </div>

      <details className="risk-threshold-note">
        <summary>{t('risk.calculated')}</summary>
        <p>{assessment.explanation}</p>
        <span>{t('risk.indexNote')}</span>
      </details>
    </section>
  );
}

interface HazardCardProps {
  assessment: HazardAssessment;
  selected: boolean;
  onClick: () => void;
}

function HazardCard({ assessment, selected, onClick }: HazardCardProps) {
  const { t } = useI18n();
  const hasEvidence = assessment.level !== 'Unknown';
  const label = hazardKey(assessment.hazard) ? t(hazardKey(assessment.hazard)!) : assessment.hazard.replace(/_/g, ' ');

  return (
    <button
      type="button"
      className="workspace-card risk-card risk-center-card"
      data-hazard={assessment.hazard}
      data-level={assessment.level.toLowerCase()}
      data-selected={selected || undefined}
      aria-pressed={selected}
      aria-label={t('risk.cardAria', { hazard: label, level: assessment.level })}
      onClick={onClick}
    >
      <div className="risk-card-heading">
        <h2>{label}</h2>
        <span className="risk-card-icon"><HazardImage hazard={assessment.hazard} size={25} /></span>
      </div>
      <div className="risk-card-score-row">
        <strong>{hasEvidence ? assessment.index : '—'}</strong>
        {hasEvidence && <small>/ 100</small>}
        <LevelBadge level={assessment.level} />
      </div>
      {hasEvidence ? (
        <span className="risk-progress" aria-label={t('risk.severityOutOf', { index: assessment.index })}>
          <i style={{ width: `${assessment.index}%` }} />
        </span>
      ) : (
        <span className="risk-card-unknown">{t('risk.insufficient')}</span>
      )}
      <p className="risk-card-driver">
        {hasEvidence ? t('risk.driverLabel', { driver: assessment.driver.replace(/_/g, ' ') }) : t('risk.moreEvidence')}
      </p>
    </button>
  );
}

export default function RiskCenterPage() {
  const { farmId } = useParams<{ farmId: string }>();
  const router = useRouter();
  const { language, t } = useI18n();
  const [risks, setRisks] = useState<RiskResponse | null>(null);
  const [timeline, setTimeline] = useState<RiskTimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [timelineError, setTimelineError] = useState<Error | null>(null);
  const [selectedHazard, setSelectedHazard] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timelineAbortRef = useRef<AbortController | null>(null);

  const loadRisks = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getFarmRisks(farmId, signal);
      setRisks(data);
      const firstAssessment = orderedAssessments(data.assessments)[0];
      setSelectedHazard((current) =>
        current && data.assessments.some((assessment) => assessment.hazard === current)
          ? current
          : firstAssessment?.hazard ?? null,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if (err instanceof RiskApiError && err.status === 404) {
        router.push('/app');
        return;
      }
      setError(err instanceof Error ? err : new Error(t('api.couldNotLoad')));
    } finally {
      setLoading(false);
    }
  }, [farmId, router, t]);

  useEffect(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    queueMicrotask(() => void loadRisks(controller.signal));
    return () => controller.abort();
  }, [loadRisks]);

  const loadTimeline = useCallback(async (signal: AbortSignal) => {
    setTimelineLoading(true);
    setTimelineError(null);
    try {
      const data = await getFarmRiskTimeline(farmId, 7, signal);
      setTimeline(data);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setTimelineError(err instanceof Error ? err : new Error(t('api.couldNotLoad')));
    } finally {
      setTimelineLoading(false);
    }
  }, [farmId, t]);

  useEffect(() => {
    timelineAbortRef.current?.abort();
    const controller = new AbortController();
    timelineAbortRef.current = controller;
    queueMicrotask(() => void loadTimeline(controller.signal));
    return () => controller.abort();
  }, [loadTimeline]);

  const handleActionComplete = useCallback((hazard: string, actionId: string, completedAt: string) => {
    setRisks((previous) => {
      if (!previous) return previous;
      return {
        ...previous,
        assessments: previous.assessments.map((assessment) =>
          assessment.hazard === hazard
            ? {
                ...assessment,
                actions: assessment.actions.map((action) =>
                  action.id === actionId ? { ...action, completed: true, completed_at: completedAt } : action,
                ),
              }
            : assessment,
        ),
      };
    });
  }, []);

  const assessments = risks ? orderedAssessments(risks.assessments) : [];
  const selectedAssessment = assessments.find((assessment) => assessment.hazard === selectedHazard) ?? null;

  return (
    <div className="decision-page content-stack risk-center-page">
      <div className="risk-topbar">
        <label className="risk-topbar-search">
          <Search aria-hidden="true" />
          <span className="sr-only">{t('risk.search')}</span>
          <input type="search" placeholder={t('risk.searchPlaceholder')} />
        </label>
        {selectedAssessment && (
          <button type="button" className="risk-period-control" aria-label={t('risk.period')}>
            <CalendarDays aria-hidden="true" />{horizonKey(selectedAssessment.horizon) ? t(horizonKey(selectedAssessment.horizon)!) : selectedAssessment.horizon.replace(/_/g, ' ')}<ChevronDown aria-hidden="true" />
          </button>
        )}
      </div>
      <header className="page-heading decision-heading risk-page-heading">
        <div>
          <h1>{t('risk.title')}</h1>
          <p>{t('risk.intro')}</p>
        </div>
      </header>

      {loading && (
        <div className="decision-loading">
          <div className="risk-center-grid">
            {Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-36 w-full rounded-xl" />)}
          </div>
          <Skeleton className="h-96 w-full rounded-xl" />
        </div>
      )}

      {!loading && error && (
        <ApiErrorState
          error={error}
          onRetry={() => {
            abortRef.current?.abort();
            const controller = new AbortController();
            abortRef.current = controller;
            void loadRisks(controller.signal);
          }}
        />
      )}

      {!loading && risks && (
        <>
          <section className="risk-center-grid" aria-label={t('risk.assessments')}>
            {assessments.map((assessment) => (
              <HazardCard
                key={assessment.hazard}
                assessment={assessment}
                selected={selectedHazard === assessment.hazard}
                onClick={() => setSelectedHazard(assessment.hazard)}
              />
            ))}
          </section>

          {selectedAssessment && (
            <section className="risk-overview-shell workspace-card" aria-labelledby="risk-overview-title">
              <div className="risk-overview-shell-heading">
                <h2 id="risk-overview-title">{t('risk.overview')}</h2>
                <p>{t('risk.overviewIntro')}</p>
              </div>
              <div className="risk-overview-layout">
                <div className="risk-overview-list" aria-label={t('risk.selectHazard')}>
                  {assessments.map((assessment) => (
                    <button
                      key={assessment.hazard}
                      type="button"
                      data-selected={assessment.hazard === selectedHazard || undefined}
                      onClick={() => setSelectedHazard(assessment.hazard)}
                    >
                      <HazardImage hazard={assessment.hazard} size={19} />
                      <span>{hazardKey(assessment.hazard) ? t(hazardKey(assessment.hazard)!) : assessment.hazard.replace(/_/g, ' ')}</span>
                      {assessment.level === 'Unknown' ? (
                        <i className="risk-overview-unknown">{t('risk.insufficient')}</i>
                      ) : (
                        <i><b data-level={assessment.level.toLowerCase()} style={{ width: `${assessment.index}%` }} /></i>
                      )}
                      <strong data-level={assessment.level.toLowerCase()}>{assessment.level}</strong>
                    </button>
                  ))}
                </div>
                <HazardDetailPanel assessment={selectedAssessment} />
              </div>
            </section>
          )}

          <section className="risk-comparison-card risk-timeline-card workspace-card" aria-labelledby="risk-comparison-title">
            <div className="risk-visual-heading">
              <div><h2 id="risk-comparison-title">{t('risk.timeline')}</h2><p>{t('risk.timelineIntro')}</p></div>
            </div>
            {timelineLoading && <Skeleton className="h-80 w-full rounded" />}
            {!timelineLoading && timelineError && (
              <output className="risk-timeline-unavailable">
                <CalendarClock aria-hidden="true" />
                <div>
                  <strong>{t('risk.timelineUnavailable')}</strong>
                  <p>{timelineError.message}</p>
                </div>
              </output>
            )}
            {!timelineLoading && !timelineError && timeline && (
              <div style={{ width: '100%', height: 400 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={timeline.points.map((point) => ({
                      date: new Date(point.date).toLocaleDateString(language === 'sw' ? 'sw-KE' : 'en-KE', { month: 'short', day: 'numeric' }),
                      'Heavy Rainfall': point.heavy_rainfall.index,
                      'Heat Stress': point.heat.index,
                      Drought: point.drought.index,
                      Flood: point.flood_exposure.index,
                      Wind: point.wind.index,
                    }))}
                    margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="date" />
                    <YAxis domain={[0, 100]} label={{ value: t('risk.severity'), angle: -90, position: 'insideLeft' }} />
                    <Tooltip
                      formatter={(value) => (value !== null && value !== undefined ? `${value} / 100` : t('common.unavailable'))}
                      labelStyle={{ color: '#000' }}
                    />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="Heavy Rainfall"
                      name={t('risk.rainfall')}
                      stroke="#ef4444"
                      strokeWidth={2}
                      dot={{ r: 4 }}
                      connectNulls={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="Heat Stress"
                      name={t('risk.heat')}
                      stroke="#f97316"
                      strokeWidth={2}
                      dot={{ r: 4 }}
                      connectNulls={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="Drought"
                      name={t('risk.drought')}
                      stroke="#d97706"
                      strokeWidth={2}
                      dot={{ r: 4 }}
                      connectNulls={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="Flood"
                      name={t('risk.flood')}
                      stroke="#3b82f6"
                      strokeWidth={2}
                      dot={{ r: 4 }}
                      connectNulls={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="Wind"
                      name={t('risk.wind')}
                      stroke="#10b981"
                      strokeWidth={2}
                      dot={{ r: 4 }}
                      connectNulls={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </section>

          <section className="risk-recommended-actions workspace-card" aria-labelledby="risk-actions-title">
            <div className="risk-actions-heading">
              <div><h2 id="risk-actions-title">{t('risk.recommended')}</h2><p>{t('risk.recommendedIntro')}</p></div>
              {selectedAssessment && <LevelBadge level={selectedAssessment.level} />}
            </div>
            {selectedAssessment && selectedAssessment.actions.length > 0 ? (
              <ul className="risk-actions-board">
                {selectedAssessment.actions.map((action) => (
                  <ActionRow
                    key={action.id}
                    action={action}
                    farmId={farmId}
                    onComplete={(actionId, completedAt) =>
                      handleActionComplete(selectedAssessment.hazard, actionId, completedAt)
                    }
                  />
                ))}
              </ul>
            ) : (
              <div className="risk-no-actions">
                <ShieldCheck aria-hidden="true" />
                <span><strong>{t('risk.noUrgent')}</strong>{t('risk.noUrgentHelp')}</span>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

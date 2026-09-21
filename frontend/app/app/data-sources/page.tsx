'use client';

import {
  AlertTriangle,
  CalendarClock,
  Database,
  History,
  Info,
  MapPin,
  RadioTower,
  Scale,
  Workflow,
} from 'lucide-react';

import { ApiErrorState } from '@/components/api-state';
import { Skeleton } from '@/components/ui/skeleton';
import { useApiResource } from '@/hooks/use-api-resource';
import { farmTwinApi } from '@/lib/api/client';
import type { DataSourceRecord } from '@/lib/api/types';
import { useI18n } from '@/lib/i18n/context';

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

// "Not configured" uses a distinct amber/warning colour;
// "unavailable" uses a red/error colour — both different from the
// default grey used for other non-active states.
function statusDataAttr(status: string): string {
  // Pass through as-is; CSS uses data-status attribute selectors
  return status;
}

// ---------------------------------------------------------------------------
// Pipeline explanation panel (Conduit only)
// ---------------------------------------------------------------------------

function PipelineExplanation({ text }: { text: string }) {
  const { t } = useI18n();
  return (
    <div className="source-pipeline-explanation" role="note" aria-label={t('sources.flowTitle')}>
      <span className="source-pipeline-icon" aria-hidden="true">
        <Workflow />
      </span>
      <div>
        <p className="section-kicker">{t('sources.howFlows')}</p>
        <p>{text}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Individual source card
// ---------------------------------------------------------------------------

function SourceCard({ source }: { source: DataSourceRecord }) {
  const { t } = useI18n();
  const isNotConfigured = source.status === 'not_configured';
  const isUnavailable = source.status === 'unavailable';
  const localizedStatus = source.status === 'active' ? t('sources.statusActive')
    : source.status === 'pending' ? t('sources.statusPending')
    : source.status === 'not_configured' ? t('sources.statusNotConfigured')
    : source.status === 'unavailable' ? t('common.unavailable')
    : source.status === 'empty' ? t('sources.statusNoRecords')
    : source.status.replace(/_/g, ' ');

  return (
    <article
      className="workspace-card source-detail-card"
      data-status={source.status}
      aria-label={`${source.name} data source`}
    >
      {/* Header row */}
      <div className="source-detail-header">
        <span className="source-icon" aria-hidden="true">
          <Database />
        </span>
        <div className="source-detail-title">
          <h3>{source.name.charAt(0).toUpperCase() + source.name.slice(1)}</h3>
          <span
            className="source-status"
            data-status={statusDataAttr(source.status)}
          >
            {isNotConfigured && (
              <Info aria-hidden="true" style={{ width: 11, marginRight: 4 }} />
            )}
            {isUnavailable && (
              <AlertTriangle
                aria-hidden="true"
                style={{ width: 11, marginRight: 4 }}
              />
            )}
            {localizedStatus}
          </span>
        </div>
      </div>

      {/* Description */}
      {source.description && (
        <p className="source-detail-description">{source.description}</p>
      )}

      {/* Metadata grid */}
      <dl className="source-detail-meta">
        {source.resolution && (
          <div className="source-meta-item">
            <dt>
              <MapPin aria-hidden="true" />
              {t('sources.resolution')}
            </dt>
            <dd>{source.resolution}</dd>
          </div>
        )}
        {source.spatial_extent && (
          <div className="source-meta-item">
            <dt>
              <MapPin aria-hidden="true" />
              {t('sources.extent')}
            </dt>
            <dd>{source.spatial_extent}</dd>
          </div>
        )}
        <div className="source-meta-item">
          <dt>
            <Database aria-hidden="true" />
            {t('sources.mode')}
          </dt>
          <dd>{source.data_mode.replace(/_/g, ' ')}</dd>
        </div>
        <div className="source-meta-item">
          <dt>
            <CalendarClock aria-hidden="true" />
            {t('sources.lastIngestion')}
          </dt>
          <dd>
            {source.last_ingestion_time
              ? new Date(source.last_ingestion_time).toLocaleString()
              : '—'}
          </dd>
        </div>
        {source.record_count > 0 && (
          <div className="source-meta-item">
            <dt>
              <Database aria-hidden="true" />
              {t('sources.records')}
            </dt>
            <dd>{t('sources.normalised', { count: source.record_count.toLocaleString() })}</dd>
          </div>
        )}
      </dl>

      {/* License note */}
      {source.license_note && (
        <div className="source-license-note" role="note">
          <Scale aria-hidden="true" />
          <span>{source.license_note}</span>
        </div>
      )}

      {/* Not-configured explanation */}
      {isNotConfigured && (
        <p className="source-state-note source-state-note--not-configured">
          {t('sources.notConfiguredHelp')}
        </p>
      )}

      {/* Unavailable explanation */}
      {isUnavailable && (
        <p className="source-state-note source-state-note--unavailable">
          {t('sources.unavailableHelp')}
        </p>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DataSourcesPage() {
  const sources = useApiResource(farmTwinApi.listDataSources);
  const { t } = useI18n();

  // Find the Conduit entry for the pipeline explanation section
  const conduitEntry =
    sources.status === 'success'
      ? sources.result.data.sources.find((s) => s.name === 'conduit')
      : undefined;

  return (
    <div className="content-stack">
      <header className="page-heading">
        <div>
          <p className="section-kicker">{t('sources.evidence')}</p>
          <h1>{t('sources.title')}</h1>
          <p>
            {t('sources.intro')}
          </p>
        </div>
      </header>

      {/* Data integrity principles */}
      <div className="source-principles" aria-label={t('sources.principles')}>
        <div>
          <History />
          <span>
            <strong>{t('sources.historical')}</strong>
            <small>
              {t('sources.historicalHelp')}
            </small>
          </span>
        </div>
        <div>
          <RadioTower />
          <span>
            <strong>{t('sources.distance')}</strong>
            <small>
              {t('sources.distanceHelp')}
            </small>
          </span>
        </div>
        <div>
          <Database />
          <span>
            <strong>{t('sources.missing')}</strong>
            <small>
              {t('sources.missingHelp')}
            </small>
          </span>
        </div>
      </div>

      {/* Loading state */}
      {sources.status === 'loading' && (
        <div
          className="source-detail-grid"
          aria-busy="true"
          aria-label={t('sources.loading')}
        >
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div className="workspace-card source-detail-card" key={i}>
              <Skeleton className="h-10 w-10 rounded-xl" />
              <Skeleton className="h-5 w-40 mt-3" />
              <Skeleton className="h-4 w-full mt-2" />
              <Skeleton className="h-4 w-3/4 mt-1" />
            </div>
          ))}
        </div>
      )}

      {/* Error state */}
      {sources.status === 'error' && (
        <ApiErrorState error={sources.error} onRetry={sources.retry} />
      )}

      {/* Success state */}
      {sources.status === 'success' && (
        <>
          {/* Source count summary */}
          <div className="source-summary-row">
            <p className="section-kicker">
              {t('sources.providers', { count: sources.result.data.sources.length })}
            </p>
            <span className="count-badge">
              {t('sources.active', {
                count: sources.result.data.sources.filter((s) => s.status === 'active').length,
              })}
            </span>
          </div>

          {/* Provider cards grid */}
          {sources.result.data.sources.length === 0 ? (
            <p className="muted-copy">{t('sources.none')}</p>
          ) : (
            <div className="source-detail-grid">
              {sources.result.data.sources.map((source) => (
                <SourceCard key={source.name} source={source} />
              ))}
            </div>
          )}

          {/* Conduit pipeline explanation */}
          {conduitEntry?.pipeline_explanation && (
            <section
              className="workspace-card source-table-card"
              aria-labelledby="pipeline-heading"
            >
              <div className="card-heading-row">
                <div>
                  <p className="section-kicker">Conduit</p>
                  <h2 id="pipeline-heading">
                    {t('sources.flowTitle')}
                  </h2>
                </div>
              </div>
              <PipelineExplanation
                text={conduitEntry.pipeline_explanation}
              />
            </section>
          )}

          <p className="source-footnote">
            <CalendarClock />
            {t('sources.timezone')}
          </p>
        </>
      )}
    </div>
  );
}

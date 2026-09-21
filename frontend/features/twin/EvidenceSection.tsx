'use client';

import { ChevronDown, ChevronUp } from 'lucide-react';
import { useState } from 'react';

import type { EnvironmentalValue } from '@/lib/api/farms';
import { useI18n } from '@/lib/i18n/context';

type EvidenceStatus = string;

interface EvidenceSectionProps {
  title: string;
  status: EvidenceStatus;
  values: EnvironmentalValue[];
  children?: React.ReactNode;
}

function statusPillClass(status: EvidenceStatus): string {
  if (status === 'accepted') return 'evidence-pill evidence-pill-accepted';
  if (status === 'stale') return 'evidence-pill evidence-pill-stale';
  return 'evidence-pill evidence-pill-unavailable';
}

function formatAcquiredAt(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  } catch {
    return iso;
  }
}

export function EvidenceSection({
  title,
  status,
  values,
  children,
}: EvidenceSectionProps) {
  const { t } = useI18n();
  const [provenanceOpen, setProvenanceOpen] = useState(false);
  const localizedStatus = status === 'accepted' ? t('evidence.live')
    : status === 'stale' ? t('evidence.stale')
    : status === 'unavailable' ? t('common.unavailable')
    : status === 'error' ? t('evidence.error')
    : status === 'ineligible' ? t('evidence.ineligible')
    : status;

  return (
    <article className="evidence-section workspace-card" data-status={status}>
      <div className="evidence-section-header">
        <h2 className="evidence-section-title">{title}</h2>
        <span
          className={statusPillClass(status)}
          aria-label={t('evidence.statusAria', { title, status: localizedStatus })}
        >
          {localizedStatus}
        </span>
      </div>

      {status !== 'accepted' && status !== 'stale' ? (
        <p className="evidence-unavailable-note">
          {status === 'unavailable'
            ? t('evidence.unavailable')
            : status === 'ineligible'
              ? t('evidence.ineligibleHelp')
              : t('evidence.errorHelp')}
        </p>
      ) : (
        <>
          {values.length > 0 && (
            <ul className="evidence-value-list" aria-label={t('evidence.values', { title })}>
              {values.map((v, index) => (
                <li
                  key={`${v.unit}-${index}`}
                  className="evidence-value-row"
                  data-quality={v.quality}
                >
                  <span className="evidence-value-number">
                    {v.value !== null ? v.value.toLocaleString() : '—'}
                  </span>
                  <span className="evidence-value-unit">{v.unit}</span>
                  <span className="evidence-value-source">{v.source}</span>
                  <span className="evidence-value-mode evidence-mode-badge">
                    {v.data_mode}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {children}
          {values.length > 0 && (
            <details
              className="evidence-provenance"
              open={provenanceOpen}
              onToggle={(e) =>
                setProvenanceOpen((e.currentTarget as HTMLDetailsElement).open)
              }
            >
              <summary className="evidence-provenance-toggle">
                {provenanceOpen ? (
                  <ChevronUp aria-hidden="true" />
                ) : (
                  <ChevronDown aria-hidden="true" />
                )}
                {t('evidence.details')}
              </summary>
              <table className="evidence-provenance-table">
                <thead>
                  <tr>
                    <th scope="col">{t('climate.source')}</th>
                    <th scope="col">{t('climate.acquired')}</th>
                    <th scope="col">{t('climate.retrieved')}</th>
                    <th scope="col">{t('climate.mode')}</th>
                    <th scope="col">{t('climate.quality')}</th>
                    <th scope="col">{t('evidence.resolution')}</th>
                  </tr>
                </thead>
                <tbody>
                  {values.map((v, index) => (
                    <tr key={`prov-${v.unit}-${index}`}>
                      <td>{v.source}</td>
                      <td>{formatAcquiredAt(v.acquired_at)}</td>
                      <td>{formatAcquiredAt(v.retrieved_at)}</td>
                      <td>
                        <span className="evidence-mode-badge">{v.data_mode}</span>
                      </td>
                      <td>{v.quality}</td>
                      <td>
                        {v.resolution_m !== null ? `${v.resolution_m} m` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}
    </article>
  );
}

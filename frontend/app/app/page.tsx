'use client';

import Link from 'next/link';
import { ArrowRight, Leaf, Lock, MapPin } from 'lucide-react';

import { ApiErrorState } from '@/components/api-state';
import { Skeleton } from '@/components/ui/skeleton';
import { useApiResource } from '@/hooks/use-api-resource';
import { farmTwinApi } from '@/lib/api/client';
import { useI18n } from '@/lib/i18n/context';

export default function OverviewPage() {
  const farms = useApiResource(farmTwinApi.listFarms);
  const { t } = useI18n();

  return (
    <div className="welcome-page">
      {/* Map background placeholder */}
      <div className="welcome-map-bg" aria-hidden="true" />

      {farms.status === 'loading' && (
        <div className="welcome-card">
          <Skeleton className="h-10 w-10 rounded-full mx-auto mb-4" />
          <Skeleton className="h-7 w-48 mx-auto mb-2" />
          <Skeleton className="h-4 w-64 mx-auto mb-6" />
          <Skeleton className="h-12 w-56 mx-auto rounded-full" />
        </div>
      )}

      {farms.status === 'error' && (
        <div className="welcome-card">
          <ApiErrorState error={farms.error} onRetry={farms.retry} />
        </div>
      )}

      {farms.status === 'success' && farms.result.data.total === 0 && (
        <div className="welcome-card">
          <div className="welcome-pin-icon" aria-hidden="true">
            <MapPin />
          </div>
          <h1 className="welcome-title">{t('overview.welcome')}</h1>
          <p className="welcome-desc">
            {t('overview.start')}
          </p>
          <Link href="/app/farms/new" className="welcome-cta-btn">
            <MapPin aria-hidden="true" />
            {t('overview.createTwin')}
          </Link>
          <p className="welcome-lock-note">
            <Lock size={12} aria-hidden="true" />
            {t('overview.locked')}
          </p>
        </div>
      )}

      {farms.status === 'success' && farms.result.data.total > 0 && (
        <div className="welcome-card">
          <div className="welcome-pin-icon" aria-hidden="true">
            <Leaf />
          </div>
          <h1 className="welcome-title">{t('overview.yourFarms')}</h1>
          <p className="welcome-desc">{t('overview.selectFarm')}</p>
          <div className="welcome-farm-list">
            {farms.result.data.items.map((farm) => (
              <Link key={farm.id} href={`/app/farms/${farm.id}/twin`} className="welcome-farm-row">
                <span>
                  <strong>{farm.name}</strong>
                  <small>{farm.hectares.toLocaleString()} ha</small>
                </span>
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            ))}
          </div>
          <Link href="/app/farms/new" className="welcome-add-farm">
            {t('overview.addFarm')}
          </Link>
        </div>
      )}
    </div>
  );
}

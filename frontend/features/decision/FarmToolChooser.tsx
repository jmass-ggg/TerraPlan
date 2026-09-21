'use client';

import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  CloudSun,
  FlaskConical,
  MapPinned,
  ShieldAlert,
} from 'lucide-react';
import Link from 'next/link';

import { ApiErrorState } from '@/components/api-state';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useApiResource } from '@/hooks/use-api-resource';
import { farmTwinApi } from '@/lib/api/client';
import { useI18n } from '@/lib/i18n/context';

const toolConfig = {
  'annual-plan': {
    Icon: CalendarDays,
    route: 'annual-plan',
    headingKey: 'chooser.annual' as const,
  },
  'disaster-center': {
    Icon: ShieldAlert,
    route: 'risks',
    headingKey: 'chooser.risks' as const,
  },
  'crop-simulator': {
    Icon: FlaskConical,
    route: 'crops',
    headingKey: 'chooser.crops' as const,
  },
  climate: {
    Icon: CloudSun,
    route: 'crops',
    headingKey: 'chooser.climate' as const,
  },
} as const;

export function FarmToolChooser({
  tool,
}: {
  tool: 'annual-plan' | 'disaster-center' | 'crop-simulator' | 'climate';
}) {
  const farms = useApiResource(farmTwinApi.listFarms);
  const { t } = useI18n();
  const { Icon, route, headingKey } = toolConfig[tool];
  return (
    <div className="narrow-content">
      <Link className="back-link" href="/app">
        <ArrowLeft /> {t('common.backOverview')}
      </Link>
      <section className="workspace-card tool-chooser">
        <span className="unavailable-icon">
          <Icon />
        </span>
        <p className="section-kicker">{t('chooser.kicker')}</p>
        <h1>
          {t(headingKey)}
        </h1>
        <p className="page-lede">
          {t('chooser.intro')}
        </p>
        {farms.status === 'loading' && (
          <div className="chooser-loading">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        )}
        {farms.status === 'error' && (
          <ApiErrorState error={farms.error} onRetry={farms.retry} />
        )}
        {farms.status === 'success' && farms.result.data.items.length === 0 && (
          <Button
            render={<Link href="/app/farms/new" />}
            className="primary-button"
          >
            <MapPinned /> {t('chooser.create')}
          </Button>
        )}
        {farms.status === 'success' && farms.result.data.items.length > 0 && (
          <div className="chooser-list">
            {farms.result.data.items.map((farm) => (
              <Link href={`/app/farms/${farm.id}/${route}`} key={farm.id}>
                <span>
                  <strong>{farm.name}</strong>
                  <small>{farm.hectares.toLocaleString()} {t('common.hectares')}</small>
                </span>
                <ArrowRight />
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

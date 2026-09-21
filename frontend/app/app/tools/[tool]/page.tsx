import Link from 'next/link';
import {
  ArrowLeft,
  CalendarDays,
  CloudSun,
  FlaskConical,
  LockKeyhole,
  Map,
  ShieldAlert,
} from 'lucide-react';
import { notFound, useParams } from 'next/navigation';

import { Button } from '@/components/ui/button';
import { FarmToolChooser } from '@/features/decision/FarmToolChooser';
import { useI18n } from '@/lib/i18n/context';

const tools = {
  'digital-twin': {
    nameKey: 'tools.digitalTwin' as const,
    icon: Map,
    phase: 'Phase 5',
    status: 'coming-soon' as const,
    prerequisiteKey: 'tools.digitalTwinPrereq' as const,
  },
  'crop-simulator': {
    nameKey: 'tools.cropSimulator' as const,
    icon: FlaskConical,
    phase: 'Phase 6',
    status: 'farm-required' as const,
    prerequisiteKey: 'tools.cropPrereq' as const,
  },
  'annual-plan': {
    nameKey: 'tools.annualPlan' as const,
    icon: CalendarDays,
    phase: 'Phase 8',
    status: 'farm-required' as const,
    prerequisiteKey: 'tools.annualPrereq' as const,
  },
  'disaster-center': {
    nameKey: 'tools.disasterCenter' as const,
    icon: ShieldAlert,
    phase: 'Phase 7',
    status: 'farm-required' as const,
    prerequisiteKey: 'tools.riskPrereq' as const,
  },
  climate: {
    nameKey: 'tools.climateScenarios' as const,
    icon: CloudSun,
    phase: 'Phase 9',
    status: 'farm-required' as const,
    prerequisiteKey: 'tools.climatePrereq' as const,
  },
} as const;

export default function ToolPrerequisitePage() {
  const { tool } = useParams<{ tool: string }>();
  const { t } = useI18n();
  if (tool === 'annual-plan' || tool === 'disaster-center' || tool === 'crop-simulator' || tool === 'climate') {
    return <FarmToolChooser tool={tool} />;
  }
  const details = tools[tool as keyof typeof tools];
  if (!details) notFound();
  const Icon = details.icon;

  if (details.status === 'coming-soon') {
    return (
      <div className="narrow-content">
        <Link className="back-link" href="/app">
          <ArrowLeft /> {t('common.backOverview')}
        </Link>
        <section className="workspace-card unavailable-page">
          <span className="unavailable-icon">
            <Icon />
          </span>
          <p className="section-kicker">{details.phase} · {t('tools.comingSoon')}</p>
          <h1>{t(details.nameKey)}</h1>
          <p className="page-lede">{t(details.prerequisiteKey)}</p>
          <div className="prerequisite-notice">
            <LockKeyhole />
            <span>
              <strong>{t('tools.notImplemented')}</strong> {t('tools.roadmapHelp')}
            </span>
          </div>
          <div className="inline-actions">
            <Button variant="outline" render={<Link href="/app/project" />}>
              {t('tools.viewRoadmap')}
            </Button>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="narrow-content">
      <Link className="back-link" href="/app">
        <ArrowLeft /> {t('common.backOverview')}
      </Link>
      <section className="workspace-card unavailable-page">
        <span className="unavailable-icon">
          <Icon />
        </span>
        <p className="section-kicker">{details.phase}</p>
        <h1>{t('tools.needsFarm', { name: t(details.nameKey) })}</h1>
        <p className="page-lede">{t(details.prerequisiteKey)}</p>
        <div className="prerequisite-notice">
          <LockKeyhole />
          <span>
            <strong>{t('tools.whyLocked')}</strong> {t('tools.lockedHelp')}
          </span>
        </div>
        <div className="inline-actions">
          <Button
            render={<Link href="/app/farms/new" />}
            className="primary-button"
          >
            {t('tools.startFarm')}
          </Button>
          <Button variant="outline" render={<Link href="/app/project" />}>
            {t('tools.viewRoadmap')}
          </Button>
        </div>
      </section>
    </div>
  );
}
'use client';

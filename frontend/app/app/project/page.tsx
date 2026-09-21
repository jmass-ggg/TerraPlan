'use client';

import {
  Check,
  CircleDashed,
  ClipboardCopy,
  GitBranch,
  Layers3,
  LockKeyhole,
  Route,
} from 'lucide-react';
import { useState } from 'react';
import { useI18n } from '@/lib/i18n/context';

const phases = [
  {
    number: 0,
    name: 'Audit and contract freeze',
    status: 'Verified',
    note: 'Architecture, boundaries, source register, and API contract recorded. Build log gate passed September 5, 2026.',
  },
  {
    number: 1,
    name: 'Backend and database foundation',
    status: 'Verified',
    note: 'Configuration, auth boundary, ownership, PostGIS, and read APIs tested. 182 core tests pass. Runtime role gaps documented and resolved.',
  },
  {
    number: 2,
    name: 'Conduit pipeline',
    status: 'Verified',
    note: '327 tests pass. Historical fixture ingestion, normalization, deduplication, aggregation, provenance, and endpoints implemented. DATA_MODE=live guard verified.',
  },
  {
    number: 3,
    name: 'Frontend foundation',
    status: 'Verified',
    note: 'Next.js 15, TypeScript, MapLibre, shadcn/ui design system, app shell, and farm-tool routing. 355 combined backend + frontend tests pass.',
  },
  {
    number: 4,
    name: 'Farm selection and persistence',
    status: 'Verified',
    note: 'Global search, polygon drawing, validation, geodesic area, geometry revisions, ownership isolation, idempotent creation, and optimistic concurrency all verified. 355 tests pass.',
  },
  {
    number: 5,
    name: 'Environmental context and twin',
    status: 'Verified',
    note: '378 backend + 11 frontend tests pass. Provider adapters for weather, satellite, soil, terrain, climate, and Conduit eligibility. Unavailable sources return null — never zeros.',
  },
  {
    number: 6,
    name: 'Crop suitability and simulator',
    status: 'Verified',
    note: '388 backend + 18 frontend tests pass. 12-crop register, deterministic scoring engine, hard exclusion, snapshot-backed context, demonstration fallback, and cross-user isolation verified.',
  },
  {
    number: 7,
    name: 'Hazards and actions',
    status: 'Verified',
    note: 'Drought, heat, heavy-rainfall, wind hazard screening with action recommendations. Action completions persist. Flood explicitly unknown without terrain evidence. Build log gate passed September 8, 2026.',
  },
  {
    number: 8,
    name: 'Annual planner',
    status: 'Verified',
    note: 'Persistent planting schedule, overlap validation, change proposals, and My Schedule panel. Reuses deterministic crop-scoring engine. Build log gate passed September 8, 2026.',
  },
  {
    number: 9,
    name: 'Climate views and scenarios',
    status: 'Verified',
    note: '420 backend + 32 frontend tests pass. Rainfall and temperature scenarios recompute crop rankings and hazard levels without mutating the baseline snapshot. Zero-delta identity verified by property test.',
  },
  {
    number: 10,
    name: 'Supporting pages and connected behavior',
    status: 'In Progress',
    note: 'Data Sources, Settings, and Project & Architecture pages being completed. Dead controls being audited and removed.',
  },
  {
    number: 11,
    name: 'Validation and release preparation',
    status: 'Planned',
    note: 'Security review, accessibility audit, recovery testing, and end-to-end release gates.',
  },
  {
    number: 12,
    name: 'Production deployment',
    status: 'Planned',
    note: 'Only after release gates pass and the project owner authorizes the target environment.',
  },
];

const techStack = [
  {
    category: 'Frontend runtime',
    items: [
      {
        tool: 'React 19 + TypeScript 5.9',
        role: 'UI rendering and strict typing across all pages',
      },
      {
        tool: 'Vinext / Next.js-compatible',
        role: 'File-system routing and server components via Sites hosting',
      },
      { tool: 'Tailwind CSS 4', role: 'Design tokens and utility-first styling' },
      { tool: 'shadcn/ui + Base UI', role: 'Accessible component primitives' },
      {
        tool: 'TanStack Query 5',
        role: 'Server-state cache, background refetch, and stale-data management',
      },
      {
        tool: 'MapLibre GL JS 5',
        role: 'Interactive farm boundary map and evidence layer rendering',
      },
      { tool: 'Recharts 3', role: 'Climate charts, suitability bars, and scenario comparisons' },
      { tool: 'Lucide React', role: 'Consistent icon set across all pages' },
    ],
  },
  {
    category: 'Frontend tooling',
    items: [
      { tool: 'Vitest 3 + Testing Library', role: 'Component and hook unit tests' },
      { tool: 'oxlint + oxfmt', role: 'Fast linting and formatting' },
      { tool: 'Wrangler + Cloudflare Workers', role: 'Edge runtime and local dev server' },
    ],
  },
  {
    category: 'Backend runtime',
    items: [
      {
        tool: 'Python 3.12 + FastAPI',
        role: 'Versioned HTTP API with Pydantic v2 request/response validation',
      },
      {
        tool: 'SQLAlchemy 2 + asyncpg',
        role: 'Async ORM with ownership-scoped repositories',
      },
      {
        tool: 'Alembic',
        role: 'Transactional schema migrations with named constraints',
      },
      {
        tool: 'GeoAlchemy2 + Shapely + PyProj',
        role: 'Geometry validation, geodesic area, and PostGIS integration',
      },
      {
        tool: 'Hypothesis',
        role: 'Property-based testing for engines, parsers, and provenance invariants',
      },
    ],
  },
  {
    category: 'Data and infrastructure',
    items: [
      {
        tool: 'PostgreSQL 16 + PostGIS 3.4',
        role: 'Transactional farm, snapshot, plan, and observation storage with spatial queries',
      },
      {
        tool: 'Redis 7',
        role: 'Analysis job queue consumed by the async worker process',
      },
      {
        tool: 'Docker + Compose',
        role: 'Reproducible local environment for database, API, and worker',
      },
      {
        tool: 'httpx',
        role: 'Bounded async HTTP client for all provider adapter calls',
      },
    ],
  },
  {
    category: 'External data providers',
    items: [
      {
        tool: 'Conduit',
        role: 'IoT weather station observations ingested as historical fixture; live endpoint pending credentials',
      },
      {
        tool: 'Open-Meteo',
        role: 'Current weather forecasts and 30-year historical climate archive',
      },
      {
        tool: 'Copernicus / Sentinel-2',
        role: 'Satellite NDVI and NDMI for vegetation and moisture context',
      },
      { tool: 'SoilGrids', role: 'Global soil pH, clay, and sand fraction estimates' },
      { tool: 'Copernicus DEM GLO-30', role: 'Terrain elevation and slope analysis' },
    ],
  },
];

export default function ProjectPage() {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      const res = await fetch('/project_build.md');
      const text = await res.text();
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: navigate to the file
      window.open('/project_build.md', '_blank');
    }
  }

  return (
    <div className="content-stack project-page">
      <header className="page-heading project-heading">
        <div>
          <p className="section-kicker">{t('project.kicker')}</p>
          <h1>{t('project.title')}</h1>
          <p>
            {t('project.intro')}
          </p>
        </div>
        <button
          className="copy-plan-btn"
          onClick={handleCopy}
          aria-label={t('project.copyAria')}
          title={t('project.copyTitle')}
        >
          <ClipboardCopy aria-hidden="true" />
          {copied ? t('project.copied') : t('project.copy')}
        </button>
      </header>

      <section
        className="workspace-card architecture-card"
        aria-labelledby="system-flow"
      >
        <div className="card-heading-row">
          <div>
            <p className="section-kicker">{t('project.systemFlow')}</p>
            <h2 id="system-flow">{t('project.evidenceFirst')}</h2>
          </div>
          <GitBranch />
        </div>
        <div
          className="architecture-flow"
          aria-label={t('project.flowAria')}
        >
          {[
            t('project.boundary'),
            t('project.environmentalEvidence'),
            t('project.versionedTwin'),
            t('project.supportedDecisions'),
          ].map((label, index) => (
            <div className="flow-step" key={label}>
              <span>{index + 1}</span>
              <strong>{label}</strong>
              {index < 3 && <Route aria-hidden="true" />}
            </div>
          ))}
        </div>
        <p className="architecture-note">
          <Layers3 /> {t('project.languageNote')}
        </p>
      </section>

      <section aria-labelledby="roadmap-title">
        <div className="section-heading-row">
          <div>
            <p className="section-kicker">{t('project.roadmap')}</p>
            <h2 id="roadmap-title">{t('project.phases')}</h2>
          </div>
          <p>{t('project.verifiedHelp')}</p>
        </div>
        <div className="phase-list">
          {phases.map((phase) => (
            <article
              className="phase-row"
              key={phase.number}
              data-status={phase.status.toLowerCase().replace(' ', '-')}
            >
              <span className="phase-number">{String(phase.number).padStart(2, '0')}</span>
              <div>
                <h3>{phase.name}</h3>
                <p>{phase.note}</p>
              </div>
              <span className="phase-status">
                {phase.status === 'Verified' ? (
                  <Check />
                ) : phase.status === 'Implemented' || phase.status === 'In Progress' ? (
                  <CircleDashed />
                ) : (
                  <LockKeyhole />
                )}
                {phase.status}
              </span>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="tech-stack-title">
        <div className="section-heading-row">
          <div>
            <p className="section-kicker">{t('project.stack')}</p>
            <h2 id="tech-stack-title">{t('project.toolsRoles')}</h2>
          </div>
          <p>{t('project.bounded')}</p>
        </div>
        <div className="tech-stack-list">
          {techStack.map((group) => (
            <div className="tech-group" key={group.category}>
              <h3 className="tech-group-title">{group.category}</h3>
              <ul className="tech-items">
                {group.items.map((item) => (
                  <li className="tech-item" key={item.tool}>
                    <strong className="tech-tool">{item.tool}</strong>
                    <span className="tech-role">{item.role}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

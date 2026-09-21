'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import {
  CalendarDays,
  CloudSun,
  Database,
  FlaskConical,
  FolderKanban,
  Home,
  LockKeyhole,
  Map,
  Menu,
  Settings,
  ShieldAlert,
} from 'lucide-react';

import { Brand } from '@/components/brand';
import { LanguageSwitcher } from '@/components/language-switcher';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import { useI18n } from '@/lib/i18n/context';

function Navigation({ close }: { close?: () => void }) {
  const pathname = usePathname();
  const { t } = useI18n();
  const farmId = pathname.match(/^\/app\/farms\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\/|$)/i)?.[1];
  const farmBase = farmId ? `/app/farms/${farmId}` : null;
  const primaryNavigation = [
    { href: '/app', label: t('nav.overview'), icon: Home, available: true },
    { href: farmBase ? `${farmBase}/twin` : '#', label: t('nav.landIntelligence'), icon: Map, available: Boolean(farmBase) },
    { href: farmBase ? `${farmBase}/crops` : '#', label: t('nav.cropSuitability'), icon: FlaskConical, available: Boolean(farmBase) },
    { href: farmBase ? `${farmBase}/annual-plan` : '#', label: t('nav.seasonalPlan'), icon: CalendarDays, available: Boolean(farmBase) },
    { href: farmBase ? `${farmBase}/risks` : '#', label: t('nav.environmentalRisk'), icon: ShieldAlert, available: Boolean(farmBase) },
    { href: farmBase ? `${farmBase}/climate` : '#', label: t('nav.climateIntelligence'), icon: CloudSun, available: Boolean(farmBase) },
  ];
  const secondaryNavigation = [
    { href: '/app/data-sources', label: t('nav.environmentalSources'), icon: Database },
    { href: '/app/settings', label: t('nav.settings'), icon: Settings },
    { href: '/app/project', label: t('nav.projectArchitecture'), icon: FolderKanban },
  ];
  return (
    <nav className="app-navigation" aria-label={t('nav.workspace')}>
      <div className="nav-group">
        <p className="nav-label">{t('nav.workspace')}</p>
        {primaryNavigation.map(({ href, label, icon: Icon, available }) => {
          const active =
            href === '/app'
              ? pathname === href
              : pathname.startsWith(href) ||
                (href.endsWith('annual-plan') &&
                  pathname.includes('/annual-plan')) ||
                (href.endsWith('disaster-center') &&
                  pathname.includes('/risks')) ||
                (href.endsWith('crop-simulator') &&
                  pathname.includes('/crops')) ||
                (href.endsWith('/climate') &&
                  pathname.includes('/climate'));
          if (!available) {
            return (
              <span
                key={label}
                className="nav-item nav-item-unavailable"
                aria-disabled="true"
              >
                <Icon aria-hidden="true" />
                <span className="nav-item-text">
                  {label}
                  <small className="nav-coming-soon">{t('nav.selectFarmFirst')}</small>
                </span>
                <LockKeyhole className="nav-lock" aria-label={t('nav.selectFarmFirst')} />
              </span>
            );
          }
          return (
            <Link
              key={href}
              href={href}
              onClick={close}
              className="nav-item"
              data-active={active || undefined}
              aria-current={active ? 'page' : undefined}
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </Link>
          );
        })}
      </div>
      <div className="nav-group nav-group-secondary">
        <p className="nav-label">{t('nav.system')}</p>
        {secondaryNavigation.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              onClick={close}
              className="nav-item"
              data-active={active || undefined}
              aria-current={active ? 'page' : undefined}
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { t } = useI18n();
  return (
    <div className="app-shell">
      <aside className="desktop-sidebar">
        <div className="sidebar-brand">
          <Brand />
        </div>
        <Navigation />
        <div className="sidebar-foot">
          <div className="sidebar-foot-brand">
            <p>{t('shell.betterDecisions')}</p>
            <p>{t('shell.healthierFarms')}</p>
            <p>{t('shell.greenerKenya')}</p>
          </div>
        </div>
      </aside>

      <div className="app-frame">
        <header className="app-header">
          <div className="mobile-brand">
            <Brand />
          </div>
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger
              render={
                <Button
                  variant="outline"
                  size="icon-lg"
                  className="app-menu-button"
                  aria-label={t('nav.open')}
                />
              }
            >
              <Menu />
            </SheetTrigger>
            <SheetContent side="left" className="mobile-sheet">
              <SheetHeader>
                <SheetTitle>
                  <Brand />
                </SheetTitle>
                <SheetDescription>{t('nav.description')}</SheetDescription>
              </SheetHeader>
              <Navigation close={() => setMobileOpen(false)} />
            </SheetContent>
          </Sheet>
          <div className="app-language-switcher">
            <LanguageSwitcher />
          </div>
        </header>
        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}

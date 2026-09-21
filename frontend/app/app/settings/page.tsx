'use client';

import { useEffect, useRef, useState } from 'react';
import { Link2, ShieldCheck, User } from 'lucide-react';

import { farmTwinApi, FarmTwinApiError } from '@/lib/api/client';
import type { UserProfile } from '@/lib/api/types';
import { useI18n } from '@/lib/i18n/context';

// ─── component ────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { t } = useI18n();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [authMode, setAuthMode] = useState<string | null>(null);
  const [backendStatus, setBackendStatus] = useState<'connected' | 'unreachable' | 'checking'>('checking');

  const [displayName, setDisplayName] = useState('');
  const [savedName, setSavedName] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  const hasUnsavedChanges = displayName !== savedName;

  // ── load profile ────────────────────────────────────────────────────────────
  useEffect(() => {
    const ac = new AbortController();
    abortRef.current = ac;

    async function load() {
      // health check
      try {
        await farmTwinApi.getHealth(ac.signal);
        setBackendStatus('connected');
      } catch {
        if (!ac.signal.aborted) setBackendStatus('unreachable');
      }

      // profile
      try {
        const result = await farmTwinApi.getProfile(ac.signal);
        setProfile(result.data);
        setAuthMode(result.authMode);
        const name = result.data.display_name ?? '';
        setDisplayName(name);
        setSavedName(name);
      } catch (err) {
        if (ac.signal.aborted) return;
        // profile fetch failed — auth mode may still come from health headers
        if (err instanceof FarmTwinApiError && err.status === 401) {
          // demo mode without credentials — silently skip profile
        }
      }
    }

    void load();
    return () => ac.abort();
  }, []);

  // ── save handler ────────────────────────────────────────────────────────────
  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      const result = await farmTwinApi.updateProfile({ display_name: displayName || null });
      setProfile(result.data);
      const updated = result.data.display_name ?? '';
      setDisplayName(updated);
      setSavedName(updated);
      setSaveSuccess(true);
    } catch (err) {
      setSaveError(
        err instanceof FarmTwinApiError
          ? err.message
          : t('settings.unexpected'),
      );
    } finally {
      setSaving(false);
    }
  }

  // ─── render ─────────────────────────────────────────────────────────────────
  return (
    <div className="content-stack">
      <header className="page-heading">
        <div>
          <p className="section-kicker">{t('nav.system')}</p>
          <h1>{t('nav.settings')}</h1>
          <p>{t('settings.intro')}</p>
        </div>
      </header>

      <div className="settings-grid">

        {/* ── Profile card ── */}
        <section className="workspace-card settings-card">
          <span className="settings-icon">
            <User />
          </span>
          <div>
            <p className="section-kicker">{t('settings.profile')}</p>
            <h2>{t('settings.displayName')}</h2>
          </div>

          <p>
            {profile
              ? t('settings.signedIn', { identity: profile.email ?? profile.subject })
              : t('settings.loadingProfile')}
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', width: '100%' }}>
            <label htmlFor="display-name" style={{ fontSize: '0.875rem', fontWeight: 500 }}>
              {t('settings.displayName')}
              {hasUnsavedChanges && (
                <span
                  aria-label={t('settings.unsavedChanges')}
                  style={{ marginLeft: '0.5rem', color: 'var(--color-warning, #d97706)', fontSize: '0.75rem' }}
                >
                  {t('settings.unsaved')}
                </span>
              )}
            </label>
            <input
              id="display-name"
              type="text"
              value={displayName}
              onChange={(e) => {
                setDisplayName(e.target.value);
                setSaveSuccess(false);
              }}
              placeholder={t('settings.namePlaceholder')}
              maxLength={255}
              style={{
                padding: '0.5rem 0.75rem',
                borderRadius: '0.375rem',
                border: '1px solid var(--color-border, #d1d5db)',
                background: 'var(--color-input-bg, #fff)',
                fontSize: '0.875rem',
                width: '100%',
              }}
            />
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <button
                onClick={handleSave}
                disabled={saving || !hasUnsavedChanges}
                style={{
                  padding: '0.4rem 1rem',
                  borderRadius: '0.375rem',
                  border: 'none',
                  background: 'var(--color-primary, #16a34a)',
                  color: '#fff',
                  fontSize: '0.875rem',
                  cursor: saving || !hasUnsavedChanges ? 'not-allowed' : 'pointer',
                  opacity: saving || !hasUnsavedChanges ? 0.6 : 1,
                }}
              >
                {saving ? t('common.saving') : t('settings.save')}
              </button>
              {saveSuccess && (
                <span style={{ fontSize: '0.8rem', color: 'var(--color-success, #16a34a)' }}>
                  {t('settings.saved')}
                </span>
              )}
              {saveError && (
                <span style={{ fontSize: '0.8rem', color: 'var(--color-error, #dc2626)' }}>
                  {saveError}
                </span>
              )}
            </div>
          </div>
        </section>

        {/* ── Connection card ── */}
        <section className="workspace-card settings-card">
          <span className="settings-icon">
            <Link2 />
          </span>
          <div>
            <p className="section-kicker">{t('settings.backend')}</p>
            <h2>
              {backendStatus === 'checking'
                ? t('settings.checking')
                : backendStatus === 'connected'
                ? t('settings.connected')
                : t('settings.unreachable')}
            </h2>
          </div>
          <p>
            {backendStatus === 'connected'
              ? t('settings.connectedHelp')
              : backendStatus === 'unreachable'
              ? t('settings.unreachableHelp')
              : t('settings.checkingHelp')}
          </p>
          <span
            className="connection-pill"
            data-connected={backendStatus === 'connected' ? 'true' : 'false'}
          >
            {backendStatus === 'connected'
              ? t('settings.online')
              : backendStatus === 'unreachable'
              ? t('settings.backendUnreachable')
              : t('settings.checking')}
          </span>
        </section>

        {/* ── Auth mode card ── */}
        <section className="workspace-card settings-card">
          <span className="settings-icon">
            <ShieldCheck />
          </span>
          <div>
            <p className="section-kicker">{t('settings.authentication')}</p>
            <h2>{!authMode ? t('settings.unknown') : authMode === 'local_demo' ? t('settings.demo') : authMode === 'oidc' ? t('settings.authenticated') : authMode}</h2>
          </div>
          <p>{authMode === 'local_demo' ? t('settings.demoHelp') : authMode === 'oidc' ? t('settings.authHelp') : t('settings.authUnknown')}</p>
        </section>

      </div>
    </div>
  );
}

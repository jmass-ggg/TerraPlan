'use client';

import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useRef, useState } from 'react';

import { FarmNameInput } from '@/features/farm/FarmNameInput';
import { MapEditor } from '@/features/farm/MapEditor';
import { createFarm, ValidationError, type GeoJSONPolygon } from '@/lib/api/farms';
import { useI18n } from '@/lib/i18n/context';

export default function NewFarmPage() {
  const router = useRouter();
  const { t } = useI18n();
  const idempotencyKey = useRef(crypto.randomUUID());
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [geometryError, setGeometryError] = useState<string | null>(null);
  const [currentGeometry, setCurrentGeometry] = useState<GeoJSONPolygon | null>(null);
  const [isGeometryValid, setIsGeometryValid] = useState(false);
  const [boundaryConfirmed, setBoundaryConfirmed] = useState(false);
  const [hasBoundaryPoints, setHasBoundaryPoints] = useState(false);

  const save = async (geometry: GeoJSONPolygon) => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError(t('createFarm.nameRequired'));
      return;
    }
    if (!geometry) {
      setGeometryError(t('createFarm.boundaryRequired'));
      return;
    }
    if (!boundaryConfirmed) {
      setGeometryError(t('createFarm.confirmRequired'));
      return;
    }
    setSaving(true);
    setNameError(null);
    setGeometryError(null);
    try {
      const farm = await createFarm({
        name: trimmedName,
        geometry,
        idempotency_key: idempotencyKey.current,
      });
      router.push(`/app/farms/${farm.id}/twin`);
    } catch (error) {
      if (error instanceof ValidationError) {
        const nameMessage = error.fieldMessage('name');
        const geometryMessage = error.fieldMessage('geometry');
        setNameError(nameMessage ?? null);
        setGeometryError(geometryMessage ?? (nameMessage ? null : error.message));
      } else {
        setGeometryError(error instanceof Error ? error.message : t('createFarm.failed'));
      }
    } finally {
      setSaving(false);
    }
  };

  const handleSaveClick = () => {
    if (saving) return;
    if (!name.trim()) {
      setNameError(t('createFarm.nameRequired'));
      return;
    }
    if (!currentGeometry || !isGeometryValid) {
      setGeometryError(t('createFarm.boundaryRequired'));
      return;
    }
    if (!boundaryConfirmed) {
      setGeometryError(t('createFarm.confirmRequired'));
      return;
    }
    void save(currentGeometry);
  };

  const handleGeometryChange = useCallback((geometry: GeoJSONPolygon | null, isValid: boolean) => {
    setCurrentGeometry(geometry);
    setIsGeometryValid(isValid);
    if (isValid) setGeometryError(null);
  }, []);

  const canSave = Boolean(name.trim())
    && isGeometryValid
    && currentGeometry !== null
    && boundaryConfirmed
    && !saving;

  return (
    <div className="farm-editor-page">
      <Link className="back-link" href="/app"><ArrowLeft /> {t('common.backOverview')}</Link>
      <header className="editor-page-heading create-farm-heading">
        <div>
          <p className="section-kicker">{t('createFarm.kicker')}</p>
          <h1>{t('createFarm.title')}</h1>
          <p>{t('createFarm.intro')}</p>
        </div>
      </header>
      <FarmNameInput
        value={name}
        onChange={(value) => { setName(value); setNameError(null); }}
        error={nameError}
        onSave={handleSaveClick}
        canSave={canSave}
        isSaving={saving}
      />
      <ol className="farm-create-steps" aria-label={t('createFarm.stepsLabel')}>
        <li data-current={!hasBoundaryPoints || undefined} data-complete={hasBoundaryPoints || undefined}><span>1</span> {t('createFarm.locate')}</li>
        <li data-current={Boolean(hasBoundaryPoints && !currentGeometry) || undefined} data-complete={Boolean(currentGeometry) || undefined}><span>2</span> {t('createFarm.draw')}</li>
        <li data-current={Boolean(currentGeometry && !boundaryConfirmed) || undefined} data-complete={boundaryConfirmed || undefined}><span>3</span> {t('createFarm.confirmAnalyse')}</li>
      </ol>
      <MapEditor
        apiError={geometryError}
        requireBoundaryConfirmation
        hideSaveButton
        onGeometryChange={handleGeometryChange}
        onDrawingStateChange={setHasBoundaryPoints}
        onBoundaryConfirmationChange={setBoundaryConfirmed}
      />
    </div>
  );
}

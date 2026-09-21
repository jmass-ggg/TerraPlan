'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';

import { ApiErrorState } from '@/components/api-state';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { DeleteFarmDialog } from '@/features/farm/DeleteFarmDialog';
import { FarmNameInput } from '@/features/farm/FarmNameInput';
import { MapEditor } from '@/features/farm/MapEditor';
import {
  getFarm,
  StaleRevisionError,
  triggerAnalysis,
  updateFarm,
  ValidationError,
  type FarmResponse,
  type GeoJSONPolygon,
} from '@/lib/api/farms';
import { useI18n } from '@/lib/i18n/context';

function FarmEditWorkspace({
  farmId,
  farm,
  onReload,
}: {
  farmId: string;
  farm: FarmResponse;
  onReload: () => void;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [name, setName] = useState(farm.name);
  const [saving, setSaving] = useState(false);
  const [savingName, setSavingName] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [geometryError, setGeometryError] = useState<string | null>(null);
  const [staleOpen, setStaleOpen] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const currentName = name;

  const saveName = async () => {
    if (currentName.trim() === farm.name) return;
    setSavingName(true);
    setNameError(null);
    try {
      const updated = await updateFarm(farmId, { name: currentName.trim() });
      queryClient.setQueryData(['farm', farmId], updated);
      await queryClient.invalidateQueries({ queryKey: ['farms'] });
      setName(updated.name);
    } catch (error) {
      if (error instanceof ValidationError) {
        setNameError(error.fieldMessage('name') ?? error.message);
      } else {
        setNameError(error instanceof Error ? error.message : t('edit.nameFailed'));
      }
    } finally {
      setSavingName(false);
    }
  };

  const save = async (geometry: GeoJSONPolygon) => {
    setSaving(true);
    setAnalysisError(null);
    setNameError(null);
    setGeometryError(null);
    try {
      const updated = await updateFarm(farmId, {
        name: currentName.trim(),
        geometry,
        expected_revision: farm.current_geometry_revision,
      });
      queryClient.setQueryData(['farm', farmId], updated);
      await queryClient.invalidateQueries({ queryKey: ['farms'] });
      setName(updated.name);
      // Trigger a new analysis job for the updated geometry revision
      try {
        await triggerAnalysis(farmId);
      } catch {
        setAnalysisError(t('edit.savedAnalysisFailed'));
      }
    } catch (error) {
      if (error instanceof StaleRevisionError) {
        setStaleOpen(true);
      } else if (error instanceof ValidationError) {
        setNameError(error.fieldMessage('name') ?? null);
        setGeometryError(error.fieldMessage('geometry') ?? error.message);
      } else {
        setGeometryError(error instanceof Error ? error.message : t('edit.updateFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <header className="editor-page-heading editor-page-heading-compact">
        <div>
          <p className="section-kicker">{t('edit.revision', { revision: farm.current_geometry_revision })}</p>
          <h1>{t('edit.title', { name: farm.name })}</h1>
          <p>{t('edit.instructions')}</p>
        </div>
      </header>
      <FarmNameInput
        value={currentName}
        onChange={(value) => { setName(value); setNameError(null); }}
        error={nameError}
        onSave={() => void saveName()}
        canSave={Boolean(currentName.trim()) && currentName.trim() !== farm.name}
        isSaving={savingName}
        saveLabel={t('edit.saveName')}
      />
      <MapEditor
        key={farm.current_geometry.id}
        initialGeometry={farm.current_geometry.geometry}
        canSave={Boolean(currentName.trim())}
        hasExternalChanges={currentName.trim() !== farm.name}
        isSaving={saving}
        apiError={geometryError}
        onSave={save}
      />
      {analysisError && <div role="alert" className="form-error">
        {analysisError}
        <button type="button" onClick={async () => {
          try { await triggerAnalysis(farmId); setAnalysisError(null); }
          catch { setAnalysisError(t('edit.analysisFailed')); }
        }}>{t('edit.retryAnalysis')}</button>
      </div>}
      <div className="farm-danger-zone">
        <div><strong>{t('edit.deleteFarm')}</strong><p>{t('edit.deleteAvailability')}</p></div>
        <DeleteFarmDialog farmId={farmId} farmName={farm.name} />
      </div>

      <AlertDialog open={staleOpen} onOpenChange={setStaleOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('edit.newerBoundary')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('edit.staleHelp')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('edit.keepDraft')}</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setStaleOpen(false); onReload(); }}>
              {t('edit.reloadLatest')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function FarmEditForm({ farmId }: { farmId: string }) {
  const farmQuery = useQuery({
    queryKey: ['farm', farmId],
    queryFn: ({ signal }) => getFarm(farmId, signal),
  });

  if (farmQuery.isPending) {
    return <section className="workspace-card loading-card"><Skeleton className="h-8 w-64" /><Skeleton className="h-96 w-full" /></section>;
  }
  if (farmQuery.isError) {
    return <ApiErrorState error={farmQuery.error} onRetry={() => void farmQuery.refetch()} />;
  }

  return (
    <FarmEditWorkspace
      farmId={farmId}
      farm={farmQuery.data}
      onReload={() => void farmQuery.refetch()}
    />
  );
}

export default function EditFarmPage() {
  const { farmId } = useParams<{ farmId: string }>();
  const { t } = useI18n();
  return (
    <div className="farm-editor-page">
      <Link className="back-link" href={`/app/farms/${farmId}/twin`}><ArrowLeft /> {t('edit.backFarm')}</Link>
      <FarmEditForm farmId={farmId} />
    </div>
  );
}

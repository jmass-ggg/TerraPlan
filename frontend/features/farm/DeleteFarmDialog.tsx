'use client';

import { useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { deleteFarm } from '@/lib/api/farms';
import { useI18n } from '@/lib/i18n/context';

export function DeleteFarmDialog({ farmId, farmName }: { farmId: string; farmName: string }) {
  const router = useRouter();
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirmDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      await deleteFarm(farmId);
      queryClient.removeQueries({ queryKey: ['farm', farmId] });
      await queryClient.invalidateQueries({ queryKey: ['farms'] });
      setOpen(false);
      router.push('/app');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('delete.failed'));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger render={<Button variant="outline" className="danger-button" />}>
        <Trash2 /> {t('delete.button')}
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('delete.title', { name: farmName })}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('delete.description')}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && <p className="field-error" role="alert">{error}</p>}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t('delete.keep')}</AlertDialogCancel>
          <AlertDialogAction
            className="danger-confirm-button"
            disabled={deleting}
            onClick={() => void confirmDelete()}
          >
            {deleting ? t('delete.deleting') : t('delete.permanently')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

import Link from 'next/link';
import { AlertCircle, RefreshCw, Unplug } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { FarmTwinApiError } from '@/lib/api/client';
import { useI18n } from '@/lib/i18n/context';

export function ApiErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const { t } = useI18n();
  const apiError = error instanceof FarmTwinApiError ? error : null;
  const configuration = apiError?.kind === 'configuration';

  return (
    <div className="api-state api-state-error" role="alert">
      <span className="api-state-icon" aria-hidden="true">
        {configuration ? <Unplug /> : <AlertCircle />}
      </span>
      <div>
        <p className="api-state-kicker">{configuration ? t('api.connectionNeeded') : t('api.couldNotLoad')}</p>
        <h2>{configuration ? t('api.dataUntouched') : t('api.temporarilyUnavailable')}</h2>
        <p>{error.message}</p>
        {apiError?.requestId && <p className="request-id">{t('common.requestId')}: {apiError.requestId}</p>}
        <div className="inline-actions">
          <Button onClick={onRetry} className="primary-button"><RefreshCw /> {t('common.retry')}</Button>
          <Button variant="outline" render={<Link href="/app/settings" />}>{t('common.connectionSettings')}</Button>
        </div>
      </div>
    </div>
  );
}

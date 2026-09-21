import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Save } from 'lucide-react';
import { useI18n } from '@/lib/i18n/context';

export function FarmNameInput({
  value,
  onChange,
  error,
  onSave,
  canSave = false,
  isSaving = false,
  saveLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  onSave?: () => void;
  canSave?: boolean;
  isSaving?: boolean;
  saveLabel?: string;
}) {
  const { t } = useI18n();
  return (
    <div className="farm-name-field">
      <label htmlFor="farm-name">{t('farmName.label')}</label>
      <div className="farm-name-input-row">
        <div className="farm-name-input-column">
          <Input
            id="farm-name"
            maxLength={255}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? 'farm-name-error' : 'farm-name-help'}
            placeholder={t('farmName.placeholder')}
          />
          {error
            ? <p id="farm-name-error" className="field-error" role="alert">{error}</p>
            : <p id="farm-name-help" className="farm-name-help">{t('farmName.help')}</p>}
        </div>
        {onSave && (
          <Button
            type="button"
            onClick={onSave}
            disabled={!canSave || isSaving}
            className="farm-name-save-button"
          >
            <Save /> {isSaving ? t('common.saving') : (saveLabel ?? t('common.saveFarm'))}
          </Button>
        )}
      </div>
    </div>
  );
}

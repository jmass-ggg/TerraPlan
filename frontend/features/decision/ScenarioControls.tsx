'use client';

import { CloudRain, Droplets, RotateCcw, ThermometerSun } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Slider } from '@/components/ui/slider';
import { useI18n } from '@/lib/i18n/context';

export interface ScenarioControlsProps {
  rainfall: number;
  temperature: number;
  irrigationMm?: string;
  busy?: boolean;
  onRainfallChange: (value: number) => void;
  onTemperatureChange: (value: number) => void;
  onIrrigationChange?: (value: string) => void;
  onApply: () => void;
  onReset: () => void;
}

export function ScenarioControls({
  rainfall,
  temperature,
  irrigationMm = '',
  busy,
  onRainfallChange,
  onTemperatureChange,
  onIrrigationChange,
  onApply,
  onReset,
}: ScenarioControlsProps) {
  const { t } = useI18n();
  const firstValue = (value: number | readonly number[]) =>
    typeof value === 'number' ? value : (value[0] ?? 0);

  return (
    <section
      className="workspace-card scenario-controls"
      aria-labelledby="scenario-title"
    >
      <div className="scenario-heading">
        <div>
          <p className="section-kicker">{t('scenario.kicker')}</p>
          <h2 id="scenario-title">{t('scenario.title')}</h2>
        </div>
        <span>
          {rainfall === 0 && temperature === 0 && (!irrigationMm || irrigationMm === '')
            ? t('scenario.baseline')
            : [
                rainfall !== 0 && `${rainfall > 0 ? '+' : ''}${rainfall}% ${t('scenario.rain')}`,
                temperature !== 0 && `${temperature > 0 ? '+' : ''}${temperature}°C`,
                irrigationMm && irrigationMm !== '' && `${irrigationMm} mm ${t('scenario.irrigation')}`,
              ]
                .filter(Boolean)
                .join(' · ')
          }
        </span>
      </div>
      <div className="scenario-control-grid">
        <div className="scenario-slider">
          <span>
            <CloudRain /> {t('scenario.rainfallChange')}{' '}
            <strong>
              {rainfall > 0 ? '+' : ''}
              {rainfall}%
            </strong>
          </span>
          <Slider
            aria-label={t('scenario.rainfallAria')}
            min={-50}
            max={50}
            step={5}
            value={[rainfall]}
            onValueChange={(values) => onRainfallChange(firstValue(values))}
          />
          <small>{t('scenario.rainfallRange')}</small>
        </div>
        <div className="scenario-slider">
          <span>
            <ThermometerSun /> {t('scenario.temperatureChange')}{' '}
            <strong>
              {temperature > 0 ? '+' : ''}
              {temperature}°C
            </strong>
          </span>
          <Slider
            aria-label={t('scenario.temperatureAria')}
            min={-5}
            max={5}
            step={0.5}
            value={[temperature]}
            onValueChange={(values) => onTemperatureChange(firstValue(values))}
          />
          <small>{t('scenario.temperatureRange')}</small>
        </div>
        {onIrrigationChange !== undefined && (
          <div className="scenario-input">
            <label htmlFor="scenario-irrigation-mm">
              <Droplets aria-hidden="true" /> {t('scenario.irrigationOverride')}{' '}
              {irrigationMm ? (
                <strong>{irrigationMm} mm</strong>
              ) : (
                <span className="scenario-input-hint">{t('common.optional')}</span>
              )}
            </label>
            <input
              id="scenario-irrigation-mm"
              type="number"
              min="0"
              step="10"
              value={irrigationMm}
              onChange={(e) => onIrrigationChange(e.target.value)}
              placeholder="e.g. 200"
              aria-label={t('scenario.irrigationAria')}
            />
            <small>{t('scenario.irrigationHelp')}</small>
          </div>
        )}
      </div>
      <div className="scenario-actions">
        <Button onClick={onApply} disabled={busy} className="primary-button">
          {busy ? t('scenario.recalculating') : t('scenario.recalculate')}
        </Button>
        <Button onClick={onReset} disabled={busy} variant="outline">
          <RotateCcw /> {t('scenario.reset')}
        </Button>
      </div>
    </section>
  );
}

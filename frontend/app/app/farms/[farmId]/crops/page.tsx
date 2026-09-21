'use client';

import {
  ChevronRight,
  CloudRain,
  Droplets,
  FlaskConical,
  Mountain,
  PlayCircle,
  Search,
  Thermometer,
} from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiErrorState } from '@/components/api-state';
import { buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ScenarioControls } from '@/features/decision/ScenarioControls';
import { CropCard } from '@/features/crops/CropCard';
import { CropDetailPanel } from '@/features/crops/CropDetailPanel';
import {
  type CropEntry,
  type CropRankingResponse,
  type SimulationResponse,
  type SimulationResult,
  CropValidationError,
  getCrops,
  simulateCrop,
} from '@/lib/api/crops';
import {
  type EnvironmentalValue,
  type FarmTwinResult,
  getFarmTwin,
} from '@/lib/api/farms';
import {
  computeScenario,
  type CropScenarioResult,
  type HazardScenarioResult,
} from '@/lib/api/scenarios';
import { useI18n } from '@/lib/i18n/context';

type CultivationMode = 'rain_fed' | 'irrigated';
type SortOption = 'suitability' | 'name' | 'category';

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function evidenceValue(value: EnvironmentalValue | null | undefined): string {
  if (value?.value === null || value?.value === undefined) return 'Unavailable';
  return `${value.value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${value.unit}`;
}

function evidenceSource(value: EnvironmentalValue | null | undefined): string {
  if (!value || value.value === null) return 'No current evidence';
  return value.source || 'Farm snapshot';
}

// Classify soil texture based on USDA soil texture triangle
function getSoilTexture(clay: number | null, sand: number | null, silt: number | null): string {
  if (clay === null || sand === null || silt === null) return 'Unknown';
  
  // USDA soil texture classification (simplified)
  if (clay >= 40) return 'Clay';
  if (clay >= 27 && clay < 40 && sand >= 20 && sand < 45) return 'Clay Loam';
  if (clay >= 27 && clay < 40 && sand < 20) return 'Silty Clay';
  if (clay >= 35 && clay < 55 && sand >= 45) return 'Sandy Clay';
  if (silt >= 80) return 'Silt';
  if (silt >= 50 && clay >= 12 && clay < 27) return 'Silty Clay Loam';
  if (silt >= 50 && clay < 12) return 'Silt Loam';
  if (sand >= 85) return 'Sand';
  if (sand >= 70 && sand < 85 && clay < 15) return 'Loamy Sand';
  if (sand >= 43 && sand < 85 && silt < 50 && clay < 20) return 'Sandy Loam';
  if (sand >= 20 && sand < 52 && silt >= 28 && silt < 50 && clay >= 7 && clay < 27) return 'Loam';
  if (silt >= 28 && silt < 50 && clay >= 20 && clay < 35 && sand < 45) return 'Clay Loam';
  if (sand >= 45 && sand < 80 && clay >= 20 && clay < 35) return 'Sandy Clay Loam';
  
  return 'Loam'; // Default fallback
}

// Get farmer-friendly temperature status
function getTemperatureStatus(tempC: number | null): { text: string; variant: 'good' | 'moderate' | 'unavailable' } {
  if (tempC === null) return { text: '', variant: 'unavailable' };
  
  if (tempC >= 18 && tempC <= 28) return { text: 'Good', variant: 'good' };
  if (tempC >= 15 && tempC < 18) return { text: 'Cool', variant: 'moderate' };
  if (tempC > 28 && tempC <= 32) return { text: 'Warm', variant: 'moderate' };
  if (tempC < 15) return { text: 'Cold', variant: 'moderate' };
  return { text: 'Hot', variant: 'moderate' };
}

// Convert NDMI to moisture percentage (approximate for display)
function getMoisturePercentage(ndmi: number | null): number | null {
  if (ndmi === null) return null;
  
  // Approximate conversion: NDMI ranges from -1 to 1
  // Map to 0-100% range (this is a display approximation, not exact soil moisture %)
  // NDMI > 0.3 → ~80-100%
  // NDMI 0.1-0.3 → ~60-80%
  // NDMI -0.1-0.1 → ~40-60%
  // NDMI -0.3--0.1 → ~20-40%
  // NDMI < -0.3 → ~0-20%
  
  const normalized = (ndmi + 1) / 2; // Convert -1 to 1 range → 0 to 1
  return Math.round(normalized * 100);
}

// Convert NDMI to moisture status (NDMI range: -1 to 1)
function getMoistureStatus(ndmi: number | null): { text: string; variant: 'good' | 'moderate' | 'unavailable' } {
  if (ndmi === null) return { text: '', variant: 'unavailable' };
  
  // NDMI interpretation for agriculture:
  // > 0.3: Very Moist / High moisture
  // 0.1 to 0.3: Good moisture
  // -0.1 to 0.1: Moderate
  // -0.3 to -0.1: Dry
  // < -0.3: Very Dry
  
  if (ndmi > 0.3) return { text: 'Good', variant: 'good' };
  if (ndmi >= 0.1) return { text: 'Good', variant: 'good' };
  if (ndmi >= -0.1) return { text: 'Moderate', variant: 'moderate' };
  if (ndmi >= -0.3) return { text: 'Moderate', variant: 'moderate' };
  return { text: 'Low', variant: 'moderate' };
}

// Get soil pH status
function getSoilPHStatus(ph: number | null): { text: string; variant: 'good' | 'moderate' | 'unavailable' } {
  if (ph === null) return { text: '', variant: 'unavailable' };
  
  // Agricultural pH interpretation
  if (ph >= 6.0 && ph <= 7.5) return { text: 'Optimal', variant: 'good' };
  if (ph >= 5.5 && ph < 6.0) return { text: 'Slightly Acidic', variant: 'moderate' };
  if (ph > 7.5 && ph <= 8.0) return { text: 'Slightly Alkaline', variant: 'moderate' };
  if (ph < 5.5) return { text: 'Acidic', variant: 'moderate' };
  return { text: 'Alkaline', variant: 'moderate' };
}

export default function CropSimulatorPage() {
  const { farmId } = useParams<{ farmId: string }>();
  const { t } = useI18n();

  // --- Controls state ---
  const [plantingDate, setPlantingDate] = useState<string>(todayIso());
  const [cultivationMode, setCultivationMode] = useState<CultivationMode>('rain_fed');
  const [irrigationMm, setIrrigationMm] = useState<string>('');
  const [categoryFilter, setCategoryFilter] = useState('All');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [sortOption, setSortOption] = useState<SortOption>('suitability');

  // --- Results state ---
  const [rankedResults, setRankedResults] = useState<SimulationResult[]>([]);
  const [selectedResult, setSelectedResult] = useState<SimulationResult | null>(null);
  const [dataMode, setDataMode] = useState<string | null>(null);
  const [snapshotId, setSnapshotId] = useState<string | null>(null);

  // --- Crop register (for detail panel metadata) ---
  const [cropEntries, setCropEntries] = useState<CropEntry[]>([]);
  const [twin, setTwin] = useState<FarmTwinResult | null>(null);

  // --- Scenario controls state ---
  const [draftRainfall, setDraftRainfall] = useState(0);
  const [draftTemperature, setDraftTemperature] = useState(0);
  const [draftIrrigationMm, setDraftIrrigationMm] = useState('');
  const [scenarioResult, setScenarioResult] = useState<{
    crops: CropScenarioResult[];
    hazards: HazardScenarioResult[];
  } | null>(null);
  const [scenarioBusy, setScenarioBusy] = useState(false);

  // --- Loading / error state ---
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);

  // AbortController ref for in-flight requests
  const abortRef = useRef<AbortController | null>(null);

  // --- Load crop register once ---
  useEffect(() => {
    const controller = new AbortController();
    getCrops(controller.signal).then(
      (list) => setCropEntries(list.crops),
      () => { /* non-critical; detail panel gracefully handles null */ },
    );
    return () => controller.abort();
  }, []);

  // Load the existing Digital Twin once for the evidence strip. Simulator scores
  // remain sourced from simulateCrop(); this request only exposes raw farm values.
  useEffect(() => {
    const controller = new AbortController();
    getFarmTwin(farmId, controller.signal).then(
      (result) => setTwin(result),
      () => setTwin(null),
    );
    return () => controller.abort();
  }, [farmId]);

  // --- Load ranked list ---
  const loadRanked = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    setValidationMessage(null);
    try {
      const irrigation = cultivationMode === 'irrigated' && irrigationMm !== ''
        ? parseFloat(irrigationMm)
        : undefined;
      const response = await simulateCrop(
        farmId,
        {
          planting_date: plantingDate,
          cultivation_mode: cultivationMode,
          ...(irrigation !== undefined ? { irrigation_mm: irrigation } : {}),
        },
        signal,
      );
      if ('ranked' in response) {
        const ranking = response as CropRankingResponse;
        setRankedResults(ranking.ranked);
        setDataMode(ranking.data_mode);
        setSnapshotId(ranking.snapshot_id);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if (err instanceof CropValidationError) {
        setValidationMessage(err.message);
      } else {
        setError(err instanceof Error ? err : new Error(t('api.couldNotLoad')));
      }
    } finally {
      setLoading(false);
    }
  }, [farmId, plantingDate, cultivationMode, irrigationMm, t]);

  // Re-load ranked list whenever controls change
  useEffect(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    queueMicrotask(() => void loadRanked(controller.signal));
    return () => controller.abort();
  }, [loadRanked]);

  // --- Load single crop detail on card selection ---
  const handleSelectCrop = useCallback(async (cropName: string) => {
    // Toggle off if already selected
    if (selectedResult?.crop_name === cropName) {
      setSelectedResult(null);
      return;
    }
    setSelectedLoading(true);
    try {
      const irrigation = cultivationMode === 'irrigated' && irrigationMm !== ''
        ? parseFloat(irrigationMm)
        : undefined;
      const response = await simulateCrop(farmId, {
        crop_name: cropName,
        planting_date: plantingDate,
        cultivation_mode: cultivationMode,
        ...(irrigation !== undefined ? { irrigation_mm: irrigation } : {}),
      });
      if ('selected' in response) {
        const sim = response as SimulationResponse;
        setSelectedResult(sim.selected);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      // Fall back to ranked result on error
      const fallback = rankedResults.find((r) => r.crop_name === cropName) ?? null;
      setSelectedResult(fallback);
    } finally {
      setSelectedLoading(false);
    }
  }, [farmId, plantingDate, cultivationMode, irrigationMm, rankedResults, selectedResult]);

  // --- Scenario handlers ---
  const handleScenarioApply = async () => {
    if (!snapshotId) return; // no snapshot — controls should not be shown
    const hasChanges =
      draftRainfall !== 0 || draftTemperature !== 0 || draftIrrigationMm !== '';
    if (!hasChanges) {
      setScenarioResult(null);
      return;
    }
    setScenarioBusy(true);
    try {
      const result = await computeScenario(farmId, 'Crop simulator scenario', {
        rainfall_change_pct: draftRainfall,
        temperature_change_c: draftTemperature,
        irrigation_mm_override:
          draftIrrigationMm !== '' ? parseFloat(draftIrrigationMm) : null,
      });
      setScenarioResult({ crops: result.crops, hazards: result.hazards });
    } catch {
      // fail silently — user can retry
    } finally {
      setScenarioBusy(false);
    }
  };

  const handleScenarioReset = () => {
    setDraftRainfall(0);
    setDraftTemperature(0);
    setDraftIrrigationMm('');
    setScenarioResult(null);
  };

  // --- Filtering ---
  const availableCategories = Array.from(
    new Set(cropEntries.map((entry) => entry.category).filter(Boolean)),
  ).sort((a, b) => a.localeCompare(b));

  const filteredResults = rankedResults.filter((r) => {
    const matchCategory =
      categoryFilter === 'All' ||
      (cropEntries.find((e) => e.name === r.crop_name)?.category ?? '') === categoryFilter;
    const matchSearch =
      searchQuery === '' ||
      r.crop_name.toLowerCase().includes(searchQuery.toLowerCase());
    return matchCategory && matchSearch;
  }).sort((a, b) => {
    if (sortOption === 'name') return a.crop_name.localeCompare(b.crop_name);
    if (sortOption === 'category') {
      const aCategory = cropEntries.find((entry) => entry.name === a.crop_name)?.category ?? '';
      const bCategory = cropEntries.find((entry) => entry.name === b.crop_name)?.category ?? '';
      return aCategory.localeCompare(bCategory) || b.suitability_index - a.suitability_index;
    }
    return b.suitability_index - a.suitability_index;
  });

  const selectedCropEntry =
    selectedResult
      ? (cropEntries.find((e) => e.name === selectedResult.crop_name) ?? null)
      : null;

  // Snapshot date derived from the ranked list data_mode info
  const snapshotDateLabel = snapshotId
    ? `${t('crops.snapshotBacked')} · ${snapshotId.slice(0, 8)}…`
    : null;

  // Extract real data values from twin
  const temperatureC = twin?.weather?.temperature_2m?.value ?? null;
  const precipitationMm = twin?.weather?.precipitation?.value ?? null;
  const soilClay = twin?.soil?.depth_0_5cm?.clay?.value ?? null;
  const soilSand = twin?.soil?.depth_0_5cm?.sand?.value ?? null;
  const soilSilt = twin?.soil?.depth_0_5cm?.silt?.value ?? null;
  const soilPH = twin?.soil?.depth_0_5cm?.phh2o?.value ?? null;
  const ndmiValue = twin?.satellite?.ndmi?.value ?? null;

  // Calculate 7-day rainfall from daily forecast if available
  const dailyPrecipitation = (twin?.weather?.daily?.precipitation_sum as number[]) || [];
  const rainfall7Day = dailyPrecipitation.length >= 7
    ? dailyPrecipitation.slice(0, 7).reduce((sum, val) => sum + (val || 0), 0)
    : null;

  // Debug logging to see what data we have
  console.log('Twin data:', {
    status: twin?.status,
    temperatureC,
    precipitationMm,
    rainfall7Day,
    soilClay,
    soilSand,
    soilSilt,
    soilPH,
    ndmiValue,
  });

  const evidenceCards = [
    {
      label: t('crops.temperature'),
      value: twin?.status === 'ready' && temperatureC !== null
        ? `${temperatureC.toFixed(1)} °C`
        : t('common.unavailable'),
      source: twin?.status === 'ready' ? evidenceSource(twin.weather?.temperature_2m) : t('crops.analysisNotReady'),
      status: twin?.status === 'ready' 
        ? getTemperatureStatus(temperatureC) 
        : { text: '', variant: 'unavailable' as const },
      icon: Thermometer,
      iconColor: '#ef4444',
      iconBg: '#fee2e2',
    },
    {
      label: t('crops.rainfall7'),
      value: twin?.status === 'ready' && rainfall7Day !== null
        ? `${rainfall7Day.toFixed(1)} mm`
        : t('common.unavailable'),
      source: twin?.status === 'ready' ? 'Open-Meteo 7-day forecast' : t('crops.analysisNotReady'),
      status: twin?.status === 'ready' && rainfall7Day !== null
        ? (rainfall7Day < 20 ? { text: 'Low', variant: 'moderate' as const } 
           : rainfall7Day < 50 ? { text: 'Moderate', variant: 'moderate' as const }
           : { text: 'High', variant: 'good' as const })
        : { text: '', variant: 'unavailable' as const },
      icon: CloudRain,
      iconColor: '#3b82f6',
      iconBg: '#dbeafe',
    },
    {
      label: t('crops.soil'),
      value: twin?.status === 'ready'
        ? (soilClay !== null && soilSand !== null && soilSilt !== null
            ? getSoilTexture(soilClay, soilSand, soilSilt)
            : soilPH !== null
              ? `pH ${soilPH.toFixed(1)}`
              : t('common.unavailable'))
        : t('common.unavailable'),
      source: twin?.status === 'ready' 
        ? (soilPH !== null ? evidenceSource(twin.soil?.depth_0_5cm?.phh2o) : t('crops.noSoilData'))
        : t('crops.analysisNotReady'),
      status: twin?.status === 'ready' && soilPH !== null 
        ? getSoilPHStatus(soilPH) 
        : { text: '', variant: 'unavailable' as const },
      icon: Mountain,
      iconColor: '#22c55e',
      iconBg: '#dcfce7',
    },
    {
      label: t('crops.soilMoisture'),
      value: twin?.status === 'ready' && ndmiValue !== null
        ? `${getMoisturePercentage(ndmiValue)}%`
        : t('common.unavailable'),
      source: twin?.status === 'ready' && ndmiValue !== null 
        ? 'Sentinel-2 NDMI (satellite-derived)' 
        : (twin?.status === 'ready' ? t('crops.satelliteUnavailable') : t('crops.analysisNotReady')),
      status: twin?.status === 'ready' 
        ? getMoistureStatus(ndmiValue) 
        : { text: '', variant: 'unavailable' as const },
      icon: Droplets,
      iconColor: '#3b82f6',
      iconBg: '#dbeafe',
    },
  ];

  // Auto-select highest-ranked crop on load
  useEffect(() => {
    if (!selectedResult && filteredResults.length > 0 && !loading) {
      const topCrop = filteredResults[0];
      void handleSelectCrop(topCrop.crop_name);
    }
  }, [filteredResults, selectedResult, loading, handleSelectCrop]);

  return (
    <div className="crops-page content-stack">
      <nav className="crops-breadcrumb" aria-label="Breadcrumb">
        <Link href={`/app/farms/${farmId}/twin`}>{t('crops.simulator')}</Link>
        <ChevronRight aria-hidden="true" />
        <span>{t('crops.title')}</span>
      </nav>

      {/* Compact page header */}
      <header className="page-heading crops-heading">
        <div>
          <h1>{t('crops.title')}</h1>
          <p>
            {t('crops.intro')}
          </p>
        </div>
        {dataMode && (
          <span className="mode-pill">
            {dataMode === 'demonstration' ? t('crops.demonstrationIndex') : snapshotDateLabel}
          </span>
        )}
      </header>

      {/* Compact environment metrics */}
      <section className="environment-summary" aria-label={t('crops.currentEvidence')}>
        {evidenceCards.map(({ label, value, status, icon: Icon, iconColor, iconBg }) => (
          <article key={label} className="environment-card workspace-card">
            <div className="environment-icon" style={{ backgroundColor: iconBg }}>
              <Icon style={{ color: iconColor }} />
            </div>
            <div className="environment-card-content">
              <small>{label}</small>
              <strong>{value}</strong>
              {status.text && (
                <span className="environment-status" data-variant={status.variant}>
                  {status.text}
                </span>
              )}
            </div>
          </article>
        ))}
      </section>

      {/* Compact simulation controls + main container */}
      <div className="crops-main-container workspace-card">
        {/* Compact controls row */}
        <div className="crops-controls-compact" aria-label={t('crops.simulationSettings')}>
          <div className="control-group-inline">
            <label htmlFor="planting-date" className="control-label-inline">
              {t('crops.plantingDate')}
            </label>
            <input
              id="planting-date"
              type="date"
              className="control-input-compact"
              value={plantingDate}
              onChange={(e) => setPlantingDate(e.target.value)}
              aria-label={t('crops.plantingDate')}
            />
          </div>

          <div className="control-group-inline">
            <span className="control-label-inline">{t('crops.mode')}</span>
            <div className="cultivation-toggle-compact">
              <button
                type="button"
                role="radio"
                aria-checked={cultivationMode === 'rain_fed'}
                data-active={cultivationMode === 'rain_fed' || undefined}
                onClick={() => setCultivationMode('rain_fed')}
              >
                {t('crops.rainFed')}
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={cultivationMode === 'irrigated'}
                data-active={cultivationMode === 'irrigated' || undefined}
                onClick={() => setCultivationMode('irrigated')}
              >
                {t('crops.irrigated')}
              </button>
            </div>
          </div>

          {cultivationMode === 'irrigated' && (
            <div className="control-group-inline">
              <label htmlFor="irrigation-mm" className="control-label-inline">
                {t('crops.irrigation')}
              </label>
              <input
                id="irrigation-mm"
                type="number"
                min="0"
                step="10"
                className="control-input-compact"
                value={irrigationMm}
                onChange={(e) => setIrrigationMm(e.target.value)}
                placeholder="mm"
                aria-label={t('crops.irrigationAria')}
              />
            </div>
          )}
        </div>

      {/* Error states */}
      {error && (
        <ApiErrorState
          error={error}
          onRetry={() => {
            abortRef.current?.abort();
            const controller = new AbortController();
            abortRef.current = controller;
            void loadRanked(controller.signal);
          }}
        />
      )}

      {validationMessage && (
        <div className="validation-banner workspace-card" role="alert">
          <p>
            <strong>{t('crops.missingInputs')}</strong> {validationMessage}
          </p>
          <Link
            className={buttonVariants({ variant: 'outline' })}
            href={`/app/farms/${farmId}/twin`}
          >
            <PlayCircle /> {t('crops.runAnalysis')}
          </Link>
        </div>
      )}

      {/* Main content area */}
      {!error && !validationMessage && (
        <>
          <div className="crops-layout" data-panel-open={selectedResult ? 'true' : undefined}>
            {/* Crop grid */}
            <section
              className="crops-grid-section"
              aria-label={t('crops.rankings')}
              aria-busy={loading}
            >
              <div className="crops-filter-row" aria-label={t('crops.filter')}>
                <div className="crop-search-wrap">
                  <Search aria-hidden="true" />
                  <input
                    type="search"
                    className="crop-search-input"
                    placeholder={t('crops.search')}
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>
                <div className="category-chips" aria-label={t('crops.categoryFilter')}>
                  {['All', ...availableCategories].map((category) => (
                    <button
                      key={category}
                      type="button"
                      aria-pressed={categoryFilter === category}
                      data-active={categoryFilter === category || undefined}
                      onClick={() => setCategoryFilter(category)}
                      className="category-chip"
                    >
                      {category === 'All' ? t('crops.all') : category}
                    </button>
                  ))}
                </div>
                <div className="crop-sort">
                  <span>{t('crops.sortBy')}</span>
                  <select value={sortOption} onChange={(event) => setSortOption(event.target.value as SortOption)}>
                    <option value="suitability">{t('crops.suitability')}</option>
                    <option value="name">{t('crops.cropName')}</option>
                    <option value="category">{t('crops.category')}</option>
                  </select>
                </div>
              </div>
              {loading ? (
                <div className="crops-skeleton-grid">
                  {Array.from({ length: 12 }).map((_, i) => (
                    <Skeleton key={i} className="h-40 w-full rounded-xl" />
                  ))}
                </div>
              ) : (
                <>
                  {filteredResults.length === 0 && (
                    <p className="crops-empty">
                      {t('crops.noMatch')}
                    </p>
                  )}
                  <ul className="crops-grid">
                    {filteredResults.map((result) => (
                      <li key={result.crop_name}>
                        <CropCard
                          result={result}
                          category={cropEntries.find((entry) => entry.name === result.crop_name)?.category}
                          selected={selectedResult?.crop_name === result.crop_name}
                          onClick={() => void handleSelectCrop(result.crop_name)}
                        />
                      </li>
                    ))}
                  </ul>
                  {filteredResults.length > 0 && (
                    <p className="crops-footer">{filteredResults.length === 1 ? t('crops.showingOne') : t('crops.showingMany', { count: filteredResults.length })}</p>
                  )}
                </>
              )}
            </section>

            {/* Detail panel */}
            <aside className="crops-detail-aside" aria-label={t('crops.detailPanel')}>
              {selectedLoading ? (
                <div className="crops-detail-loading">
                  <Skeleton className="h-full w-full rounded-xl" />
                </div>
              ) : selectedResult ? (
                <CropDetailPanel
                  result={selectedResult}
                  cropEntry={selectedCropEntry}
                  planHref={`/app/farms/${farmId}/annual-plan?crop=${encodeURIComponent(selectedResult.crop_name)}&month=${Number(plantingDate.slice(5, 7))}`}
                  farmId={farmId}
                  simulateRequest={{
                    crop_name: selectedResult.crop_name,
                    planting_date: plantingDate,
                    cultivation_mode: cultivationMode,
                    irrigation_mm: cultivationMode === 'irrigated' ? irrigationMm : undefined,
                  }}
                />
              ) : (
                <div className="crop-detail-empty">
                  <FlaskConical aria-hidden="true" />
                  <h2>{t('crops.select')}</h2>
                  <p>{t('crops.selectHelp')}</p>
                </div>
              )}
            </aside>
          </div>
        </>
      )}
      </div> {/* Close crops-main-container */}

      <details className="scenario-disclosure">
        <summary>{t('scenario.kicker')}</summary>
        {snapshotId ? (
          <ScenarioControls
            rainfall={draftRainfall}
            temperature={draftTemperature}
            irrigationMm={draftIrrigationMm}
            busy={scenarioBusy}
            onRainfallChange={setDraftRainfall}
            onTemperatureChange={setDraftTemperature}
            onIrrigationChange={setDraftIrrigationMm}
            onApply={() => void handleScenarioApply()}
            onReset={handleScenarioReset}
          />
        ) : !loading && !error && dataMode !== null ? (
          <output className="scenario-unavailable-notice workspace-card">
            <p><strong>{t('crops.whatIfUnavailable')}</strong> {t('crops.whatIfHelp')}</p>
          </output>
        ) : null}
      </details>

      {/* Scenario comparison — shown after applying a real scenario (Req 2.3, 2.4) */}
      {scenarioResult && (
        <section
          className="workspace-card comparison-card"
          aria-labelledby="crop-scenario-comparison-title"
        >
          <div className="card-heading-row">
            <div>
              <p className="section-kicker">{t('crops.scenarioExplorer')}</p>
              <h2 id="crop-scenario-comparison-title">
                {t('crops.baselineVsScenario')}
              </h2>
            </div>
            <span>
              {draftRainfall}% rain ·{' '}
              {draftTemperature > 0 ? '+' : ''}
              {draftTemperature}°C
            </span>
          </div>
          <div className="comparison-table-wrap">
            <table className="comparison-table">
              <caption className="sr-only">
                Baseline and climate scenario crop suitability scores for all 12 crops
              </caption>
              <thead>
                <tr>
                  <th>{t('crops.crop')}</th>
                  <th>{t('crops.baseline')}</th>
                  <th>{t('crops.scenario')}</th>
                  <th>{t('crops.change')}</th>
                </tr>
              </thead>
              <tbody>
                {scenarioResult.crops.map((item) => {
                  const delta = item.scenario_index - item.baseline_index;
                  return (
                    <tr key={item.crop_name}>
                      <td>{item.crop_name}</td>
                      <td>
                        {item.baseline_index}
                        <small className="comparison-label"> {item.baseline_label}</small>
                      </td>
                      <td>
                        {item.scenario_index}
                        <small className="comparison-label"> {item.scenario_label}</small>
                      </td>
                      <td>
                        <b
                          data-direction={
                            delta === 0 ? 'same' : delta > 0 ? 'up' : 'down'
                          }
                        >
                          {delta > 0 ? '+' : ''}
                          {delta}
                        </b>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {scenarioResult.hazards.length > 0 && (
            <div className="comparison-hazards">
              <h3>{t('crops.hazardLevels')}</h3>
              <table className="comparison-table">
                <caption className="sr-only">
                  Baseline and scenario hazard levels for all 5 hazards
                </caption>
                <thead>
                  <tr>
                    <th>{t('crops.hazard')}</th>
                    <th>{t('crops.baseline')}</th>
                    <th>{t('crops.scenario')}</th>
                  </tr>
                </thead>
                <tbody>
                  {scenarioResult.hazards.map((h) => (
                    <tr key={h.hazard}>
                      <td>{h.hazard}</td>
                      <td>
                        <span data-level={h.baseline_level.toLowerCase()}>
                          {h.baseline_level}
                        </span>
                      </td>
                      <td>
                        <span data-level={h.scenario_level.toLowerCase()}>
                          {h.scenario_level}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

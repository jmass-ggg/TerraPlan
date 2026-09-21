'use client';

import {
  AlertCircle, CheckCircle2, CircleDot, CloudRain, Layers3,
  LocateFixed, MapPin, Pencil, PlayCircle, RefreshCw,
  Ruler, Search, Sprout, ThermometerSun,
} from 'lucide-react';
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiErrorState } from '@/components/api-state';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { ClimateOverview } from '@/features/twin/ClimateOverview';
import { EvidenceSection } from '@/features/twin/EvidenceSection';
import { useApiResource } from '@/hooks/use-api-resource';
import { farmTwinApi } from '@/lib/api/client';
import {
  type EnvironmentalValue,
  type FarmTwinResult,
  type GeoJSONPolygon,
  type JobProgress,
  type JobStage,
  getFarmTwin,
  getJobProgress,
  triggerAnalysis,
} from '@/lib/api/farms';
import { createFarmMapStyle, type FarmBasemap } from '@/lib/map-style';
import { useI18n } from '@/lib/i18n/context';
import type { TranslationKey } from '@/lib/i18n/translations';

const POLL_INTERVAL_MS = 4000;

type InsightLayer = 'satellite' | 'vegetation' | 'soil' | 'water' | 'flood' | 'elevation';
const LAYERS: Array<{ id: InsightLayer; labelKey: TranslationKey }> = [
  { id: 'satellite', labelKey: 'twin.satellite' },
  { id: 'vegetation', labelKey: 'twin.vegetation' },
  { id: 'soil', labelKey: 'twin.soil' },
  { id: 'water', labelKey: 'twin.water' },
  { id: 'flood', labelKey: 'twin.floodRisk' },
  { id: 'elevation', labelKey: 'twin.elevation' },
];

const STAGE_LABELS: Record<string, TranslationKey> = {
  weather: 'twin.weather',
  climate: 'twin.climate',
  satellite: 'twin.satellite',
  soil: 'twin.soil',
  terrain: 'twin.terrain',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function valueText(value: EnvironmentalValue | null | undefined, digits = 1): string {
  if (value?.value === null || value?.value === undefined) return 'Unavailable';
  
  // Clean up unit display
  let unit = value.unit;
  if (unit === 'dimensionless') unit = '';
  else if (unit === 'percent') unit = '%';
  else if (unit === 'metres') unit = 'm';
  else if (unit === 'degrees') unit = '°';
  else if (unit === 'g/kg') unit = 'g/kg';
  else if (unit === 'Celsius') unit = '°C';
  
  const formattedValue = value.value.toLocaleString(undefined, { maximumFractionDigits: digits });
  return unit ? `${formattedValue} ${unit}` : formattedValue;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

function formatDateOnly(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' });
  } catch {
    return iso;
  }
}

function layerColour(layer: InsightLayer, twin: FarmTwinResult | null): string {
  if (layer === 'vegetation') {
    const ndvi = twin?.satellite?.ndvi?.value;
    if (ndvi === null || ndvi === undefined) return '#0b8d48';
    if (ndvi >= 0.6) return '#08783b';
    if (ndvi >= 0.35) return '#76a932';
    return '#c78a21';
  }
  if (layer === 'soil') return '#946b3e';
  if (layer === 'water') return '#1687bd';
  if (layer === 'flood') return '#687b8d';
  if (layer === 'elevation') return '#7258a5';
  return '#0a9b50';
}

// ---------------------------------------------------------------------------
// Stage progress indicator
// ---------------------------------------------------------------------------

function StageIcon({ stage }: { stage: JobStage | undefined }) {
  if (!stage) return <CircleDot className="stage-icon stage-icon-queued" aria-hidden="true" />;
  if (stage.status === 'completed') return <CheckCircle2 className="stage-icon stage-icon-done" aria-hidden="true" />;
  if (stage.status === 'failed') return <AlertCircle className="stage-icon stage-icon-failed" aria-hidden="true" />;
  if (stage.status === 'running') return <RefreshCw className="stage-icon stage-icon-running spin" aria-hidden="true" />;
  return <CircleDot className="stage-icon stage-icon-queued" aria-hidden="true" />;
}

function StageProgress({ stages }: { stages: Record<string, JobStage> }) {
  const { t } = useI18n();
  const ordered = ['weather', 'climate', 'satellite', 'soil', 'terrain'];
  return (
    <ul className="job-stage-list" aria-label={t('twin.stageProgress')}>
      {ordered.map((key) => {
        const stage = stages[key];
        const label = STAGE_LABELS[key] ? t(STAGE_LABELS[key]) : key;
        const status = stage?.status ?? 'queued';
        return (
          <li key={key} className="job-stage-row" data-status={status}>
            <StageIcon stage={stage} />
            <span className="job-stage-name">{label}</span>
            <span className="job-stage-status">{status}</span>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Map component
// ---------------------------------------------------------------------------

function TwinMap({
  farmName,
  areaHectares,
  editHref,
  geometry,
  twin,
}: {
  farmName: string;
  areaHectares: number;
  editHref: string;
  geometry: GeoJSONPolygon;
  twin: FarmTwinResult | null;
}) {
  const { language, t } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [layer, setLayer] = useState<InsightLayer>('satellite');
  const [basemap, setBasemap] = useState<FarmBasemap>('satellite');
  const [query, setQuery] = useState('');
  const [searchStatus, setSearchStatus] = useState<string | null>(null);
  const [mapUnavailable, setMapUnavailable] = useState(false);
  const [showArea, setShowArea] = useState(true);
  const colour = layerColour(layer, twin);

  useEffect(() => {
    if (!containerRef.current) return;
    const coordinates = geometry.coordinates[0] as [number, number][];
    const first = coordinates[0] ?? [36.82, -1.29];
    let map: MapLibreMap;
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: createFarmMapStyle(basemap),
        center: first,
        zoom: 14,
        attributionControl: false,
      });
    } catch {
      queueMicrotask(() => setMapUnavailable(true));
      return;
    }
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    map.on('load', () => {
      map.addSource('farm-boundary', {
        type: 'geojson',
        data: { type: 'Feature', properties: {}, geometry },
      });
      map.addLayer({
        id: 'farm-fill',
        type: 'fill',
        source: 'farm-boundary',
        paint: {
          'fill-color': colour,
          'fill-opacity': layer === 'satellite' ? 0.25 : 0.46,
        },
      });
      map.addLayer({
        id: 'farm-outline',
        type: 'line',
        source: 'farm-boundary',
        paint: { 'line-color': '#37f394', 'line-width': 3 },
      });
      if (coordinates.length) {
        const bounds = coordinates.reduce(
          (acc, c) => acc.extend(c),
          new maplibregl.LngLatBounds(coordinates[0], coordinates[0]),
        );
        const reserveInspector = (containerRef.current?.clientWidth ?? 0) > 960;
        map.fitBounds(bounds, {
          padding: { top: 90, bottom: 90, left: 90, right: reserveInspector ? 350 : 90 },
          maxZoom: 17,
        });
      }
      setMapUnavailable(false);
    });
    map.on('error', () => {
      if (basemap === 'satellite') setBasemap('street');
      else setMapUnavailable(true);
    });
    mapRef.current = map;
    const resizeObserver = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => map.resize())
      : null;
    resizeObserver?.observe(containerRef.current);
    return () => {
      resizeObserver?.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, [basemap, colour, geometry, layer]);

  const search = async () => {
    const term = query.trim();
    if (!term) return;
    const parts = term.split(',').map(Number);
    if (parts.length === 2 && parts.every(Number.isFinite)) {
      mapRef.current?.flyTo({ center: [parts[1], parts[0]], zoom: 15 });
      setSearchStatus(t('twin.movedCoordinates'));
      return;
    }
    setSearchStatus('Searching…');
    try {
      const res = await fetch(
        'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(term),
        { headers: { 'Accept-Language': language } },
      );
      const matches = await res.json() as Array<{ lat: string; lon: string; display_name: string }>;
      if (!matches[0]) throw new Error(t('map.noPlace'));
      mapRef.current?.flyTo({ center: [Number(matches[0].lon), Number(matches[0].lat)], zoom: 15 });
      setSearchStatus(matches[0].display_name);
    } catch (error) {
      setSearchStatus(error instanceof Error ? error.message : t('twin.searchFailed'));
    }
  };

  return (
    <section className="twin-map-workspace" aria-label={t('twin.mapAria')}>
      <div className="twin-map-toolbar">
        <div className="twin-map-search">
          <Search aria-hidden="true" />
          <Input
            aria-label={t('twin.searchLocation')}
            placeholder={t('twin.searchPlaceholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void search(); }}
          />
          <Button variant="outline" type="button" onClick={() => void search()}>
            <LocateFixed /> {t('common.find')}
          </Button>
        </div>
        <div className="twin-layer-tabs" role="tablist" aria-label={t('twin.layers')}>
          {LAYERS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={layer === item.id}
              data-active={layer === item.id || undefined}
              onClick={() => setLayer(item.id)}
            >
              {t(item.labelKey)}
            </button>
          ))}
        </div>
      </div>
      {searchStatus && <output className="twin-search-status">{searchStatus}</output>}
      <div ref={containerRef} className="twin-map-canvas" aria-label={farmName + ' saved boundary map'} />
      {mapUnavailable && (
        <output className="twin-map-unavailable">
          <MapPin />
          <strong>{t('twin.boundarySaved')}</strong>
          <span>{t('twin.basemapFailed')}</span>
        </output>
      )}
      <div className="twin-map-edit-tools" aria-label={t('twin.mapTools')}>
        <Link href={editHref} aria-label={t('twin.editBoundary')}>
          <Pencil aria-hidden="true" />
          <span>{t('twin.editShape')}</span>
        </Link>
        <button
          type="button"
          aria-label={t('twin.showArea')}
          aria-pressed={showArea}
          onClick={() => setShowArea((current) => !current)}
        >
          <Ruler aria-hidden="true" />
          <span>{t('twin.measureArea')}</span>
        </button>
      </div>
      <div className="twin-map-label">
        <strong>
          {farmName}{showArea ? ` · ${areaHectares.toLocaleString(undefined, { maximumFractionDigits: 2 })} ha` : ''}
        </strong>
        {layer !== 'satellite' && (
          <span>{t(LAYERS.find((l) => l.id === layer)?.labelKey ?? 'twin.insight')} · {t('twin.farmWide')}</span>
        )}
      </div>
      <div className="twin-basemap-switcher" aria-label={t('twin.basemap')}>
        <button type="button" data-active={basemap === 'satellite' || undefined} onClick={() => setBasemap('satellite')}>{t('twin.satellite')}</button>
        <button type="button" data-active={basemap === 'street' || undefined} onClick={() => setBasemap('street')}>{t('twin.street')}</button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Full right-panel detail sections
// ---------------------------------------------------------------------------

function LocationSection({ farm }: { farm: { name: string; current_geometry: { hectares: number; centroid?: { type: 'Point'; coordinates: [number, number] } | null }; current_geometry_revision: number } }) {
  const { t } = useI18n();
  const centroid = farm.current_geometry.centroid?.coordinates;
  return (
    <section className="farm-insight-group">
      <h2><MapPin /> {t('twin.location')}</h2>
      <dl>
        <div><dt>{t('farmName.label')}</dt><dd>{farm.name}</dd></div>
        <div><dt>{t('twin.area')}</dt><dd>{farm.current_geometry.hectares.toLocaleString(undefined, { maximumFractionDigits: 2 })} ha</dd></div>
        <div><dt>{t('twin.boundaryRevision')}</dt><dd>{farm.current_geometry_revision}</dd></div>
        {centroid && (
          <div>
            <dt>{t('twin.centroid')}</dt>
            <dd>{centroid[1].toFixed(5)}°, {centroid[0].toFixed(5)}°</dd>
          </div>
        )}
      </dl>
    </section>
  );
}

function WeatherSection({ weather }: { weather: FarmTwinResult['weather'] }) {
  const { t } = useI18n();
  return (
    <section className="farm-insight-group">
      <h2><ThermometerSun /> {t('twin.weather')}</h2>
      {!weather ? (
        <p className="insight-unavailable">{t('twin.providerUnavailable', { provider: t('twin.weather').toLowerCase() })}</p>
      ) : (
        <>
          <dl>
            <div><dt>{t('crops.temperature')}</dt><dd>{valueText(weather.temperature_2m)}</dd></div>
            <div><dt>{t('twin.rainfall')}</dt><dd>{valueText(weather.precipitation)}</dd></div>
            <div><dt>{t('twin.humidity')}</dt><dd>{valueText(weather.relative_humidity_2m)}</dd></div>
            <div><dt>{t('twin.windSpeed')}</dt><dd>{valueText(weather.wind_speed_10m)}</dd></div>
            {weather.valid_time && <div><dt>{t('twin.validTime')}</dt><dd>{formatDate(weather.valid_time)}</dd></div>}
            {weather.model_name && <div><dt>{t('twin.provider')}</dt><dd>{weather.model_name}</dd></div>}
          </dl>
        </>
      )}
    </section>
  );
}

function VegetationSection({ satellite }: { satellite: FarmTwinResult['satellite'] }) {
  const { t } = useI18n();
  return (
    <section className="farm-insight-group">
      <h2><Sprout /> {t('twin.vegetation')}</h2>
      {!satellite ? (
        <p className="insight-unavailable">{t('twin.providerUnavailable', { provider: t('twin.satellite').toLowerCase() })}</p>
      ) : (
        <dl>
          <div><dt>NDVI</dt><dd>{valueText(satellite.ndvi, 3)}</dd></div>
          <div><dt>NDMI</dt><dd>{valueText(satellite.ndmi, 3)}</dd></div>
          {satellite.acquisition_date && (
            <div><dt>{t('twin.acquisitionDate')}</dt><dd>{formatDateOnly(satellite.acquisition_date)}</dd></div>
          )}
          {satellite.cloud_cover_pct !== null && satellite.cloud_cover_pct !== undefined && (
            <div><dt>{t('twin.cloudCover')}</dt><dd>{satellite.cloud_cover_pct.toFixed(1)} %</dd></div>
          )}
          {satellite.valid_pixel_pct !== null && satellite.valid_pixel_pct !== undefined && (
            <div><dt>{t('twin.validPixels')}</dt><dd>{satellite.valid_pixel_pct.toFixed(1)} %</dd></div>
          )}
          <div className="insight-note">{t('twin.ndviNote')}</div>
        </dl>
      )}
    </section>
  );
}

function SoilSection({ soil }: { soil: FarmTwinResult['soil'] }) {
  const { t } = useI18n();
  const depth = soil?.depth_0_5cm ?? soil?.depth_5_15cm;
  return (
    <section className="farm-insight-group">
      <h2><Layers3 /> {t('twin.soil')}</h2>
      {!soil || !depth ? (
        <p className="insight-unavailable">{t('twin.providerUnavailable', { provider: t('twin.soil').toLowerCase() })}</p>
      ) : (
        <>
          <dl>
            <div><dt>pH (H₂O)</dt><dd>{valueText(depth.phh2o, 1)}</dd></div>
            <div><dt>{t('twin.clay')}</dt><dd>{valueText(depth.clay, 1)}</dd></div>
            <div><dt>{t('twin.sand')}</dt><dd>{valueText(depth.sand, 1)}</dd></div>
            <div><dt>{t('twin.silt')}</dt><dd>{valueText(depth.silt, 1)}</dd></div>
            <div><dt>{t('twin.organicCarbon')}</dt><dd>{valueText(depth.soc, 2)}</dd></div>
            <div><dt>{t('twin.soilResolution')}</dt><dd>{soil.source_resolution_m} m</dd></div>
          </dl>
          {soil.modelled_estimate && (
            <p className="insight-note insight-note-warning">
              {t('twin.soilModelled')}
            </p>
          )}
          {soil.small_farm_flag && (
            <p className="insight-note">
              {t('twin.smallFarm')}
            </p>
          )}
        </>
      )}
    </section>
  );
}

function TerrainSection({ terrain }: { terrain: FarmTwinResult['terrain'] }) {
  const { t } = useI18n();
  return (
    <section className="farm-insight-group">
      <h2><Layers3 /> {t('twin.terrain')}</h2>
      {!terrain ? (
        <p className="insight-unavailable">{t('twin.providerUnavailable', { provider: t('twin.terrain').toLowerCase() })}</p>
      ) : (
        <>
          <dl>
            <div><dt>{t('twin.meanElevation')}</dt><dd>{valueText(terrain.mean_elevation_m)}</dd></div>
            <div><dt>{t('twin.minElevation')}</dt><dd>{valueText(terrain.min_elevation_m)}</dd></div>
            <div><dt>{t('twin.maxElevation')}</dt><dd>{valueText(terrain.max_elevation_m)}</dd></div>
            <div><dt>{t('twin.meanSlope')}</dt><dd>{valueText(terrain.mean_slope_deg)}</dd></div>
            {terrain.dem_source && <div><dt>{t('twin.demSource')}</dt><dd>{terrain.dem_source}</dd></div>}
            {terrain.resolution_m && <div><dt>{t('twin.demResolution')}</dt><dd>{terrain.resolution_m} m</dd></div>}
          </dl>
          <div style={{ marginTop: '16px' }}>
            <p style={{ margin: 0, color: '#738178', fontSize: '.82rem', fontWeight: 500 }}>{t('twin.floodExposure')}</p>
            <p className="insight-unavailable" style={{ marginTop: '4px' }}>
              {t('common.unavailable')}
            </p>
            <p className="insight-note">
              {t('twin.drainageRequired')}
            </p>
          </div>
        </>
      )}
    </section>
  );
}



function SourceStatusSection({ statuses }: { statuses: Record<string, string> }) {
  const { t } = useI18n();
  return (
    <section className="farm-insight-group">
      <h2><CloudRain /> {t('twin.sourceStatus')}</h2>
      <div className="source-status-grid">
        {['weather', 'satellite', 'soil', 'terrain'].map((src) => (
          <span key={src} data-status={statuses[src] ?? 'unavailable'}>
            {src} <b>{statuses[src] ?? 'unavailable'}</b>
          </span>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Evidence drawer (bottom section)
// ---------------------------------------------------------------------------

function collectEvidence(twin: FarmTwinResult | null) {
  const weather = twin?.weather;
  const satellite = twin?.satellite;
  const soil = twin?.soil?.depth_0_5cm ?? twin?.soil?.depth_5_15cm;
  const terrain = twin?.terrain;
  const values = (items: Array<EnvironmentalValue | null | undefined>) =>
    items.filter((item): item is EnvironmentalValue => item !== null && item !== undefined);
  return {
    weather: values([weather?.temperature_2m, weather?.precipitation, weather?.relative_humidity_2m, weather?.wind_speed_10m]),
    satellite: values([satellite?.ndvi, satellite?.ndmi]),
    soil: values([soil?.phh2o, soil?.clay, soil?.sand, soil?.silt, soil?.soc]),
    terrain: values([terrain?.mean_elevation_m, terrain?.min_elevation_m, terrain?.max_elevation_m, terrain?.mean_slope_deg]),
  };
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function FarmTwinPage() {
  const { farmId } = useParams<{ farmId: string }>();
  const { t } = useI18n();

  const loadFarm = useCallback(
    (signal: AbortSignal) => farmTwinApi.getFarm(farmId, signal),
    [farmId],
  );
  const farm = useApiResource(loadFarm);

  const [twin, setTwin] = useState<FarmTwinResult | null>(null);
  const [twinLoading, setTwinLoading] = useState(true);
  const [twinError, setTwinError] = useState<Error | null>(null);
  const [jobProgress, setJobProgress] = useState<JobProgress | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);

  const loadTwin = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await getFarmTwin(farmId, signal);
      setTwin(result);
      setTwinError(null);
      // When a job_id is present in a pending response, fetch stage-level progress
      if (result.status === 'pending' && result.job_id) {
        try {
          const progress = await getJobProgress(result.job_id);
          setJobProgress(progress);
        } catch {
          // Stage progress is best-effort; don't surface this error
        }
      } else {
        setJobProgress(null);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setTwinError(error instanceof Error ? error : new Error(t('twin.loadFailed')));
    } finally {
      setTwinLoading(false);
    }
  }, [farmId, t]);

  // Initial load
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void loadTwin(controller.signal), 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [loadTwin]);

  // Poll while pending — stops automatically when status changes
  useEffect(() => {
    if (twin?.status !== 'pending') return;
    const timer = setTimeout(() => void loadTwin(), POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [loadTwin, twin?.status, twin]);

  const reloadTwin = useCallback(() => {
    setTwinLoading(true);
    void loadTwin();
  }, [loadTwin]);

  const runAnalysis = async () => {
    setTriggering(true);
    setActionError(null);
    try {
      await triggerAnalysis(farmId);
      // Reset twin state so the previous snapshot does not bleed through
      // while the new job is pending.
      setTwin(null);
      setJobProgress(null);
      reloadTwin();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : t('twin.startFailed'));
    } finally {
      setTriggering(false);
    }
  };

  if (farm.status === 'loading') {
    return <div className="twin-loading-page"><Skeleton className="h-full w-full rounded-3xl" /></div>;
  }
  if (farm.status === 'error') {
    return <ApiErrorState error={farm.error} onRetry={farm.retry} />;
  }

  const farmData = farm.result.data;
  const evidence = collectEvidence(twin);
  const statuses = twin?.evidence_statuses ?? {};
  // Merge stage statuses from job progress into evidence_statuses for display
  const stageStatuses = jobProgress?.stages
    ? Object.fromEntries(
        Object.entries(jobProgress.stages).map(([k, v]) => [k, v.status]),
      )
    : {};
  const displayStatuses = { ...statuses, ...stageStatuses };

  return (
    <div className="twin-page">
      <div className="twin-map-column">
        <TwinMap
          farmName={farmData.name}
          areaHectares={farmData.current_geometry.hectares}
          editHref={'/app/farms/' + farmId + '/edit'}
          geometry={farmData.current_geometry.geometry}
          twin={twin}
        />
      </div>

      <aside className="farm-insight-panel" aria-label={t('twin.insightsAria', { name: farmData.name })}>
        <header className="farm-insight-header">
          <div className="farm-avatar"><Sprout /></div>
          <div>
            <span>{t('twin.yourFarm')}</span>
            <h1>{farmData.name}</h1>
            <p>{farmData.current_geometry.hectares.toLocaleString(undefined, { maximumFractionDigits: 2 })} ha</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            render={<Link href={'/app/farms/' + farmId + '/edit'} />}
            aria-label={t('twin.editBoundary')}
          >
            <Pencil />
          </Button>
        </header>

        <div className="farm-inspector-tabs" role="tablist" aria-label={t('twin.farmDetails')}>
          <button type="button" role="tab" aria-selected="true">{t('nav.overview')}</button>
        </div>

        <div className="farm-boundary-confirmed">
          <MapPin />
          <div>
            <strong>{t('twin.boundaryConfirmed')}</strong>
            <span>{t('twin.revision', { revision: farmData.current_geometry_revision })}</span>
          </div>
        </div>

        {twinLoading && (
          <div className="twin-side-loading">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        )}
        {twinError && <ApiErrorState error={twinError} onRetry={reloadTwin} />}

        {/* Pending — with stage-level breakdown */}
        {!twinLoading && !twinError && twin?.status === 'pending' && (
          <div className="twin-analysis-progress" aria-live="polite">
            <div className="twin-analysis-progress-header">
              <RefreshCw className="spin" aria-hidden="true" />
              <div>
                <strong>{t('twin.building')}</strong>
                <span>{t('twin.checking')}</span>
              </div>
            </div>
            {jobProgress?.stages && Object.keys(jobProgress.stages).length > 0 ? (
              <StageProgress stages={jobProgress.stages} />
            ) : (
              <StageProgress stages={{}} />
            )}
          </div>
        )}

        {/* No analysis yet */}
        {!twinLoading && !twinError && twin?.status === 'unavailable' && (
          <div className="twin-empty-analysis">
            <Layers3 />
            <strong>{t('twin.readyStart')}</strong>
            <span>{t('twin.savedBoundaryCheck')}</span>
            <Button onClick={() => void runAnalysis()} disabled={triggering}>
              <PlayCircle /> {triggering ? t('twin.starting') : t('twin.build')}
            </Button>
          </div>
        )}

        {/* Ready — full detail panel */}
        {!twinLoading && !twinError && twin?.status === 'ready' && (
          <>
            <LocationSection farm={farmData} />
            <WeatherSection weather={twin.weather} />
            <VegetationSection satellite={twin.satellite} />
            <SoilSection soil={twin.soil} />
            <TerrainSection terrain={twin.terrain} />
            <SourceStatusSection statuses={displayStatuses} />
            <details className="twin-evidence-accordion">
              <summary>{t('twin.evidenceDetails')}</summary>
              <div className="twin-evidence-stack">
                <EvidenceSection title={t('twin.weather')} status={statuses.weather ?? 'unavailable'} values={evidence.weather} />
                <EvidenceSection title={t('twin.satellite')} status={statuses.satellite ?? 'unavailable'} values={evidence.satellite} />
                <EvidenceSection title={t('twin.soil')} status={statuses.soil ?? 'unavailable'} values={evidence.soil} />
                <EvidenceSection title={t('twin.terrain')} status={statuses.terrain ?? 'unavailable'} values={evidence.terrain} />
              </div>
              <p className="twin-evidence-note">
                {t('twin.mapColoursNote')}
              </p>
            </details>
            <Button
              className="primary-button twin-refresh-button"
              onClick={() => void runAnalysis()}
              disabled={triggering}
            >
              <RefreshCw /> {triggering ? t('twin.starting') : t('twin.refresh')}
            </Button>
          </>
        )}

        {actionError && <p className="form-error" role="alert">{actionError}</p>}
      </aside>

      {/* Climate section - separate from insights panel */}
      {!twinLoading && !twinError && twin?.status === 'ready' && (
        <section id="climate" className="twin-climate-section" aria-label={t('climate.title')}>
          <ClimateOverview 
            weather={twin.weather}
            satellite={twin.satellite}
            climateBaseline={twin.climate_baseline}
          />
        </section>
      )}
    </div>
  );
}

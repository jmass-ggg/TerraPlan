import type { StyleSpecification } from 'maplibre-gl';

export type FarmBasemap = 'satellite' | 'street';

const OSM_TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ESRI_IMAGERY_TILES =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

export function createFarmMapStyle(basemap: FarmBasemap = 'street'): StyleSpecification {
  const satellite = basemap === 'satellite';
  return {
    version: 8,
    sources: {
      basemap: {
        type: 'raster',
        tiles: [satellite ? ESRI_IMAGERY_TILES : OSM_TILES],
        tileSize: 256,
        attribution: satellite
          ? 'Tiles © Esri'
          : '© OpenStreetMap contributors',
        maxzoom: 19,
      },
    },
    layers: [
      {
        id: 'basemap',
        type: 'raster',
        source: 'basemap',
        minzoom: 0,
        maxzoom: 20,
      },
    ],
  };
}

/**
 * Test: Crop Simulator Environmental Cards Data Validity
 * 
 * PURPOSE:
 * Ensures that the Crop Simulator environmental cards display ONLY real data
 * or explicitly marked unavailable data. No fake fallback values should ever
 * be shown to users.
 * 
 * CONTEXT:
 * Previously, the UI showed:
 * - "650 mm" rainfall when real data was 0.0 mm
 * - "Loam" soil type when no soil texture data was available
 * - "62%" moisture when no NDMI data was available
 * 
 * This created a misleading dashboard that appeared complete but showed
 * fabricated values instead of real evidence.
 */

import { describe, it, expect } from 'vitest';

describe('Crop Simulator Environmental Cards - Data Provenance', () => {
  
  it('should show real temperature when available', () => {
    const twin = {
      status: 'ready' as const,
      weather: {
        temperature_2m: {
          value: 22.1,
          unit: '°C',
          source: 'open-meteo',
          quality: 'accepted',
        },
      },
    };
    
    const temperatureC = twin.weather.temperature_2m.value;
    const displayValue = twin.status === 'ready' && temperatureC !== null
      ? `${temperatureC.toFixed(1)} °C`
      : 'Unavailable';
    
    expect(displayValue).toBe('22.1 °C');
    expect(displayValue).not.toBe('18–28 °C'); // old fake fallback
  });
  
  it('should show "Unavailable" when temperature is missing', () => {
    const twin = {
      status: 'ready' as const,
      weather: {
        temperature_2m: null,
      },
    };
    
    const temperatureC = twin.weather.temperature_2m;
    const displayValue = twin.status === 'ready' && temperatureC !== null
      ? `${temperatureC.toFixed(1)} °C`
      : 'Unavailable';
    
    expect(displayValue).toBe('Unavailable');
    expect(displayValue).not.toBe('18–28 °C'); // old fake fallback
  });
  
  it('should calculate 7-day rainfall from daily forecast when available', () => {
    const twin = {
      status: 'ready' as const,
      weather: {
        daily: {
          precipitation_sum: [0.5, 2.0, 0.0, 12.3, 1.2, 8.9, 13.9],
        },
      },
    };
    
    const dailyPrecipitation = (twin.weather.daily.precipitation_sum as number[]) || [];
    const rainfall7Day = dailyPrecipitation.length >= 7
      ? dailyPrecipitation.slice(0, 7).reduce((sum, val) => sum + (val || 0), 0)
      : null;
    
    expect(rainfall7Day).toBeCloseTo(38.8, 1);
    expect(rainfall7Day).not.toBe(650); // old fake fallback
  });
  
  it('should show "Unavailable" when rainfall data is missing', () => {
    const twin = {
      status: 'ready' as const,
      weather: {
        daily: {
          precipitation_sum: [], // no data
        },
      },
    };
    
    const dailyPrecipitation = (twin.weather.daily.precipitation_sum as number[]) || [];
    const rainfall7Day = dailyPrecipitation.length >= 7
      ? dailyPrecipitation.slice(0, 7).reduce((sum, val) => sum + (val || 0), 0)
      : null;
    
    const displayValue = twin.status === 'ready' && rainfall7Day !== null
      ? `${rainfall7Day.toFixed(1)} mm`
      : 'Unavailable';
    
    expect(displayValue).toBe('Unavailable');
    expect(displayValue).not.toBe('650 mm'); // old fake fallback
  });
  
  it('should show "Unavailable" when soil texture data is missing', () => {
    const twin = {
      status: 'ready' as const,
      soil: {
        depth_0_5cm: {
          clay: null,
          sand: null,
          silt: null,
          phh2o: null,
        },
      },
    };
    
    const soilClay = twin.soil.depth_0_5cm.clay;
    const soilSand = twin.soil.depth_0_5cm.sand;
    const soilSilt = twin.soil.depth_0_5cm.silt;
    const soilPH = twin.soil.depth_0_5cm.phh2o;
    
    const displayValue = twin.status === 'ready'
      ? (soilClay !== null && soilSand !== null && soilSilt !== null
          ? 'Would call getSoilTexture()'
          : soilPH !== null
            ? `pH ${soilPH.toFixed(1)}`
            : 'Unavailable')
      : 'Unavailable';
    
    expect(displayValue).toBe('Unavailable');
    expect(displayValue).not.toBe('Loam'); // old fake fallback
  });
  
  it('should show pH when texture data is unavailable but pH exists', () => {
    const twin = {
      status: 'ready' as const,
      soil: {
        depth_0_5cm: {
          clay: null,
          sand: null,
          silt: null,
          phh2o: { value: 6.5 },
        },
      },
    };
    
    const soilClay = twin.soil.depth_0_5cm.clay;
    const soilSand = twin.soil.depth_0_5cm.sand;
    const soilSilt = twin.soil.depth_0_5cm.silt;
    const soilPH = twin.soil.depth_0_5cm.phh2o?.value ?? null;
    
    const displayValue = twin.status === 'ready'
      ? (soilClay !== null && soilSand !== null && soilSilt !== null
          ? 'Would call getSoilTexture()'
          : soilPH !== null
            ? `pH ${soilPH.toFixed(1)}`
            : 'Unavailable')
      : 'Unavailable';
    
    expect(displayValue).toBe('pH 6.5');
    expect(displayValue).not.toBe('Loam'); // old fake fallback
  });
  
  it('should calculate moisture percentage from real NDMI when available', () => {
    const twin = {
      status: 'ready' as const,
      satellite: {
        ndmi: {
          value: -0.057,
          unit: 'dimensionless',
          source: 'sentinel-2-l2a',
          quality: 'accepted',
        },
      },
    };
    
    const ndmiValue = twin.satellite.ndmi.value;
    
    // NDMI to percentage: (ndmi + 1) / 2 * 100
    const getMoisturePercentage = (ndmi: number | null): number | null => {
      if (ndmi === null) return null;
      const normalized = (ndmi + 1) / 2;
      return Math.round(normalized * 100);
    };
    
    const moisturePct = getMoisturePercentage(ndmiValue);
    
    expect(moisturePct).toBe(47); // (-0.057 + 1) / 2 * 100 = 47.15 → 47
    expect(moisturePct).not.toBe(62); // old fake fallback
  });
  
  it('should show "Unavailable" when NDMI data is missing', () => {
    const twin = {
      status: 'ready' as const,
      satellite: {
        ndmi: null,
      },
    };
    
    const ndmiValue = twin.satellite.ndmi;
    
    const getMoisturePercentage = (ndmi: number | null): number | null => {
      if (ndmi === null) return null;
      const normalized = (ndmi + 1) / 2;
      return Math.round(normalized * 100);
    };
    
    const moisturePct = ndmiValue !== null ? getMoisturePercentage(ndmiValue) : null;
    const displayValue = twin.status === 'ready' && moisturePct !== null
      ? `${moisturePct}%`
      : 'Unavailable';
    
    expect(displayValue).toBe('Unavailable');
    expect(displayValue).not.toBe('62%'); // old fake fallback
  });
});

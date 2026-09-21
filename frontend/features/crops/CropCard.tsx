'use client';

import { Check, Sprout } from 'lucide-react';

import type { SimulationResult } from '@/lib/api/crops';
import avocadoImage from '@/photos/avocado.png';
import bananaImage from '@/photos/banana.png';
import beansImage from '@/photos/beans.png';
import cabbageImage from '@/photos/cabbage.png';
import carrotImage from '@/photos/carrot.png';
import cowpeaImage from '@/photos/cowpea.png';
import maizeImage from '@/photos/maize.png';
import mangoImage from '@/photos/mango.png';
import onionImage from '@/photos/onion.png';
import orangeImage from '@/photos/orange.png';
import papayaImage from '@/photos/papaya.png';
import pineappleImage from '@/photos/pineapple.png';
import riceImage from '@/photos/rice.png';
import soybeanImage from '@/photos/soyabean.png';
import sorghumImage from '@/photos/sorghum.png';
import sweetPotatoImage from '@/photos/sweet_photo.png';
import tomatoImage from '@/photos/tomatos.png';
import wheatImage from '@/photos/wheat.png';
import { useI18n } from '@/lib/i18n/context';

type CropImageAsset = typeof beansImage;

interface CropCardProps {
  result: SimulationResult;
  category?: string;
  selected: boolean;
  onClick: () => void;
}

const CROP_GLYPHS: Array<[string, string]> = [
  ['maize', '🌽'],
  ['corn', '🌽'],
  ['bean', '🫘'],
  ['tomato', '🍅'],
  ['cabbage', '🥬'],
  ['kale', '🥬'],
  ['spinach', '🥬'],
  ['carrot', '🥕'],
  ['onion', '🧅'],
  ['potato', '🥔'],
  ['rice', '🌾'],
  ['wheat', '🌾'],
  ['sorghum', '🌾'],
  ['soy', '🫘'],
  ['pea', '🫛'],
];

const CROP_IMAGES: Array<[string, CropImageAsset]> = [
  ['sweet potato', sweetPotatoImage],
  ['soybean', soybeanImage],
  ['soyabean', soybeanImage],
  ['soy bean', soybeanImage],
  ['avocado', avocadoImage],
  ['banana', bananaImage],
  ['pineapple', pineappleImage],
  ['papaya', papayaImage],
  ['mango', mangoImage],
  ['orange', orangeImage],
  ['cowpea', cowpeaImage],
  ['maize', maizeImage],
  ['corn', maizeImage],
  ['beans', beansImage],
  ['bean', beansImage],
  ['tomatoes', tomatoImage],
  ['tomato', tomatoImage],
  ['cabbage', cabbageImage],
  ['carrot', carrotImage],
  ['onion', onionImage],
  ['rice', riceImage],
  ['wheat', wheatImage],
  ['sorghum', sorghumImage],
];

export function CropVisual({ cropName }: { cropName: string }) {
  const normalizedName = cropName.trim().toLowerCase();
  const image = CROP_IMAGES.find(([name]) => normalizedName.includes(name))?.[1];
  const glyph = CROP_GLYPHS.find(([name]) => normalizedName.includes(name))?.[1];
  const imageSrc = typeof image === 'string' ? image : image?.src;

  return (
    <span className="crop-visual" aria-hidden="true">
      {imageSrc ? (
        // oxlint-disable-next-line next/no-img-element -- Vite imports these small local crop assets and Vitest does not resolve next/image.
        <img
          className="crop-visual-image"
          src={imageSrc}
          alt=""
          loading="lazy"
          decoding="async"
        />
      ) : (
        glyph ?? <Sprout />
      )}
    </span>
  );
}

function scoreBadgeVariant(
  index: number,
  hardExclusion: boolean,
): 'green' | 'amber' | 'red' {
  if (hardExclusion) return 'red';
  if (index >= 82) return 'green';
  if (index >= 68) return 'amber';
  return 'red';
}

export function CropCard({ result, category, selected, onClick }: CropCardProps) {
  const { t } = useI18n();
  const badgeVariant = scoreBadgeVariant(result.suitability_index, result.hard_exclusion);
  const score = result.hard_exclusion ? 0 : result.suitability_index;
  
  // Determine circle color based on score
  const circleColor = '#e7ede9'; // default gray for unfilled
  let filledColor = '#c93d3d'; // red for low scores
  if (score >= 82) {
    filledColor = '#07883d'; // green for excellent
  } else if (score >= 68) {
    filledColor = '#c97b06'; // orange/yellow for moderate
  }

  return (
    <button
      type="button"
      className="crop-card workspace-card"
      data-selected={selected || undefined}
      data-score={badgeVariant}
      aria-pressed={selected}
      onClick={onClick}
    >
      {selected && (
        <span className="crop-selected-check" aria-label={t('crops.selected')}>
          <Check />
        </span>
      )}
      <div className="crop-card-main">
        <CropVisual cropName={result.crop_name} />
        <span
          className="crop-score-badge"
          data-variant={badgeVariant}
          aria-label={t('crops.scoreAria', { score })}
          style={{
            background: `conic-gradient(${filledColor} ${score * 3.6}deg, ${circleColor} 0deg)`,
          }}
        >
          <span>{score}%</span>
        </span>
      </div>
      <div className="crop-card-copy">
        <p className="crop-category">{result.crop_name}</p>
        {category && <small className="crop-meta">{category}</small>}
      </div>
      <span className="crop-status-pill" data-variant={badgeVariant}>
        {result.hard_exclusion ? t('crops.notSuitable') : result.label}
      </span>
    </button>
  );
}

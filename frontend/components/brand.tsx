'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useI18n } from '@/lib/i18n/context';

export function Brand({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  return (
    <Link href="/" className="brand" aria-label={t('brand.home')}>
      <Image
        src="/logo.png"
        alt="TerraPlan"
        width={compact ? 40 : 82}
        height={compact ? 40 : 82}
        priority
        className={compact ? 'brand-logo brand-logo-compact' : 'brand-logo'}
      />
    </Link>
  );
}

'use client';

import { Check, ChevronDown, Languages } from 'lucide-react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useI18n } from '@/lib/i18n/context';
import type { Language } from '@/lib/i18n/translations';

const choices: Language[] = ['en', 'sw'];

export function LanguageSwitcher() {
  const { language, setLanguage, t } = useI18n();
  const label = language === 'en' ? t('language.english') : t('language.kiswahili');

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="language-switcher-trigger"
        aria-label={`${t('language.label')}: ${label}`}
      >
        <Languages aria-hidden="true" />
        <span>{label}</span>
        <ChevronDown aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="language-switcher-menu">
        {choices.map((choice) => {
          const choiceLabel = choice === 'en' ? t('language.english') : t('language.kiswahili');
          return (
            <DropdownMenuItem
              key={choice}
              onClick={() => setLanguage(choice)}
              className="language-switcher-option"
            >
              <span>{choiceLabel}</span>
              {language === choice && <Check aria-hidden="true" />}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

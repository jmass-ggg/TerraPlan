import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';

import { I18nProvider, useI18n } from './context';

function Harness() {
  const { language, setLanguage, t } = useI18n();
  const [farmName, setFarmName] = useState('');
  const [points, setPoints] = useState(0);
  return (
    <div>
      <h1>{t('createFarm.title')}</h1>
      <input aria-label="farm" value={farmName} onChange={(event) => setFarmName(event.target.value)} />
      <output aria-label="points">{points}</output>
      <button type="button" onClick={() => setPoints((value) => value + 1)}>point</button>
      <button type="button" onClick={() => setLanguage(language === 'en' ? 'sw' : 'en')}>language</button>
    </div>
  );
}

describe('I18nProvider', () => {
  beforeEach(() => window.localStorage.clear());

  it('switches presentation text without clearing in-progress form or drawing state', () => {
    render(<I18nProvider><Harness /></I18nProvider>);
    fireEvent.change(screen.getByLabelText('farm'), { target: { value: 'Shamba A' } });
    fireEvent.click(screen.getByText('point'));
    fireEvent.click(screen.getByText('language'));

    expect(screen.getByRole('heading')).toHaveTextContent('Unda shamba lako');
    expect(screen.getByLabelText('farm')).toHaveValue('Shamba A');
    expect(screen.getByLabelText('points')).toHaveTextContent('1');
    expect(window.localStorage.getItem('terraplan-language')).toBe('sw');

    fireEvent.click(screen.getByText('language'));
    expect(screen.getByRole('heading')).toHaveTextContent('Create your farm');
    expect(screen.getByLabelText('farm')).toHaveValue('Shamba A');
    expect(screen.getByLabelText('points')).toHaveTextContent('1');
  });

  it('restores a persisted language after the hydration-safe English render', async () => {
    window.localStorage.setItem('terraplan-language', 'sw');
    render(<I18nProvider><Harness /></I18nProvider>);
    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('Unda shamba lako'));
  });
});

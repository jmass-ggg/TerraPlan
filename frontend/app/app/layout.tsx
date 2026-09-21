import type { Metadata } from 'next';

import { AppShell } from '@/components/app-shell';
import { I18nProvider } from '@/lib/i18n/context';

export const metadata: Metadata = { title: 'Workspace' };

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <I18nProvider><AppShell>{children}</AppShell></I18nProvider>;
}

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AppShell } from './app-shell';

const route = vi.hoisted(() => ({ path: '/app/farms/new' }));
vi.mock('next/navigation', () => ({ usePathname: () => route.path }));

describe('farm navigation', () => {
  it.each(['/app/farms/new', '/app/farms/new/twin', '/app/farms/invalid/edit'])('keeps farm tools disabled on %s', (path) => {
    route.path = path;
    render(<AppShell>Workspace</AppShell>);
    expect(screen.queryByRole('link', { name: 'Farm Digital Twin' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Crop Simulator' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Annual Crop Plan' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Disaster Center' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Climate' })).not.toBeInTheDocument();
  });
  it('enables farm tools for a saved UUID', () => {
    const id = '98c56a7f-9c69-4bc4-ae26-35ac8904d1be';
    route.path = `/app/farms/${id}/edit`;
    render(<AppShell>Workspace</AppShell>);
    expect(screen.getByRole('link', { name: 'Farm Digital Twin' })).toHaveAttribute('href', `/app/farms/${id}/twin`);
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import EditFarmPage from './page';

const api = vi.hoisted(() => ({ getFarm: vi.fn(), updateFarm: vi.fn(), triggerAnalysis: vi.fn() }));
vi.mock('@/lib/api/farms', async (original) => ({ ...await original<object>(), ...api }));
vi.mock('next/navigation', () => ({ useParams: () => ({ farmId: '98c56a7f-9c69-4bc4-ae26-35ac8904d1be' }) }));
vi.mock('@/features/farm/DeleteFarmDialog', () => ({ DeleteFarmDialog: () => null }));
vi.mock('@/features/farm/MapEditor', () => ({ MapEditor: ({ onSave, initialGeometry }: {onSave: (g: unknown) => void; initialGeometry: unknown}) => <button onClick={() => onSave(initialGeometry)}>Save boundary test</button> }));

const geometry = { type: 'Polygon', coordinates: [[[36.8,-1.3],[36.805,-1.3],[36.805,-1.295],[36.8,-1.3]]] };
beforeEach(() => {
  vi.clearAllMocks();
  const farm = { name: 'My farm', current_geometry_revision: 1, current_geometry: { id: 'revision-one', geometry } };
  api.getFarm.mockResolvedValue(farm);
  api.updateFarm.mockResolvedValue({ ...farm, current_geometry_revision: 2, current_geometry: { id: 'revision-two', geometry } });
  api.triggerAnalysis.mockResolvedValue({ jobId: 'job' });
});

function mount() {
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}><EditFarmPage /></QueryClientProvider>);
}

it('saves with optimistic revision and starts analysis after success', async () => {
  mount();
  await userEvent.click(await screen.findByRole('button', { name: 'Save boundary test' }));
  await waitFor(() => expect(api.triggerAnalysis).toHaveBeenCalled());
  expect(api.updateFarm).toHaveBeenCalledWith('98c56a7f-9c69-4bc4-ae26-35ac8904d1be', {name: 'My farm', geometry, expected_revision: 1});
  expect(api.updateFarm.mock.invocationCallOrder[0]).toBeLessThan(api.triggerAnalysis.mock.invocationCallOrder[0]);
  expect(screen.getByText('Boundary revision 2')).toBeInTheDocument();
});

it('keeps the saved revision and offers retry when analysis cannot start', async () => {
  api.triggerAnalysis.mockRejectedValueOnce(new Error('Offline'));
  mount();
  await userEvent.click(await screen.findByRole('button', { name: 'Save boundary test' }));
  const retry = await screen.findByRole('button', { name: 'Retry analysis' });
  expect(screen.getByText('Boundary revision 2')).toBeInTheDocument();
  await userEvent.click(retry);
  await waitFor(() => expect(screen.queryByRole('button', {name: 'Retry analysis'})).not.toBeInTheDocument());
  expect(api.updateFarm).toHaveBeenCalledTimes(1);
  expect(api.triggerAnalysis).toHaveBeenCalledTimes(2);
});

it('keeps the farm name input focused and saves the complete edited name', async () => {
  const updatedFarm = {
    name: 'Green Valley Farm',
    current_geometry_revision: 1,
    current_geometry: { id: 'revision-one', geometry },
  };
  api.updateFarm.mockResolvedValueOnce(updatedFarm);
  mount();

  const nameInput = await screen.findByLabelText('Farm name');
  await userEvent.clear(nameInput);
  await userEvent.type(nameInput, 'Green Valley Farm');

  expect(nameInput).toHaveValue('Green Valley Farm');
  expect(nameInput).toHaveFocus();
  expect(api.updateFarm).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole('button', { name: 'Save name' }));
  await waitFor(() => expect(api.updateFarm).toHaveBeenCalledWith(
    '98c56a7f-9c69-4bc4-ae26-35ac8904d1be',
    { name: 'Green Valley Farm' },
  ));
});

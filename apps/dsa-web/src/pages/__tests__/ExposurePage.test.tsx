import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import ExposurePage from '../ExposurePage';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';

vi.mock('../../api/exposure', () => ({
  exposureApi: {
    listEdges: vi.fn().mockResolvedValue({
      items: [
        {
          id: 1,
          code: '600584',
          targetEntityId: 'semi_packaging',
          linkType: 'revenue_share',
          strength: 'high',
          direction: 'positive',
          pricingDriver: 'core_business',
          source: 'theme_pack',
          ttlDays: 90,
          isDisabled: false,
        },
      ],
      total: 1,
      limit: 100,
      offset: 0,
    }),
    listEventSignals: vi.fn().mockResolvedValue({
      items: [
        {
          id: 2,
          sourceType: 'news',
          sourceUrl: 'https://example.com',
          title: '存储扩产',
          status: 'pending',
          entities: ['storage'],
          matchedCodes: [{ code: '600584' }],
        },
      ],
      total: 1,
      limit: 30,
      offset: 0,
    }),
    submitEdgeFeedback: vi.fn(),
    submitEventFeedback: vi.fn(),
  },
}));

function renderPage() {
  return render(
    <UiLanguageProvider>
      <MemoryRouter>
        <ExposurePage />
      </MemoryRouter>
    </UiLanguageProvider>,
  );
}

describe('ExposurePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders exposure edges and events', async () => {
    renderPage();
    expect(await screen.findByText('暴露图谱')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/600584/)).toBeInTheDocument();
      expect(screen.getByText('存储扩产')).toBeInTheDocument();
    });
  });
});

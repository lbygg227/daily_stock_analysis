import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import FundamentalsPage from '../FundamentalsPage';

vi.mock('../../api/fundamentals', () => ({
  fundamentalsApi: {
    getCacheStats: vi.fn().mockResolvedValue({
      listedCount: 10,
      financialCoverageCount: 8,
      industryCoverageCount: 2,
      lastListingSyncAt: '2026-06-21 15:44:41',
      latestFinancialReportPeriod: '2026-03-31',
    }),
    listIndustries: vi.fn().mockResolvedValue({
      total: 1,
      items: [{ name: '白酒', stockCount: 3 }],
    }),
    listStocks: vi.fn().mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [
        {
          code: '600519',
          name: '贵州茅台',
          market: 'SH',
          industryThs: '白酒',
          status: 'listed',
          hasFinancial: true,
          revenue: 100,
          roe: 30,
        },
      ],
    }),
    getStockDetail: vi.fn(),
  },
  formatIndustryLabel: (name: string) => name,
  UNCLASSIFIED_INDUSTRY_KEY: '__UNCLASSIFIED__',
}));

describe('FundamentalsPage', () => {
  it('renders stats and stock table', async () => {
    render(
      <MemoryRouter>
        <FundamentalsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('基本面数据')).toBeInTheDocument();
    });

    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
  });
});

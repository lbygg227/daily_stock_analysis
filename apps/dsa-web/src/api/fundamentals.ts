import apiClient from './index';

// ========================================================================
// Types
// ========================================================================

export const UNCLASSIFIED_INDUSTRY_KEY = '__UNCLASSIFIED__';

export interface StockListItem {
  code: string;
  name: string;
  market: string;
  industryThs?: string;
  sectorName?: string;
  status: string;
  listingDate?: string;
  hasFinancial?: boolean;
  latestReportPeriod?: string;
  revenue?: number;
  revenueYoy?: number;
  netMargin?: number;
  roe?: number;
}

export interface StockListResponse {
  total: number;
  page: number;
  limit: number;
  items: StockListItem[];
}

export interface IndustryItem {
  name: string;
  stockCount: number;
}

export interface IndustryListResponse {
  total: number;
  items: IndustryItem[];
}

export interface FundamentalsCacheStats {
  listedCount: number;
  financialCoverageCount: number;
  industryCoverageCount: number;
  lastListingSyncAt?: string;
  latestFinancialReportPeriod?: string;
}

export interface FinancialIndicator {
  reportPeriod: string;
  netProfit?: number;
  netProfitYoy?: number;
  revenue?: number;
  revenueYoy?: number;
  eps?: number;
  bvps?: number;
  grossMargin?: number;
  netMargin?: number;
  roe?: number;
  roeDiluted?: number;
  currentRatio?: number;
  quickRatio?: number;
  debtRatio?: number;
  capitalReservePs?: number;
  retainedEarningsPs?: number;
  operatingCfPs?: number;
  inventoryTurnover?: number;
  receivablesTurnoverDays?: number;
  operatingCycle?: number;
}

export interface StockDetailResponse {
  code: string;
  name: string;
  market: string;
  industryThs?: string;
  sectorName?: string;
  status: string;
  listingDate?: string;
  latestFinancial?: FinancialIndicator;
  financialHistory: FinancialIndicator[];
}

export interface FinancialHistoryResponse {
  code: string;
  name?: string;
  periods: FinancialIndicator[];
}

export type StockSortBy = 'code' | 'roe' | 'revenue' | 'revenue_yoy' | 'net_margin';
export type StockSortOrder = 'asc' | 'desc';

// ========================================================================
// Mappers
// ========================================================================

function mapFinancial(f: Record<string, unknown> | undefined): FinancialIndicator | undefined {
  if (!f) return undefined;
  return {
    reportPeriod: f.report_period as string,
    netProfit: f.net_profit as number | undefined,
    netProfitYoy: f.net_profit_yoy as number | undefined,
    revenue: f.revenue as number | undefined,
    revenueYoy: f.revenue_yoy as number | undefined,
    eps: f.eps as number | undefined,
    bvps: f.bvps as number | undefined,
    grossMargin: f.gross_margin as number | undefined,
    netMargin: f.net_margin as number | undefined,
    roe: f.roe as number | undefined,
    roeDiluted: f.roe_diluted as number | undefined,
    currentRatio: f.current_ratio as number | undefined,
    quickRatio: f.quick_ratio as number | undefined,
    debtRatio: f.debt_ratio as number | undefined,
    capitalReservePs: f.capital_reserve_ps as number | undefined,
    retainedEarningsPs: f.retained_earnings_ps as number | undefined,
    operatingCfPs: f.operating_cf_ps as number | undefined,
    inventoryTurnover: f.inventory_turnover as number | undefined,
    receivablesTurnoverDays: f.receivables_turnover_days as number | undefined,
    operatingCycle: f.operating_cycle as number | undefined,
  };
}

function mapStockListItem(item: Record<string, unknown>): StockListItem {
  return {
    code: item.code as string,
    name: item.name as string,
    market: item.market as string,
    industryThs: item.industry_ths as string | undefined,
    sectorName: item.sector_name as string | undefined,
    status: item.status as string,
    listingDate: item.listing_date as string | undefined,
    hasFinancial: Boolean(item.has_financial),
    latestReportPeriod: item.latest_report_period as string | undefined,
    revenue: item.revenue as number | undefined,
    revenueYoy: item.revenue_yoy as number | undefined,
    netMargin: item.net_margin as number | undefined,
    roe: item.roe as number | undefined,
  };
}

// ========================================================================
// API
// ========================================================================

export const fundamentalsApi = {
  async getCacheStats(): Promise<FundamentalsCacheStats> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/fundamentals/stats');
    const data = response.data;
    return {
      listedCount: Number(data.listed_count ?? 0),
      financialCoverageCount: Number(data.financial_coverage_count ?? 0),
      industryCoverageCount: Number(data.industry_coverage_count ?? 0),
      lastListingSyncAt: data.last_listing_sync_at as string | undefined,
      latestFinancialReportPeriod: data.latest_financial_report_period as string | undefined,
    };
  },

  async listIndustries(): Promise<IndustryListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/fundamentals/industries');
    const data = response.data;
    return {
      total: Number(data.total ?? 0),
      items: ((data.items as Array<Record<string, unknown>>) || []).map((item) => ({
        name: item.name as string,
        stockCount: Number(item.stock_count ?? 0),
      })),
    };
  },

  async listStocks(params: {
    search?: string;
    market?: string;
    industry?: string;
    industryExact?: boolean;
    sortBy?: StockSortBy;
    sortOrder?: StockSortOrder;
    page?: number;
    limit?: number;
  }): Promise<StockListResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/fundamentals/stocks',
      {
        params: {
          search: params.search,
          market: params.market,
          industry: params.industry,
          industry_exact: params.industryExact,
          sort_by: params.sortBy,
          sort_order: params.sortOrder,
          page: params.page,
          limit: params.limit,
        },
      },
    );
    const data = response.data;
    return {
      total: Number(data.total ?? 0),
      page: Number(data.page ?? 1),
      limit: Number(data.limit ?? 20),
      items: ((data.items as Array<Record<string, unknown>>) || []).map(mapStockListItem),
    };
  },

  async getStockDetail(code: string): Promise<StockDetailResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/fundamentals/stocks/${code}`,
    );
    const data = response.data;
    return {
      code: data.code as string,
      name: data.name as string,
      market: data.market as string,
      industryThs: data.industry_ths as string | undefined,
      sectorName: data.sector_name as string | undefined,
      status: data.status as string,
      listingDate: data.listing_date as string | undefined,
      latestFinancial: mapFinancial(data.latest_financial as Record<string, unknown> | undefined),
      financialHistory: ((data.financial_history as Array<Record<string, unknown>>) || [])
        .map((f) => mapFinancial(f)!)
        .filter(Boolean),
    };
  },

  async getFinancialHistory(
    code: string,
    limit?: number,
  ): Promise<FinancialHistoryResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/fundamentals/stocks/${code}/financials`,
      { params: limit !== undefined ? { limit } : undefined },
    );
    const data = response.data;
    return {
      code: data.code as string,
      name: data.name as string | undefined,
      periods: ((data.periods as Array<Record<string, unknown>>) || [])
        .map((p) => mapFinancial(p)!)
        .filter(Boolean),
    };
  },
};

export function formatIndustryLabel(name: string): string {
  return name === UNCLASSIFIED_INDUSTRY_KEY ? '未分类' : name;
}

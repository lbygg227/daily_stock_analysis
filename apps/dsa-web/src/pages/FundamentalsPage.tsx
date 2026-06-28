import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BarChart3,
  Building2,
  Database,
  Layers3,
  MessageSquare,
  Search,
  TrendingUp,
} from 'lucide-react';
import { type ParsedApiError, getParsedApiError } from '../api/error';
import {
  fundamentalsApi,
  formatIndustryLabel,
  type FinancialIndicator,
  type FundamentalsCacheStats,
  type IndustryItem,
  type StockListItem,
  type StockSortBy,
  type StockSortOrder,
} from '../api/fundamentals';
import {
  AppPage,
  Badge,
  Card,
  Drawer,
  EmptyState,
  Input,
  Loading,
  PageHeader,
  Pagination,
  Select,
  StatCard,
} from '../components/common';

const PAGE_SIZE = 20;

type ViewTab = 'search' | 'industry';

// ========================================================================
// Helpers
// ========================================================================

function formatAmount(value?: number): string {
  if (value === undefined || value === null) return '-';
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}万亿`;
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

function formatPercent(value?: number): string {
  if (value === undefined || value === null) return '-';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function marketLabel(market: string): string {
  const map: Record<string, string> = { SH: '沪市', SZ: '深市', BJ: '北交所' };
  return map[market] || market;
}

function marketBadge(market: string): 'default' | 'success' | 'info' | 'warning' | 'danger' {
  const map: Record<string, 'default' | 'success' | 'info' | 'warning' | 'danger'> = {
    SH: 'danger',
    SZ: 'success',
    BJ: 'warning',
  };
  return map[market] || 'default';
}

function buildAnalyzeLink(code: string, name: string): string {
  const params = new URLSearchParams({ stock: code, name });
  return `/chat?${params.toString()}`;
}

// ========================================================================
// Sub-components
// ========================================================================

interface StockTableProps {
  stocks: StockListItem[];
  loading: boolean;
  onOpenDetail: (code: string) => void;
}

const StockTable: React.FC<StockTableProps> = ({ stocks, loading, onOpenDetail }) => {
  if (loading) {
    return <Loading />;
  }
  if (stocks.length === 0) {
    return null;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[960px] border-collapse text-sm">
        <thead className="border-b border-border bg-surface text-left text-xs text-secondary-text">
          <tr>
            <th className="px-4 py-3 font-semibold">代码</th>
            <th className="px-4 py-3 font-semibold">名称</th>
            <th className="px-4 py-3 font-semibold">市场</th>
            <th className="px-4 py-3 font-semibold">行业</th>
            <th className="px-4 py-3 text-right font-semibold">营收 (最新)</th>
            <th className="px-4 py-3 text-right font-semibold">营收同比</th>
            <th className="px-4 py-3 text-right font-semibold">净利率</th>
            <th className="px-4 py-3 text-right font-semibold">ROE</th>
            <th className="px-4 py-3 text-center font-semibold">操作</th>
          </tr>
        </thead>
        <tbody>
          {stocks.map((stock) => (
            <tr
              key={stock.code}
              className="border-b border-border align-top transition-colors hover:bg-hover/50"
            >
              <td className="px-4 py-3 font-mono text-xs font-medium">{stock.code}</td>
              <td className="px-4 py-3 font-medium">
                <div className="flex items-center gap-2">
                  <span>{stock.name}</span>
                  {!stock.hasFinancial && (
                    <Badge variant="warning">无财务</Badge>
                  )}
                </div>
              </td>
              <td className="px-4 py-3">
                <Badge variant={marketBadge(stock.market)}>{marketLabel(stock.market)}</Badge>
              </td>
              <td className="px-4 py-3 text-secondary-text">
                {stock.industryThs ? formatIndustryLabel(stock.industryThs) : '未分类'}
              </td>
              <td className="px-4 py-3 text-right font-mono text-xs">
                {formatAmount(stock.revenue)}
              </td>
              <td className="px-4 py-3 text-right font-mono text-xs">
                {formatPercent(stock.revenueYoy)}
              </td>
              <td className="px-4 py-3 text-right font-mono text-xs">
                {stock.netMargin != null ? `${stock.netMargin.toFixed(2)}%` : '-'}
              </td>
              <td className="px-4 py-3 text-right font-mono text-xs">
                {stock.roe != null ? `${stock.roe.toFixed(2)}%` : '-'}
              </td>
              <td className="px-4 py-3 text-center">
                <div className="flex items-center justify-center gap-2">
                  <button
                    type="button"
                    className="btn-ghost btn-sm text-xs"
                    onClick={() => onOpenDetail(stock.code)}
                  >
                    详情
                  </button>
                  <Link
                    to={buildAnalyzeLink(stock.code, stock.name)}
                    className="btn-ghost btn-sm text-xs inline-flex items-center"
                  >
                    分析
                  </Link>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

// ========================================================================
// Component
// ========================================================================

const FundamentalsPage: React.FC = () => {
  useEffect(() => {
    document.title = '基本面数据 - DSA';
  }, []);

  const [activeTab, setActiveTab] = useState<ViewTab>('search');
  const [stats, setStats] = useState<FundamentalsCacheStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [industries, setIndustries] = useState<IndustryItem[]>([]);
  const [industriesLoading, setIndustriesLoading] = useState(false);
  const [selectedIndustry, setSelectedIndustry] = useState<string | null>(null);

  const [stocks, setStocks] = useState<StockListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const [searchText, setSearchText] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [marketFilter, setMarketFilter] = useState('');
  const [sortBy, setSortBy] = useState<StockSortBy>('code');
  const [sortOrder, setSortOrder] = useState<StockSortOrder>('asc');

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    code: string;
    name: string;
    market: string;
    industryThs?: string;
    latestFinancial?: FinancialIndicator;
    financialHistory: FinancialIndicator[];
  } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<ParsedApiError | null>(null);

  const requestIdRef = useRef(0);

  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const resp = await fundamentalsApi.getCacheStats();
      setStats(resp);
    } catch {
      setStats(null);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  const loadIndustries = useCallback(async () => {
    setIndustriesLoading(true);
    try {
      const resp = await fundamentalsApi.listIndustries();
      setIndustries(resp.items);
      setSelectedIndustry((current) => current ?? resp.items[0]?.name ?? null);
    } catch {
      setIndustries([]);
    } finally {
      setIndustriesLoading(false);
    }
  }, []);

  const loadStocks = useCallback(
    async (pageOverride?: number) => {
      const requestId = requestIdRef.current + 1;
      requestIdRef.current = requestId;
      const isLatest = () => requestIdRef.current === requestId;

      setLoading(true);
      try {
        const resp = await fundamentalsApi.listStocks({
          search: searchText || undefined,
          market: marketFilter || undefined,
          industry:
            activeTab === 'industry'
              ? selectedIndustry || undefined
              : undefined,
          sortBy,
          sortOrder,
          page: pageOverride ?? page,
          limit: PAGE_SIZE,
        });
        if (!isLatest()) return;
        setStocks(resp.items);
        setTotal(resp.total);
        setError(null);
      } catch (err) {
        if (!isLatest()) return;
        setError(getParsedApiError(err));
      } finally {
        if (isLatest()) setLoading(false);
      }
    },
    [
      activeTab,
      marketFilter,
      page,
      searchText,
      selectedIndustry,
      sortBy,
      sortOrder,
    ],
  );

  const openDetail = useCallback(async (code: string) => {
    setSelectedCode(code);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const resp = await fundamentalsApi.getStockDetail(code);
      setDetail({
        code: resp.code,
        name: resp.name,
        market: resp.market,
        industryThs: resp.industryThs,
        latestFinancial: resp.latestFinancial,
        financialHistory: resp.financialHistory,
      });
      setDetailError(null);
    } catch (err) {
      setDetailError(getParsedApiError(err));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedCode(null);
    setDetail(null);
    setDetailError(null);
  }, []);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  useEffect(() => {
    if (activeTab === 'industry') {
      void loadIndustries();
    }
  }, [activeTab, loadIndustries]);

  useEffect(() => {
    void loadStocks();
  }, [loadStocks]);

  const handleSearch = () => {
    setSearchText(searchInput);
    setPage(1);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSearch();
  };

  const emptyDescription = useMemo(() => {
    if (stats?.listedCount === 0) {
      return '本地库暂无数据，请先运行 python main.py --sync-fundamentals 同步基本面数据。';
    }
    if (activeTab === 'industry') {
      return selectedIndustry
        ? `行业「${formatIndustryLabel(selectedIndustry)}」下暂无股票`
        : '请选择左侧行业分类';
    }
    if (searchText) {
      return `未找到与「${searchText}」匹配的股票`;
    }
    return '暂无股票数据';
  }, [activeTab, searchText, selectedIndustry, stats?.listedCount]);

  const tabButtonClass = (tab: ViewTab) =>
    `rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
      activeTab === tab
        ? 'bg-primary text-primary-foreground'
        : 'bg-surface text-secondary-text hover:bg-hover'
    }`;

  return (
    <AppPage>
      <PageHeader
        title="基本面数据"
        description="浏览本地 SQLite 中的股票清单与季度财务摘要，支持搜索与行业分类查看"
      />

      <Card className="mb-5">
        {statsLoading ? (
          <Loading />
        ) : (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              label="上市股票"
              value={stats ? String(stats.listedCount) : '-'}
              icon={<Database className="h-4 w-4" />}
            />
            <StatCard
              label="有财务数据"
              value={stats ? String(stats.financialCoverageCount) : '-'}
              icon={<BarChart3 className="h-4 w-4" />}
            />
            <StatCard
              label="有行业分类"
              value={stats ? String(stats.industryCoverageCount) : '-'}
              icon={<Layers3 className="h-4 w-4" />}
            />
            <StatCard
              label="最近同步 / 最新财报"
              value={
                stats?.lastListingSyncAt
                  ? stats.lastListingSyncAt.slice(0, 16)
                  : '未同步'
              }
              icon={<TrendingUp className="h-4 w-4" />}
            />
          </div>
        )}
        {!statsLoading && stats?.listedCount === 0 && (
          <p className="mt-3 text-sm text-warning">
            本地库为空。请运行 <code>python main.py --sync-fundamentals</code> 导入数据；
            行业分类需额外开启 <code>--sync-industry</code> 或闲时同步配置。
          </p>
        )}
        {!statsLoading && stats && stats.latestFinancialReportPeriod && (
          <p className="mt-2 text-xs text-secondary-text">
            库中最新财务报告期：{stats.latestFinancialReportPeriod}
          </p>
        )}
      </Card>

      <div className="mb-4 flex flex-wrap gap-2">
        <button type="button" className={tabButtonClass('search')} onClick={() => setActiveTab('search')}>
          搜索浏览
        </button>
        <button
          type="button"
          className={tabButtonClass('industry')}
          onClick={() => {
            setActiveTab('industry');
            setPage(1);
          }}
        >
          行业分类
        </button>
      </div>

      {activeTab === 'search' && (
        <Card className="mb-5">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1" style={{ minWidth: 240 }}>
              <Input
                label="搜索"
                placeholder="输入股票代码或名称（支持模糊匹配）..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={handleKeyDown}
              />
            </div>
            <div style={{ minWidth: 120 }}>
              <Select
                label="市场"
                value={marketFilter}
                onChange={(value) => {
                  setMarketFilter(value);
                  setPage(1);
                }}
                options={[
                  { value: '', label: '全部' },
                  { value: 'SH', label: '沪市' },
                  { value: 'SZ', label: '深市' },
                  { value: 'BJ', label: '北交所' },
                ]}
              />
            </div>
            <div style={{ minWidth: 140 }}>
              <Select
                label="排序"
                value={sortBy}
                onChange={(value) => {
                  setSortBy(value as StockSortBy);
                  setPage(1);
                }}
                options={[
                  { value: 'code', label: '代码' },
                  { value: 'roe', label: 'ROE' },
                  { value: 'revenue', label: '营收' },
                  { value: 'revenue_yoy', label: '营收同比' },
                  { value: 'net_margin', label: '净利率' },
                ]}
              />
            </div>
            <div style={{ minWidth: 100 }}>
              <Select
                label="方向"
                value={sortOrder}
                onChange={(value) => {
                  setSortOrder(value as StockSortOrder);
                  setPage(1);
                }}
                options={[
                  { value: 'asc', label: '升序' },
                  { value: 'desc', label: '降序' },
                ]}
              />
            </div>
            <div className="flex items-end">
              <button type="button" className="btn-primary" onClick={handleSearch}>
                <Search className="mr-1.5 h-4 w-4" />
                搜索
              </button>
            </div>
          </div>
        </Card>
      )}

      <Card>
        {activeTab === 'industry' ? (
          <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
            <div className="max-h-[640px] overflow-y-auto rounded-xl border border-border bg-surface p-2">
              {industriesLoading ? (
                <Loading />
              ) : industries.length === 0 ? (
                <p className="px-2 py-3 text-sm text-secondary-text">
                  暂无行业数据。请运行带 <code>--sync-industry</code> 的同步任务。
                </p>
              ) : (
                <div className="space-y-1">
                  {industries.map((industry) => {
                    const active = selectedIndustry === industry.name;
                    return (
                      <button
                        key={industry.name}
                        type="button"
                        className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                          active ? 'bg-primary/10 text-primary' : 'hover:bg-hover'
                        }`}
                        onClick={() => {
                          setSelectedIndustry(industry.name);
                          setPage(1);
                        }}
                      >
                        <span>{formatIndustryLabel(industry.name)}</span>
                        <Badge variant="default">{industry.stockCount}</Badge>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            <div>
              {selectedIndustry && (
                <div className="mb-3 text-sm text-secondary-text">
                  当前行业：{formatIndustryLabel(selectedIndustry)}
                </div>
              )}

              {error && !loading && (
                <div className="mb-3 rounded-lg border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
                  {error.message}
                </div>
              )}

              {!loading && !error && stocks.length === 0 && (
                <EmptyState
                  icon={<Search className="h-10 w-10 text-secondary-text" />}
                  title="暂无数据"
                  description={emptyDescription}
                />
              )}

              <StockTable
                stocks={stocks}
                loading={loading}
                onOpenDetail={(code) => void openDetail(code)}
              />

              {!loading && stocks.length > 0 && total > PAGE_SIZE && (
                <div className="mt-4 flex justify-center">
                  <Pagination
                    currentPage={page}
                    totalPages={Math.ceil(total / PAGE_SIZE)}
                    onPageChange={(nextPage) => setPage(nextPage)}
                  />
                </div>
              )}
            </div>
          </div>
        ) : (
          <>
            {error && !loading && (
              <div className="rounded-lg border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
                {error.message}
              </div>
            )}

            {!loading && !error && stocks.length === 0 && (
              <EmptyState
                icon={<Search className="h-10 w-10 text-secondary-text" />}
                title="暂无数据"
                description={emptyDescription}
              />
            )}

            <StockTable
              stocks={stocks}
              loading={loading}
              onOpenDetail={(code) => void openDetail(code)}
            />

            {!loading && stocks.length > 0 && total > PAGE_SIZE && (
              <div className="mt-4 flex justify-center">
                <Pagination
                  currentPage={page}
                  totalPages={Math.ceil(total / PAGE_SIZE)}
                  onPageChange={(nextPage) => setPage(nextPage)}
                />
              </div>
            )}
          </>
        )}
      </Card>

      <Drawer
        isOpen={selectedCode !== null}
        onClose={closeDetail}
        title={detail ? `${detail.code} ${detail.name}` : '加载中...'}
      >
        {detailLoading && <Loading />}

        {detailError && !detailLoading && (
          <div className="rounded-lg border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
            {detailError.message}
          </div>
        )}

        {detail && !detailLoading && (
          <div className="space-y-5">
            <div className="flex flex-wrap gap-2">
              <Link
                to={buildAnalyzeLink(detail.code, detail.name)}
                className="btn-primary btn-sm inline-flex items-center"
              >
                <MessageSquare className="mr-1.5 h-4 w-4" />
                发起分析
              </Link>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <StatCard
                label="行业"
                value={detail.industryThs ? formatIndustryLabel(detail.industryThs) : '未分类'}
                icon={<Building2 className="h-4 w-4" />}
              />
              <StatCard
                label="市场"
                value={marketLabel(detail.market)}
                icon={<BarChart3 className="h-4 w-4" />}
              />
              {detail.latestFinancial && (
                <>
                  <StatCard
                    label="最新营收"
                    value={formatAmount(detail.latestFinancial.revenue)}
                    icon={<TrendingUp className="h-4 w-4" />}
                  />
                  <StatCard
                    label="净利率"
                    value={
                      detail.latestFinancial.netMargin !== undefined
                        ? `${detail.latestFinancial.netMargin.toFixed(2)}%`
                        : '-'
                    }
                  />
                  <StatCard
                    label="ROE"
                    value={
                      detail.latestFinancial.roe !== undefined
                        ? `${detail.latestFinancial.roe.toFixed(2)}%`
                        : '-'
                    }
                  />
                  <StatCard
                    label="毛利率"
                    value={
                      detail.latestFinancial.grossMargin !== undefined
                        ? `${detail.latestFinancial.grossMargin.toFixed(2)}%`
                        : '-'
                    }
                  />
                  <StatCard
                    label="EPS"
                    value={
                      detail.latestFinancial.eps !== undefined
                        ? detail.latestFinancial.eps.toFixed(2)
                        : '-'
                    }
                  />
                  <StatCard
                    label="每股净资产"
                    value={
                      detail.latestFinancial.bvps !== undefined
                        ? detail.latestFinancial.bvps.toFixed(2)
                        : '-'
                    }
                  />
                  <StatCard
                    label="资产负债率"
                    value={
                      detail.latestFinancial.debtRatio !== undefined
                        ? `${detail.latestFinancial.debtRatio.toFixed(2)}%`
                        : '-'
                    }
                  />
                  <StatCard
                    label="流动比率"
                    value={
                      detail.latestFinancial.currentRatio !== undefined
                        ? detail.latestFinancial.currentRatio.toFixed(2)
                        : '-'
                    }
                  />
                  <StatCard
                    label="营收同比"
                    value={formatPercent(detail.latestFinancial.revenueYoy)}
                  />
                  <StatCard
                    label="净利润同比"
                    value={formatPercent(detail.latestFinancial.netProfitYoy)}
                  />
                </>
              )}
            </div>

            {detail.financialHistory.length > 0 && (
              <div>
                <h4 className="mb-3 text-sm font-semibold">财务历史（最近 8 期）</h4>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[600px] border-collapse text-xs">
                    <thead className="border-b border-border bg-surface text-left text-secondary-text">
                      <tr>
                        <th className="px-3 py-2 font-semibold">报告期</th>
                        <th className="px-3 py-2 text-right font-semibold">营收</th>
                        <th className="px-3 py-2 text-right font-semibold">净利润</th>
                        <th className="px-3 py-2 text-right font-semibold">ROE</th>
                        <th className="px-3 py-2 text-right font-semibold">毛利率</th>
                        <th className="px-3 py-2 text-right font-semibold">净利率</th>
                        <th className="px-3 py-2 text-right font-semibold">EPS</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.financialHistory.map((item) => (
                        <tr
                          key={item.reportPeriod}
                          className="border-b border-border transition-colors hover:bg-hover/50"
                        >
                          <td className="px-3 py-2 font-mono">{item.reportPeriod}</td>
                          <td className="px-3 py-2 text-right font-mono">
                            {formatAmount(item.revenue)}
                          </td>
                          <td className="px-3 py-2 text-right font-mono">
                            {formatAmount(item.netProfit)}
                          </td>
                          <td className="px-3 py-2 text-right font-mono">
                            {item.roe != null ? `${item.roe.toFixed(2)}%` : '-'}
                          </td>
                          <td className="px-3 py-2 text-right font-mono">
                            {item.grossMargin != null ? `${item.grossMargin.toFixed(2)}%` : '-'}
                          </td>
                          <td className="px-3 py-2 text-right font-mono">
                            {item.netMargin != null ? `${item.netMargin.toFixed(2)}%` : '-'}
                          </td>
                          <td className="px-3 py-2 text-right font-mono">
                            {item.eps != null ? item.eps.toFixed(2) : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </AppPage>
  );
};

export default FundamentalsPage;

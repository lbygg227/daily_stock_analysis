import { useCallback, useEffect, useState } from 'react';
import { exposureApi } from '../api/exposure';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  AppPage,
  Button,
  Card,
  EmptyState,
  Input,
  Loading,
  PageHeader,
} from '../components/common';
import type { CompanyExposureItem, EventSignalItem } from '../types/exposure';

export function ExposurePage() {
  const [codeFilter, setCodeFilter] = useState('');
  const [edges, setEdges] = useState<CompanyExposureItem[]>([]);
  const [events, setEvents] = useState<EventSignalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [edgeResp, eventResp] = await Promise.all([
        exposureApi.listEdges({
          code: codeFilter.trim() || undefined,
          includeDisabled: true,
          limit: 100,
        }),
        exposureApi.listEventSignals({ limit: 30 }),
      ]);
      setEdges(edgeResp.items ?? []);
      setEvents(eventResp.items ?? []);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [codeFilter]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleDisableEdge = async (edge: CompanyExposureItem) => {
    setBusyId(edge.id);
    try {
      await exposureApi.submitEdgeFeedback(edge.id, {
        feedbackType: 'inaccurate',
        note: 'Web：关联不准',
      });
      await loadData();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setBusyId(null);
    }
  };

  const handleFalsePositiveEvent = async (signal: EventSignalItem) => {
    setBusyId(signal.id);
    try {
      await exposureApi.submitEventFeedback(signal.id, {
        feedbackType: 'false_positive',
        note: 'Web：误报',
      });
      await loadData();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <AppPage>
      <PageHeader
        title="暴露图谱"
        description="管理产业链暴露边、标记误报并查看事件 inbox"
      />
      {error ? <ApiErrorAlert error={error} className="mb-4" /> : null}

      <Card className="mb-6 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[12rem] flex-1">
            <label className="mb-1 block text-xs text-secondary-text">股票代码筛选</label>
            <Input
              value={codeFilter}
              onChange={(event) => setCodeFilter(event.target.value)}
              placeholder="如 002208"
            />
          </div>
          <Button onClick={() => void loadData()}>刷新</Button>
        </div>
      </Card>

      {loading ? (
        <Loading label="加载暴露图谱..." />
      ) : (
        <div className="grid gap-6 xl:grid-cols-2">
          <Card className="p-4">
            <h2 className="mb-3 text-base font-semibold">暴露边</h2>
            {edges.length === 0 ? (
              <EmptyState title="暂无暴露边" description="可先导入主题包或运行公告抽取" />
            ) : (
              <div className="space-y-3">
                {edges.map((edge) => (
                  <div
                    key={edge.id}
                    className="rounded-xl border border-border/70 p-3 text-sm"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-medium">
                        {edge.code} → {edge.targetEntityId}
                        {edge.isDisabled ? (
                          <span className="ml-2 text-xs text-amber-600">已禁用</span>
                        ) : null}
                      </div>
                      <span className="text-xs text-secondary-text">{edge.source}</span>
                    </div>
                    <div className="mt-1 text-secondary-text">
                      {edge.linkType} · {edge.strength} · {edge.summary || '—'}
                    </div>
                    {!edge.isDisabled ? (
                      <div className="mt-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busyId === edge.id}
                          onClick={() => void handleDisableEdge(edge)}
                        >
                          标记关联不准
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card className="p-4">
            <h2 className="mb-3 text-base font-semibold">事件 Inbox</h2>
            {events.length === 0 ? (
              <EmptyState title="暂无事件" description="开启 ExposureEventWorker 后可见" />
            ) : (
              <div className="space-y-3">
                {events.map((event) => (
                  <div
                    key={event.id}
                    className="rounded-xl border border-border/70 p-3 text-sm"
                  >
                    <div className="font-medium">{event.title}</div>
                    <div className="mt-1 text-xs text-secondary-text">
                      {event.status}
                      {event.resonanceSector ? ` · 板块：${event.resonanceSector}` : ''}
                    </div>
                    {event.status !== 'skipped' || event.skipReason !== 'user_false_positive' ? (
                      <div className="mt-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busyId === event.id}
                          onClick={() => void handleFalsePositiveEvent(event)}
                        >
                          标记误报
                        </Button>
                      </div>
                    ) : (
                      <div className="mt-2 text-xs text-amber-600">已标记误报</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </AppPage>
  );
}

export default ExposurePage;

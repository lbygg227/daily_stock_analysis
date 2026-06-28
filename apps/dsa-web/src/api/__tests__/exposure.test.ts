import { beforeEach, describe, expect, it, vi } from 'vitest';
import { exposureApi } from '../exposure';

const { get } = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../index', () => ({
  default: {
    get,
  },
}));

describe('exposureApi', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('uses /api/v1 prefixed paths', async () => {
    get.mockResolvedValueOnce({
      data: { items: [], total: 0, limit: 100, offset: 0 },
    });
    get.mockResolvedValueOnce({
      data: { items: [], total: 0, limit: 30, offset: 0 },
    });

    await exposureApi.listEdges({ limit: 100 });
    await exposureApi.listEventSignals({ limit: 30 });

    expect(get).toHaveBeenNthCalledWith(1, '/api/v1/exposure/edges', {
      params: {
        code: undefined,
        entity_id: undefined,
        source: undefined,
        include_disabled: undefined,
        limit: 100,
        offset: undefined,
      },
    });
    expect(get).toHaveBeenNthCalledWith(2, '/api/v1/events/signals', {
      params: { limit: 30 },
    });
  });
});

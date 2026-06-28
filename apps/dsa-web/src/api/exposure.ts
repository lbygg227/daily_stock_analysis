import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  ExposureEdgeListResponse,
  ExposureFeedbackListResponse,
  ExposureMutationResponse,
  EventSignalListResponse,
} from '../types/exposure';

export const exposureApi = {
  async listEdges(params: {
    code?: string;
    entityId?: string;
    source?: string;
    includeDisabled?: boolean;
    limit?: number;
    offset?: number;
  } = {}): Promise<ExposureEdgeListResponse> {
    const response = await apiClient.get('/api/v1/exposure/edges', {
      params: {
        code: params.code,
        entity_id: params.entityId,
        source: params.source,
        include_disabled: params.includeDisabled,
        limit: params.limit,
        offset: params.offset,
      },
    });
    return toCamelCase(response.data) as ExposureEdgeListResponse;
  },

  async updateEdge(
    edgeId: number,
    payload: { strength?: string; summary?: string },
  ): Promise<ExposureMutationResponse> {
    const response = await apiClient.patch(`/api/v1/exposure/edges/${edgeId}`, payload);
    return toCamelCase(response.data) as ExposureMutationResponse;
  },

  async submitEdgeFeedback(
    edgeId: number,
    payload: { feedbackType: string; note?: string },
  ): Promise<ExposureMutationResponse> {
    const response = await apiClient.post(`/api/v1/exposure/edges/${edgeId}/feedback`, {
      feedback_type: payload.feedbackType,
      note: payload.note,
    });
    return toCamelCase(response.data) as ExposureMutationResponse;
  },

  async listFeedback(params: {
    targetType?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<ExposureFeedbackListResponse> {
    const response = await apiClient.get('/api/v1/exposure/feedback', {
      params: {
        target_type: params.targetType,
        limit: params.limit,
        offset: params.offset,
      },
    });
    return toCamelCase(response.data) as ExposureFeedbackListResponse;
  },

  async listEventSignals(params: {
    status?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<EventSignalListResponse> {
    const response = await apiClient.get('/api/v1/events/signals', { params });
    return toCamelCase(response.data) as EventSignalListResponse;
  },

  async submitEventFeedback(
    signalId: number,
    payload: { feedbackType: string; note?: string },
  ): Promise<ExposureMutationResponse> {
    const response = await apiClient.post(`/api/v1/events/signals/${signalId}/feedback`, {
      feedback_type: payload.feedbackType,
      note: payload.note,
    });
    return toCamelCase(response.data) as ExposureMutationResponse;
  },
};

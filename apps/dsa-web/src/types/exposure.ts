export interface CompanyExposureItem {
  id: number;
  code: string;
  targetEntityId: string;
  linkType: string;
  role?: string | null;
  strength: string;
  exposurePct?: number | null;
  direction: string;
  pricingDriver: string;
  summary?: string | null;
  source: string;
  sourceRef?: string | null;
  verifiedAt?: string | null;
  ttlDays: number;
  isDisabled: boolean;
}

export interface ExposureEdgeListResponse {
  items: CompanyExposureItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExposureFeedbackItem {
  id: number;
  targetType: string;
  targetId: number;
  feedbackType: string;
  note?: string | null;
  code?: string | null;
  entityId?: string | null;
  createdAt?: string | null;
}

export interface ExposureFeedbackListResponse {
  items: ExposureFeedbackItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExposureMutationResponse {
  success: boolean;
  message?: string;
}

export interface EventSignalItem {
  id: number;
  sourceType: string;
  sourceUrl: string;
  title: string;
  snippet?: string | null;
  status: string;
  skipReason?: string | null;
  resonanceSector?: string | null;
  processedAt?: string | null;
  entities: string[];
  matchedCodes: Array<Record<string, unknown>>;
}

export interface EventSignalListResponse {
  items: EventSignalItem[];
  total: number;
  limit: number;
  offset: number;
}

import client from './client';

export type HttpAccessSite = {
  id: string; site: string; sources: string[]; environment: string; role: string;
  monitoring_mode: string; enable_4xx: boolean; enable_latency: boolean;
  enable_traffic_drop: boolean; owner: string; last_seen_at?: string;
};

export const httpAccessApi = {
  listSites: (params?: Record<string, string>) => client.get('/http-access/sites', { params }),
  updateSite: (id: string, data: Partial<HttpAccessSite>) => client.patch(`/http-access/sites/${id}`, data),
  bulkUpdate: (site_ids: string[], data: Record<string, unknown>) => client.patch('/http-access/site-bulk-update', { site_ids, ...data }),
  pending: () => client.get('/http-access/pending'),
};

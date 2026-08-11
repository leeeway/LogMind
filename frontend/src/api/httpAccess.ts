import client from './client';

export type HttpAccessSite = {
  id: string; site: string; sources: string[]; environment: string; role: string;
  monitoring_mode: string; enable_4xx: boolean; enable_latency: boolean;
  enable_traffic_drop: boolean; owner: string; last_seen_at?: string;
  diagnostic_business_line_id?: string; repository_id?: string;
  deployment_service_name?: string;
};

export type GitRepository = {
  id: string; name: string; clone_url: string; default_branch: 'main' | 'master';
  credential_ref: string; is_active: boolean; last_sync_status: string;
  last_sync_error: string; last_synced_at?: string; last_commit_sha: string;
  cache_size_bytes: number;
};

export const httpAccessApi = {
  listSites: (params?: Record<string, string>) => client.get('/http-access/sites', { params: { limit: '500', ...params } }),
  discoverSites: (window_minutes = 60) => client.post('/http-access/sites/discover', { window_minutes }),
  updateSite: (id: string, data: Partial<HttpAccessSite>) => client.patch(`/http-access/sites/${id}`, data),
  bulkUpdate: (site_ids: string[], data: Record<string, unknown>) => client.patch('/http-access/site-bulk-update', { site_ids, ...data }),
  pending: () => client.get('/http-access/pending'),
  learningRules: () => client.get('/http-access/learning-rules'),
  governanceStatus: () => client.get('/http-access/governance-status'),
  feedback: (id: string, action: 'valid' | 'false_positive' | 'expected' | 'resolved', comment = '') =>
    client.post(`/http-access/incidents/${id}/feedback`, { action, comment }),
  repositories: () => client.get('/http-access/repositories'),
  createRepository: (data: Pick<GitRepository, 'name' | 'clone_url' | 'default_branch' | 'credential_ref'>) =>
    client.post('/http-access/repositories', data),
  testRepository: (id: string) => client.post(`/http-access/repositories/${id}/test`),
  syncRepository: (id: string) => client.post(`/http-access/repositories/${id}/sync`),
};

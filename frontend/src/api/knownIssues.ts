import client from './client';

export const knownIssuesApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    severity?: string;
    business_line_id?: string;
    search?: string;
    sort_by?: string;
    sort_order?: string;
  }) => client.get('/known-issues', { params }),

  get: (id: string) => client.get(`/known-issues/${id}`),

  updateStatus: (id: string, status: string) =>
    client.put(`/known-issues/${id}/status`, { status }),

  remove: (id: string) => client.delete(`/known-issues/${id}`),
};

import client from './client';

export interface BusinessLineListItem {
  id: string;
  name: string;
  es_index_pattern: string;
  [key: string]: unknown;
}

export const logsApi = {
  search: (data: { business_line_id: string; time_from: string; time_to: string; query?: string; severity?: string; size?: number }) =>
    client.post('/logs/search', data),

  naturalQuery: (question: string) =>
    client.post('/logs/natural-query', { question }),

  getStats: (businessLineId: string) =>
    client.get('/logs/stats', { params: { business_line_id: businessLineId } }),

  listIndices: () =>
    client.get('/logs/indices'),
};

export const businessLineApi = {
  list: (params?: { page?: number; page_size?: number }) =>
    client.get('/business-lines', { params }),

  listAll: async () => {
    const pageSize = 100;
    let page = 1;
    let total = Number.POSITIVE_INFINITY;
    const items: BusinessLineListItem[] = [];

    while (items.length < total) {
      const { data } = await client.get('/business-lines', {
        params: { page, page_size: pageSize },
      });
      const pageItems = Array.isArray(data?.items)
        ? data.items as BusinessLineListItem[]
        : [];
      items.push(...pageItems);
      total = typeof data?.total === 'number' ? data.total : items.length;

      if (pageItems.length < pageSize || page > 100) break;
      page += 1;
    }

    return { data: { items, total: items.length } };
  },

  create: (data: Record<string, unknown>) =>
    client.post('/business-lines', data),

  update: (id: string, data: Record<string, unknown>) =>
    client.put(`/business-lines/${id}`, data),

  // Discovery
  discover: () =>
    client.post('/business-lines/discover'),

  listDiscovered: () =>
    client.get('/business-lines/discovered'),

  confirmDiscovered: (id: string) =>
    client.post(`/business-lines/discovered/${id}/confirm`),

  ignoreDiscovered: (id: string) =>
    client.post(`/business-lines/discovered/${id}/ignore`),

  confirmAllDiscovered: () =>
    client.post('/business-lines/discovered/confirm-all'),
};

export const providerApi = {
  list: () => client.get('/providers'),
  getRegistered: () => client.get('/providers/registered'),
  create: (data: Record<string, unknown>) => client.post('/providers', data),
  update: (id: string, data: Record<string, unknown>) => client.put(`/providers/${id}`, data),
  remove: (id: string) => client.delete(`/providers/${id}`),
  health: () => client.get('/providers/health'),
};

export const promptApi = {
  list: () => client.get('/prompts'),
  get: (id: string) => client.get(`/prompts/${id}`),
  create: (data: Record<string, unknown>) => client.post('/prompts', data),
  update: (id: string, data: Record<string, unknown>) => client.put(`/prompts/${id}`, data),
  render: (data: Record<string, unknown>) => client.post('/prompts/render', data),
};

export const ragApi = {
  listKBs: () => client.get('/knowledge-base'),
  getKB: (id: string) => client.get(`/knowledge-base/${id}`),
  createKB: (data: Record<string, unknown>) => client.post('/knowledge-base', data),
  updateKB: (id: string, data: Record<string, unknown>) => client.put(`/knowledge-base/${id}`, data),
  deleteKB: (id: string) => client.delete(`/knowledge-base/${id}`),
  listDocs: (kbId: string) => client.get(`/knowledge-base/${kbId}/documents`),
  uploadDoc: (kbId: string, data: Record<string, unknown>) => client.post(`/knowledge-base/${kbId}/documents`, data),
  deleteDoc: (kbId: string, docId: string) => client.delete(`/knowledge-base/${kbId}/documents/${docId}`),
};

export const systemApi = {
  health: () => client.get('/health'),
};

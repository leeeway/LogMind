import client from './client';

export const logsApi = {
  search: (data: { index_pattern?: string; business_line_id?: string; time_from: string; time_to: string; query?: string; severity?: string; size?: number }) =>
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

  create: (data: Record<string, unknown>) =>
    client.post('/business-lines', data),

  update: (id: string, data: Record<string, unknown>) =>
    client.put(`/business-lines/${id}`, data),
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
  listKBs: () => client.get('/knowledge-bases'),
  getKB: (id: string) => client.get(`/knowledge-bases/${id}`),
  createKB: (data: Record<string, unknown>) => client.post('/knowledge-bases', data),
  updateKB: (id: string, data: Record<string, unknown>) => client.put(`/knowledge-bases/${id}`, data),
  deleteKB: (id: string) => client.delete(`/knowledge-bases/${id}`),
  listDocs: (kbId: string) => client.get(`/knowledge-bases/${kbId}/documents`),
  uploadDoc: (kbId: string, data: Record<string, unknown>) => client.post(`/knowledge-bases/${kbId}/documents`, data),
  deleteDoc: (kbId: string, docId: string) => client.delete(`/knowledge-bases/${kbId}/documents/${docId}`),
};

export const systemApi = {
  health: () => client.get('/health'),
};

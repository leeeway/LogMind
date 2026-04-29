import client from './client';

export const dashboardBuilderApi = {
  list: () => client.get('/dashboards/custom'),
  get: (id: string) => client.get(`/dashboards/custom/${id}`),
  create: (data: any) => client.post('/dashboards/custom', data),
  update: (id: string, data: any) => client.put(`/dashboards/custom/${id}`, data),
  delete: (id: string) => client.delete(`/dashboards/custom/${id}`),
};

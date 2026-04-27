import client from './client';

export const alertsApi = {
  listHistory: (params?: { page?: number; page_size?: number }) =>
    client.get('/alerts/history', { params }),

  ackAlert: (alertId: string) =>
    client.post(`/alerts/history/${alertId}/ack`),

  resolveAlert: (alertId: string) =>
    client.post(`/alerts/history/${alertId}/resolve`),

  listRules: () =>
    client.get('/alerts/rules'),

  createRule: (data: Record<string, unknown>) =>
    client.post('/alerts/rules', data),
};

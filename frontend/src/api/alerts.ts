import client from './client';

export const alertsApi = {
  listHistory: (params?: { page?: number; page_size?: number }) =>
    client.get('/alerts/history', { params }),

  ackAlert: (alertId: string) =>
    client.post(`/alerts/history/${alertId}/ack`),

  resolveAlert: (alertId: string) =>
    client.post(`/alerts/history/${alertId}/resolve`),

  batchAckAlerts: (alertIds: string[]) =>
    client.post('/alerts/history/batch-ack', { alert_ids: alertIds }),

  batchResolveAlerts: (alertIds: string[]) =>
    client.post('/alerts/history/batch-resolve', { alert_ids: alertIds }),

  listRules: () =>
    client.get('/alerts/rules'),

  createRule: (data: Record<string, unknown>) =>
    client.post('/alerts/rules', data),
};

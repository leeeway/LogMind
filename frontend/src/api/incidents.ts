import client from './client';

export const incidentApi = {
  list: () => client.get('/incidents'),
  get: (id: string) => client.get(`/incidents/${id}`),
  create: (data: { title: string; description?: string; severity?: string }) =>
    client.post('/incidents', data),
  update: (id: string, data: any) => client.patch(`/incidents/${id}`, data),
  addEvent: (id: string, data: { event_type?: string; content: string }) =>
    client.post(`/incidents/${id}/events`, data),
  generatePostmortem: (id: string) =>
    client.post(`/incidents/${id}/generate-postmortem`),
};

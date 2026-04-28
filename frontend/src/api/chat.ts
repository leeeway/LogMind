import client from './client';

export const chatApi = {
  createSession: () =>
    client.post('/chat/sessions'),

  listSessions: () =>
    client.get('/chat/sessions'),

  getSession: (sessionId: string) =>
    client.get(`/chat/sessions/${sessionId}`),

  deleteSession: (sessionId: string) =>
    client.delete(`/chat/sessions/${sessionId}`),

  // Note: sendMessage uses fetch() for SSE streaming, not axios
};

export const topologyApi = {
  getTopology: () =>
    client.get('/dashboard/topology'),
};

export const timelineApi = {
  getTimeline: (taskId: string) =>
    client.get(`/analysis/${taskId}/timeline`),
};

import client from './client';

export const analysisApi = {
  listTasks: (params?: { page?: number; page_size?: number; business_line_id?: string; status?: string }) =>
    client.get('/analysis/tasks', { params }),

  getTask: (taskId: string) =>
    client.get(`/analysis/tasks/${taskId}`),

  getTrace: (taskId: string) =>
    client.get(`/analysis/tasks/${taskId}/trace`),

  createTask: (data: { business_line_id: string; time_from: string; time_to: string; severity?: string }) =>
    client.post('/analysis/tasks', data),

  compare: (taskA: string, taskB: string) =>
    client.get('/analysis/compare', { params: { task_a: taskA, task_b: taskB } }),

  submitFeedback: (resultId: string, score: number, comment?: string) =>
    client.put(`/analysis/results/${resultId}/feedback`, null, { params: { score, comment } }),

  generateReport: (taskId: string, format: 'html' | 'markdown' = 'html') =>
    client.post(`/analysis/${taskId}/report`, null, { params: { format } }),

  getRootcauseChain: (taskId: string) =>
    client.get(`/analysis/${taskId}/rootcause-chain`),
};

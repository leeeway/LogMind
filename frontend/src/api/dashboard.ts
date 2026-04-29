import client from './client';

export const dashboardApi = {
  getOverview: (days = 7) =>
    client.get('/dashboard/overview', { params: { days } }),

  getTrends: (days = 7, businessLineId?: string) =>
    client.get('/dashboard/trends', { params: { days, business_line_id: businessLineId } }),

  getBusinessHealth: (days = 7) =>
    client.get('/dashboard/business-health', { params: { days } }),

  getCostAnalysis: (days = 7, businessLineId?: string) =>
    client.get('/dashboard/cost-analysis', { params: { days, business_line_id: businessLineId } }),

  getDedupStats: (days = 7, businessLineId?: string) =>
    client.get('/dashboard/dedup-stats', { params: { days, business_line_id: businessLineId } }),

  getAIEffectiveness: (days = 7, businessLineId?: string) =>
    client.get('/dashboard/ai-effectiveness', { params: { days, business_line_id: businessLineId } }),

  getAgentAnalytics: (days = 7, businessLineId?: string) =>
    client.get('/dashboard/agent-tool-analytics', { params: { days, business_line_id: businessLineId } }),

  getSLA: (days = 7) =>
    client.get('/dashboard/sla', { params: { days } }),

  getCapacityPrediction: (days = 7) =>
    client.get('/dashboard/capacity-prediction', { params: { lookback_days: days } }),
};

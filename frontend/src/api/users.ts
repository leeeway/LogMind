import client from './client';

export const usersApi = {
  list: (tenantId: string, params?: { page?: number; page_size?: number }) =>
    client.get(`/tenants/${tenantId}/users`, { params }),

  create: (tenantId: string, data: {
    username: string;
    email: string;
    password: string;
    role: string;
  }) => client.post(`/tenants/${tenantId}/users`, data),
};

export const auditApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    action?: string;
    resource_type?: string;
    user_id?: string;
  }) => client.get('/audit-logs', { params }),
};

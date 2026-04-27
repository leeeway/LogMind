import axios from 'axios';

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// Request interceptor: attach JWT
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('lm_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: handle 401
client.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      // Only redirect to login if not already on login page
      if (window.location.pathname !== '/login') {
        localStorage.removeItem('lm_token');
        localStorage.removeItem('lm_user');
        window.location.href = '/login';
      }
    }
    return Promise.reject(err);
  }
);

export default client;

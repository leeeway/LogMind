import { create } from 'zustand';

interface User {
  id: string;
  username: string;
  email: string;
  role: string;
  tenant_id: string;
}

interface AuthState {
  token: string | null;
  user: User | null;
  isAuthenticated: boolean;
  setAuth: (token: string, user: User) => void;
  logout: () => void;
  hydrate: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  isAuthenticated: false,

  setAuth: (token, user) => {
    localStorage.setItem('lm_token', token);
    localStorage.setItem('lm_user', JSON.stringify(user));
    set({ token, user, isAuthenticated: true });
  },

  logout: () => {
    localStorage.removeItem('lm_token');
    localStorage.removeItem('lm_user');
    set({ token: null, user: null, isAuthenticated: false });
  },

  hydrate: () => {
    const token = localStorage.getItem('lm_token');
    const userStr = localStorage.getItem('lm_user');
    if (token && userStr) {
      try {
        const user = JSON.parse(userStr);
        set({ token, user, isAuthenticated: true });
      } catch {
        localStorage.removeItem('lm_token');
        localStorage.removeItem('lm_user');
      }
    }
  },
}));

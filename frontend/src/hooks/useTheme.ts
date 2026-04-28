import { useState, useEffect, useCallback } from 'react';

type Theme = 'dark' | 'light';

/**
 * Theme toggle hook with localStorage persistence.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    if (typeof window !== 'undefined') {
      return (localStorage.getItem('lm-theme') as Theme) || 'dark';
    }
    return 'dark';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('lm-theme', theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  return { theme, toggleTheme, isDark: theme === 'dark' };
}

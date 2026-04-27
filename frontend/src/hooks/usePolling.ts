import { useEffect, useRef, useState, useCallback } from 'react';

interface UsePollingOptions {
  /** Polling interval in milliseconds */
  interval: number;
  /** Whether polling is enabled */
  enabled?: boolean;
  /** Whether to pause when page is not visible */
  pauseOnHidden?: boolean;
}

interface UsePollingReturn<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  lastUpdated: Date | null;
  secondsUntilRefresh: number;
  refresh: () => Promise<void>;
}

/**
 * Auto-polling hook with visibility-aware pause and countdown timer.
 * Pauses when tab is hidden and resumes when visible.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  options: UsePollingOptions
): UsePollingReturn<T> {
  const { interval, enabled = true, pauseOnHidden = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [secondsUntilRefresh, setSecondsUntilRefresh] = useState(Math.floor(interval / 1000));
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isVisibleRef = useRef(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const doFetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
      setSecondsUntilRefresh(Math.floor(interval / 1000));
    }
  }, [interval]);

  useEffect(() => {
    if (!enabled) return;

    // Initial fetch
    doFetch();

    // Set up polling
    timerRef.current = setInterval(() => {
      if (!pauseOnHidden || isVisibleRef.current) {
        doFetch();
      }
    }, interval);

    // Set up countdown
    countdownRef.current = setInterval(() => {
      setSecondsUntilRefresh((prev) => Math.max(prev - 1, 0));
    }, 1000);

    // Visibility change handler
    const handleVisibility = () => {
      isVisibleRef.current = !document.hidden;
      if (!document.hidden) {
        doFetch(); // Refresh on tab return
      }
    };

    if (pauseOnHidden) {
      document.addEventListener('visibilitychange', handleVisibility);
    }

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (countdownRef.current) clearInterval(countdownRef.current);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [enabled, interval, pauseOnHidden, doFetch]);

  return { data, loading, error, lastUpdated, secondsUntilRefresh, refresh: doFetch };
}

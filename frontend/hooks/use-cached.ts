"use client";

/**
 * Stale-while-revalidate hook for client-side reads.
 *
 * Thin wrapper over `swr` (already a dependency) with defaults tuned for this
 * dashboard: the backend data changes ~once/day, so we keep the last response
 * in SWR's module-level cache and serve it instantly on revisits, refreshing in
 * the background. `isCold` is true only on a true cold load (no cached data
 * yet) — use it to gate skeletons so they never flash on background refreshes.
 */

import useSWR, { type SWRConfiguration } from "swr";

export function useCached<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  options?: SWRConfiguration<T>,
) {
  const { data, error, isLoading, isValidating, mutate } = useSWR<T>(
    key,
    fetcher,
    {
      // Serve cached data immediately, revalidate quietly in the background.
      keepPreviousData: true,
      revalidateOnFocus: false,
      revalidateIfStale: true,
      // The data is daily; don't hammer the backend on every mount.
      dedupingInterval: 60_000,
      ...options,
    },
  );

  return {
    data,
    error,
    /** True only on a real cold load (nothing cached yet) → show a skeleton. */
    isCold: isLoading && data === undefined,
    /** True while a background refresh is in flight (don't show a skeleton). */
    isRefreshing: isValidating && data !== undefined,
    mutate,
  };
}

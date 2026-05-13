"use client";

import useSWR, { type SWRConfiguration } from "swr";

/**
 * 60s polling hook with SWR. Returns SWR's standard shape. Pass a stable key
 * (string or null to pause) and an async fetcher.
 */
export function usePoll<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  config: SWRConfiguration<T> = {},
) {
  return useSWR<T>(key, fetcher, {
    refreshInterval: 60_000,
    revalidateOnFocus: true,
    revalidateIfStale: true,
    dedupingInterval: 5_000,
    ...config,
  });
}

import { useEffect, useState } from "react";
import { ApiError, getJson } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Fetch `path` (again whenever it or `refresh` changes). Errors carry the API's own `detail`
 * message. Bump `refresh` to reload after a write.
 */
export function useApi<T>(path: string, refresh = 0): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null, loading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    getJson<T>(path, controller.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          err instanceof ApiError
            ? err.message
            : "Cannot reach the API. Is it running on :8000 (make dev)?";
        setState({ data: null, error: message, loading: false });
      });
    return () => controller.abort();
  }, [path, refresh]);

  return state;
}

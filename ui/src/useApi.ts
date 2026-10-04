import { useEffect, useState } from "react";
import { ApiError, getJson } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/** Fetch `path` once (and again when it changes). Errors carry the API's own `detail` message. */
export function useApi<T>(path: string): Loaded<T> {
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
  }, [path]);

  return state;
}

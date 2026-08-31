"use client";

import { useCallback, useEffect, useState } from "react";

interface CollectionState<T> {
  scopeKey: string;
  generation: number;
  items: T[];
  error: string;
}

interface ScopedError {
  scopeKey: string;
  message: string;
}

export function useWorkspaceCollection<T>(
  scopeKey: string | undefined,
  query: () => Promise<T[]>,
  fallbackError: string,
) {
  const activeScope = scopeKey ?? "";
  const [generation, setGeneration] = useState(0);
  const [state, setState] = useState<CollectionState<T>>({
    scopeKey: "",
    generation: 0,
    items: [],
    error: "",
  });
  const [manualError, setManualError] = useState<ScopedError>({
    scopeKey: "",
    message: "",
  });

  useEffect(() => {
    if (!scopeKey) return;
    let cancelled = false;
    void query()
      .then((items) => {
        if (!cancelled) {
          setState({ scopeKey, generation, items, error: "" });
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setState((current) => ({
            scopeKey,
            generation,
            items: current.scopeKey === scopeKey ? current.items : [],
            error: caught instanceof Error ? caught.message : fallbackError,
          }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [fallbackError, generation, query, scopeKey]);

  const refresh = useCallback(() => {
    setState((current) => ({ ...current, error: "" }));
    setManualError({ scopeKey: activeScope, message: "" });
    setGeneration((current) => current + 1);
  }, [activeScope]);

  const reportError = useCallback(
    (message: string) => {
      setManualError({ scopeKey: activeScope, message });
    },
    [activeScope],
  );

  const scoped = state.scopeKey === activeScope;
  const loading = Boolean(scopeKey) && (!scoped || state.generation !== generation);
  const error = manualError.scopeKey === activeScope && manualError.message
    ? manualError.message
    : scoped
      ? state.error
      : "";

  return {
    items: scoped ? state.items : [],
    loading,
    error,
    refresh,
    reportError,
  };
}

import { useCallback, useEffect, useState } from "react";

const POLL_INTERVAL_MS = 4000;

/** Polls now-playing + queue for either an admin-session slot (by name) or
 * a jam-mode slot (by token) -- pass exactly one of {name} or {jamToken}.
 * Paused while the tab is backgrounded so we don't hammer Spotify's API
 * for a screen nobody's looking at. Plain polling, not push -- see the
 * Phase 2 plan doc for why (song changes every few minutes, not a
 * sub-second freshness need). */
export function useSlotPlayer({ name, jamToken, fetchers }) {
  const [nowPlaying, setNowPlaying] = useState(null);
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);

  const key = name || jamToken;

  const refresh = useCallback(async () => {
    if (!key) return;
    try {
      const [np, q] = await Promise.all([fetchers.getNowPlaying(key), fetchers.getQueue(key)]);
      setNowPlaying(np.now_playing !== undefined ? np.now_playing : np);
      setQueue(q);
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, [key, fetchers]);

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    let timer;

    async function tick() {
      if (document.visibilityState === "visible") {
        await refresh();
      }
      if (!cancelled) timer = setTimeout(tick, POLL_INTERVAL_MS);
    }

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, refresh]);

  return { nowPlaying, queue, error, refresh };
}

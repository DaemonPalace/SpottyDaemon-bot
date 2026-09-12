import { useCallback, useEffect, useState } from "react";

const POLL_INTERVAL_MS = 3000;

/** Polls now-playing + queue (one combined request, see bot/api.py's
 * _player_state) for either an admin-session slot (by name) or a jam-mode
 * slot (by token) -- pass exactly one of {name} or {jamToken}. Paused
 * while the tab is backgrounded so we don't hammer Spotify's API for a
 * screen nobody's looking at. Plain polling, not push -- see the Phase 2
 * plan doc for why (song changes every few minutes, not a sub-second
 * freshness need). */
export function useSlotPlayer({ name, jamToken, fetchers }) {
  const [nowPlaying, setNowPlaying] = useState(null);
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);

  const key = name || jamToken;

  const refresh = useCallback(async () => {
    if (!key) return;
    try {
      const state = await fetchers.getPlayerState(key);
      setNowPlaying(state.now_playing);
      setQueue(state.queue);
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

import { useEffect, useState } from "react";
import { getStatus } from "../api/client";

/** Polls /api/supervisor/status. Interval is short (2s) since this only
 * matters during setup/starting/crash-loop transitions -- once state is
 * "running" the app doesn't need to keep polling this (the slots/player
 * hooks take over). */
export function useSupervisorStatus() {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let timer;

    async function poll() {
      try {
        const data = await getStatus();
        if (!cancelled) {
          setState(data.state);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err);
      }
      if (!cancelled) timer = setTimeout(poll, 2000);
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  return { state, error };
}

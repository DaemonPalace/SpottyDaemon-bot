import { useEffect, useState } from "react";
import { getStatus } from "../api/client";

/** Polls /api/supervisor/status. Interval is short (2s) since this only
 * matters during setup/starting/crash-loop transitions -- once state is
 * "running" the app doesn't need to keep polling this (the slots/player
 * hooks take over).
 *
 * "asleep": on the hosted platform the dashboard itself is served from
 * CloudFront, so a sleeping bot (no container behind the load balancer)
 * shows up as a 502/503/504 from this call rather than a network error. */
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
        if (!cancelled) {
          setError(err);
          if ([502, 503, 504].includes(err.status)) setState("asleep");
        }
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

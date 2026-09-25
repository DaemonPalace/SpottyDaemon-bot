import { useEffect, useRef } from "react";

/** Direct-mode Spotify linking (bot/api.py's _spotify_callback): the login
 * tab lands on the bot's own callback, so this tab just polls `poll` (a
 * finish-link call with no pasted url) until it stops answering pending,
 * then hands the result to onDone. */
export default function useLinkPolling(active, poll, onDone) {
  const latest = useRef({ poll, onDone });
  latest.current = { poll, onDone };

  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer;
    async function tick() {
      let data;
      try {
        data = await latest.current.poll();
      } catch (err) {
        data = { success: false, message: err.message };
      }
      if (stopped) return;
      if (data.pending) timer = setTimeout(tick, 2000);
      else latest.current.onDone(data);
    }
    timer = setTimeout(tick, 2000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [active]);
}

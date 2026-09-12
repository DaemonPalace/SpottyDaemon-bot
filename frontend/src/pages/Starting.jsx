import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getLogs } from "../api/client";
import { useSupervisorStatus } from "../hooks/useSupervisorStatus";

export default function Starting() {
  const { state } = useSupervisorStatus();
  const [logs, setLogs] = useState([]);
  const navigate = useNavigate();

  useEffect(() => {
    if (state === "running") {
      navigate("/slots");
    }
  }, [state, navigate]);

  useEffect(() => {
    let cancelled = false;
    let timer;
    async function poll() {
      try {
        const data = await getLogs();
        if (!cancelled) setLogs(data.lines);
      } catch {
        // logs endpoint may briefly 404/fail while the process spins up
      }
      if (!cancelled) timer = setTimeout(poll, 1000);
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  return (
    <div className="screen">
      <h1>{state === "crash_looping" ? "Bot isn't starting" : "Starting the bot..."}</h1>
      {state === "crash_looping" && (
        <p className="error">
          The bot keeps crashing right after startup -- most likely the Discord token is wrong.
          Check the logs below, then fix your .env and restart the supervisor.
        </p>
      )}
      <pre className="logs">{logs.join("\n") || "(waiting for output...)"}</pre>
    </div>
  );
}

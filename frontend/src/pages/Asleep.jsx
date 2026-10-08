import { useState } from "react";
import { wakeBot } from "../api/client";

export default function Asleep() {
  const [waking, setWaking] = useState(false);
  const [error, setError] = useState(null);

  async function handleWake() {
    setError(null);
    setWaking(true);
    try {
      await wakeBot();
    } catch (err) {
      setError(err.message);
      setWaking(false);
    }
  }

  return (
    <div className="screen">
      <h1>{waking ? "Waking up..." : "The bot is asleep"}</h1>
      <p className="hint">
        {waking
          ? "This takes about a minute. The page will open on its own."
          : "It sleeps when nobody has used it for a while. Wake it here or with /wake in Discord."}
      </p>
      {error && <p className="error">{error}</p>}
      <button onClick={handleWake} disabled={waking}>
        {waking ? "Waking..." : "Wake the bot"}
      </button>
    </div>
  );
}

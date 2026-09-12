import { useState } from "react";
import { finishWebApiLink, startWebApiLink } from "../api/client";

/** Drives /link-web-api's web equivalent for one slot. Used both for the
 * initial "no Web API link yet" case and for re-linking to pick up a newer
 * OAuth scope (Spotify re-prompts for consent either way). */
export default function WebApiLinkFlow({ name, prompt, onLinked }) {
  const [step, setStep] = useState("start");
  const [message, setMessage] = useState("");
  const [pastedUrl, setPastedUrl] = useState("");
  const [userId, setUserId] = useState(null);
  const [error, setError] = useState(null);

  async function handleStart() {
    setError(null);
    try {
      const data = await startWebApiLink(name);
      if (!data.success) throw new Error(data.message);
      setMessage(data.message);
      setUserId(data.user_id);
      setStep("finish");
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleFinish(e) {
    e.preventDefault();
    setError(null);
    try {
      const data = await finishWebApiLink(name, userId, pastedUrl.trim());
      if (!data.success) throw new Error(data.message);
      onLinked();
    } catch (err) {
      setError(err.message);
    }
  }

  if (step === "start") {
    return (
      <div className="link-flow">
        <p className="hint">{prompt}</p>
        {error && <p className="error">{error}</p>}
        <button onClick={handleStart}>Link Spotify</button>
      </div>
    );
  }

  return (
    <form onSubmit={handleFinish} className="form link-flow">
      <p className="hint" style={{ whiteSpace: "pre-wrap" }}>{message}</p>
      <label>
        Failed-redirect url
        <input value={pastedUrl} onChange={(e) => setPastedUrl(e.target.value)} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit">Finish linking</button>
    </form>
  );
}

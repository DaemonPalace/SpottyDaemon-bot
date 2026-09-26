import { useState } from "react";
import { finishWebApiLink, startWebApiLink } from "../api/client";
import useLinkPolling from "../hooks/useLinkPolling";

/** Drives /link-web-api's web equivalent for one slot. Used both for the
 * initial "no Web API link yet" case and for re-linking to pick up a newer
 * OAuth scope (Spotify re-prompts for consent either way).
 *
 * One click does both halves of step one: opens Spotify's login in a new
 * tab (via a blank tab grabbed synchronously in the click handler, so
 * popup blockers don't eat it once the url arrives after the await) and
 * reveals the paste-back dialog for step two, instead of asking for two
 * separate clicks. With a public domain configured (data.direct), step two
 * is just waiting for the bot's own callback instead of a paste-back.
 *
 * start/finish default to the Web API link; SlotProfile.jsx swaps in the
 * player link (/link) for a newly approved profile. onLinked gets
 * data.direct -- a direct-mode player link covers the Web API too. */
export default function WebApiLinkFlow({
  name,
  prompt,
  onLinked,
  start = startWebApiLink,
  finish = finishWebApiLink,
}) {
  const [step, setStep] = useState("start");
  const [message, setMessage] = useState("");
  const [authorizeUrl, setAuthorizeUrl] = useState(null);
  const [pastedUrl, setPastedUrl] = useState("");
  const [userId, setUserId] = useState(null);
  const [direct, setDirect] = useState(false);
  const [error, setError] = useState(null);

  useLinkPolling(step === "finish" && direct, () => finish(name, userId, ""), (data) => {
    if (data.success) {
      onLinked(true);
    } else {
      setError(data.message);
      setStep("start");
    }
  });

  async function handleStart() {
    setError(null);
    const newTab = window.open("", "_blank");
    try {
      const data = await start(name);
      if (!data.success) throw new Error(data.message);
      if (newTab) newTab.location = data.authorize_url;
      setMessage(data.message);
      setAuthorizeUrl(data.authorize_url);
      setUserId(data.user_id);
      setDirect(Boolean(data.direct));
      setStep("finish");
    } catch (err) {
      if (newTab) newTab.close();
      setError(err.message);
    }
  }

  async function handleFinish(e) {
    e.preventDefault();
    setError(null);
    try {
      const data = await finish(name, userId, pastedUrl.trim());
      if (!data.success) throw new Error(data.message);
      onLinked(false);
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

  const reopenButton = (
    <button type="button" className="ghost" onClick={() => window.open(authorizeUrl, "_blank", "noopener,noreferrer")}>
      Open Spotify login again
    </button>
  );

  if (direct) {
    return (
      <div className="link-flow">
        <p className="hint">{message}</p>
        {reopenButton}
        <p className="hint">Waiting for Spotify...</p>
      </div>
    );
  }

  return (
    <form onSubmit={handleFinish} className="form link-flow">
      <p className="hint">{message}</p>
      {reopenButton}
      <label>
        Redirect URL
        <input value={pastedUrl} onChange={(e) => setPastedUrl(e.target.value)} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit">Finish linking</button>
    </form>
  );
}

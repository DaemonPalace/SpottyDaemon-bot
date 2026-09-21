import { useState } from "react";
import { finishLink, startLink } from "../api/client";
import Modal from "./Modal";

/** Registration + Spotify-linking flow for a brand new profile, in one
 * popup: start -> waiting-login -> done. Mirrors /link's Discord-side UX
 * (paste the failed-redirect url back) since there's no public HTTPS
 * endpoint here to catch the OAuth redirect directly. */
export default function CreateProfileModal({ onClose, onCreated }) {
  const [step, setStep] = useState("start");
  const [slotName, setSlotName] = useState("");
  const [password, setPassword] = useState("");
  const [authorizeMessage, setAuthorizeMessage] = useState("");
  const [authorizeUrl, setAuthorizeUrl] = useState(null);
  const [pastedUrl, setPastedUrl] = useState("");
  const [userId, setUserId] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleStart(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    // Grabbed synchronously in this click handler so popup blockers don't
    // eat it once the real url arrives after the await below.
    const newTab = window.open("", "_blank");
    try {
      const data = await startLink(slotName.trim().toLowerCase(), password);
      if (!data.success) throw new Error(data.message);
      if (newTab) newTab.location = data.authorize_url;
      setAuthorizeMessage(data.message);
      setAuthorizeUrl(data.authorize_url);
      setUserId(data.user_id);
      setStep("waiting-login");
    } catch (err) {
      if (newTab) newTab.close();
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleFinish(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await finishLink(userId, pastedUrl.trim() || null);
      if (!data.success) throw new Error(data.message);
      setStep("done");
      onCreated();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Create profile" onClose={onClose}>
      {step === "start" && (
        <form onSubmit={handleStart} className="form">
          <p className="hint">Pick a name and password for this profile, then link its Spotify account.</p>
          <label>
            Profile name
            <input value={slotName} onChange={(e) => setSlotName(e.target.value)} placeholder="e.g. alice" required />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? "Starting..." : "Continue to Spotify"}
          </button>
        </form>
      )}

      {step === "waiting-login" && (
        <form onSubmit={handleFinish} className="form">
          <h3>Link Spotify</h3>
          <p className="hint">{authorizeMessage}</p>
          <button
            type="button"
            className="ghost"
            onClick={() => window.open(authorizeUrl, "_blank", "noopener,noreferrer")}
          >
            Open Spotify login again
          </button>
          <label>
            Redirect url (leave blank if the page loaded fine)
            <input
              value={pastedUrl}
              onChange={(e) => setPastedUrl(e.target.value)}
              placeholder="http://127.0.0.1:.../login?code=..."
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? "Finishing..." : "Finish linking"}
          </button>
        </form>
      )}

      {step === "done" && <p>Profile created! Closing...</p>}
    </Modal>
  );
}

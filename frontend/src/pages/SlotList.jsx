import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteSlot, finishLink, listSlots, login, startLink } from "../api/client";

function LinkNewSlot({ onLinked }) {
  const [step, setStep] = useState("start"); // start -> waiting-login -> done
  const [slotName, setSlotName] = useState("");
  const [password, setPassword] = useState("");
  const [authorizeMessage, setAuthorizeMessage] = useState("");
  const [pastedUrl, setPastedUrl] = useState("");
  const [userId, setUserId] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleStart(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await startLink(slotName.trim().toLowerCase(), password);
      if (!data.success) throw new Error(data.message);
      setAuthorizeMessage(data.message);
      setUserId(data.user_id);
      setStep("waiting-login");
    } catch (err) {
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
      onLinked();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (step === "start") {
    return (
      <form onSubmit={handleStart} className="form">
        <h3>Link a new Spotify account</h3>
        <label>
          Slot name
          <input value={slotName} onChange={(e) => setSlotName(e.target.value)} placeholder="e.g. alices-jams" required />
        </label>
        <label>
          Set a password for this slot
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>Start linking</button>
      </form>
    );
  }

  if (step === "waiting-login") {
    return (
      <form onSubmit={handleFinish} className="form">
        <h3>Finish linking</h3>
        <p className="hint" style={{ whiteSpace: "pre-wrap" }}>{authorizeMessage}</p>
        <label>
          Failed-redirect url (leave blank if the page loaded fine -- same machine case)
          <input value={pastedUrl} onChange={(e) => setPastedUrl(e.target.value)} placeholder="http://127.0.0.1:.../login?code=..." />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>Finish linking</button>
      </form>
    );
  }

  return <p>Linked! Refresh the list below.</p>;
}

export default function SlotList() {
  const [slots, setSlots] = useState([]);
  const [error, setError] = useState(null);
  const [showLinkForm, setShowLinkForm] = useState(false);
  const navigate = useNavigate();

  async function refresh() {
    try {
      const data = await listSlots();
      setSlots(data.slots);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleDelete(name) {
    const password = window.prompt(`Re-enter the admin password to delete '${name}':`);
    if (!password) return;
    try {
      await login(password); // re-confirms the admin password before a destructive action
      await deleteSlot(name);
      refresh();
    } catch (err) {
      window.alert(err.message);
    }
  }

  return (
    <div className="screen">
      <h1>Slots</h1>
      {error && <p className="error">{error}</p>}
      <ul className="slot-list">
        {slots.map((slot) => (
          <li key={slot.index} className="slot-row">
            <span className="slot-index">#{slot.index}</span>
            {slot.state === "claimed" ? (
              <>
                <button onClick={() => navigate(`/slots/${slot.name}`)}>{slot.name}</button>
                <span className={slot.running ? "badge running" : "badge"}>{slot.running ? "playing" : "idle"}</span>
                <button className="danger" onClick={() => handleDelete(slot.name)}>Delete</button>
              </>
            ) : (
              <span className="hint">free</span>
            )}
          </li>
        ))}
      </ul>

      {showLinkForm ? (
        <LinkNewSlot
          onLinked={() => {
            setShowLinkForm(false);
            refresh();
          }}
        />
      ) : (
        <button onClick={() => setShowLinkForm(true)}>Link a new account</button>
      )}
    </div>
  );
}

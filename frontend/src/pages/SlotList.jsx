import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteSlot, finishLink, listSlots, login, startBot, startLink } from "../api/client";
import { useSupervisorStatus } from "../hooks/useSupervisorStatus";

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

function DeleteSlotConfirm({ name, onDeleted, onCancel }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(password); // re-confirms the admin password before a destructive action
      await deleteSlot(name);
      onDeleted();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="form inline">
      <input
        type="password"
        placeholder="admin password to confirm"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
        autoFocus
      />
      <button type="submit" className="danger" disabled={busy}>Confirm delete</button>
      <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default function SlotList() {
  const { state } = useSupervisorStatus();
  const [slots, setSlots] = useState([]);
  const [error, setError] = useState(null);
  const [showLinkForm, setShowLinkForm] = useState(false);
  const [starting, setStarting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(null); // slot name, or null
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

  async function handleStartBot() {
    setStarting(true);
    try {
      await startBot();
    } catch (err) {
      setError(err.message);
      setStarting(false);
    }
  }

  useEffect(() => {
    if (state === "running") refresh();
  }, [state]);

  if (state !== null && state !== "running") {
    return (
      <div className="screen">
        <h1>Slots</h1>
        <p className="hint">
          The bot process isn't running (status: {state}
          {starting ? ", starting..." : ""}).
        </p>
        {error && <p className="error">{error}</p>}
        <button onClick={handleStartBot} disabled={starting || state === "starting"}>
          {starting || state === "starting" ? "Starting..." : "Start the bot"}
        </button>
      </div>
    );
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
                {confirmingDelete === slot.name ? (
                  <DeleteSlotConfirm
                    name={slot.name}
                    onDeleted={() => {
                      setConfirmingDelete(null);
                      refresh();
                    }}
                    onCancel={() => setConfirmingDelete(null)}
                  />
                ) : (
                  <button className="danger" onClick={() => setConfirmingDelete(slot.name)}>Delete</button>
                )}
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

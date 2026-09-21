import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteSlot, listSlots, login, startBot } from "../api/client";
import CreateProfileModal from "../components/CreateProfileModal";
import PasswordPromptModal from "../components/PasswordPromptModal";
import ProfileCircle from "../components/ProfileCircle";
import { useSupervisorStatus } from "../hooks/useSupervisorStatus";

function DeleteConfirmModal({ name, onClose, onDeleted }) {
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
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal-card" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Delete "{name}"?</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">
          <form onSubmit={handleSubmit} className="form">
            <label>
              Admin password to confirm
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoFocus
              />
            </label>
            {error && <p className="error">{error}</p>}
            <button type="submit" className="danger" disabled={busy}>
              {busy ? "Deleting..." : "Confirm delete"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

export default function SlotList() {
  const { state } = useSupervisorStatus();
  const [slots, setSlots] = useState([]);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);
  const [managing, setManaging] = useState(false);
  const [unlocking, setUnlocking] = useState(null); // slot name, or null
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(null); // slot name, or null
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
        <h1>Profiles</h1>
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

  const claimed = slots.filter((s) => s.state === "claimed");
  const hasFreeSlot = slots.some((s) => s.state === "free");

  return (
    <div className="profile-select">
      <h1 className="profile-select-title">Who's playing?</h1>
      {error && <p className="error">{error}</p>}

      <div className="profile-grid">
        {claimed.map((slot) => (
          <div key={slot.index} className="profile-item">
            <ProfileCircle
              name={slot.name}
              avatarUrl={slot.avatar_url}
              running={slot.running}
              onClick={() => (managing ? setDeleting(slot.name) : setUnlocking(slot.name))}
            >
              {managing && <span className="profile-circle-badge">✕</span>}
            </ProfileCircle>
            <span className="profile-item-name">{slot.name}</span>
          </div>
        ))}

        {!managing && hasFreeSlot && (
          <div className="profile-item">
            <ProfileCircle name="+" onClick={() => setCreating(true)} size="lg" />
            <span className="profile-item-name">Add profile</span>
          </div>
        )}
      </div>

      {claimed.length > 0 && (
        <button className="ghost profile-manage-btn" onClick={() => setManaging((m) => !m)}>
          {managing ? "Done" : "Manage profiles"}
        </button>
      )}

      {unlocking && (
        <PasswordPromptModal
          name={unlocking}
          onClose={() => setUnlocking(null)}
          onUnlocked={(data) => navigate(`/slots/${unlocking}`, { state: data })}
        />
      )}

      {creating && (
        <CreateProfileModal
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            refresh();
          }}
        />
      )}

      {deleting && (
        <DeleteConfirmModal
          name={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            setDeleting(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}

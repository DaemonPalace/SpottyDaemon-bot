import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteSlot, getUpdateStatus, listInvites, listSlots, login, startBot, updateBot } from "../api/client";
import AdminSettingsModal from "../components/AdminSettingsModal";
import InvitesModal from "../components/InvitesModal";
import PasswordPromptModal from "../components/PasswordPromptModal";
import ProfileCircle from "../components/ProfileCircle";
import { useSupervisorStatus } from "../hooks/useSupervisorStatus";

/** Re-confirms the admin password, then runs `action` (deleting a slot,
 * installing an update). */
function AdminConfirmModal({ title, note, confirmLabel, busyLabel, danger, action, onClose, onDone }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(password); // re-confirms the admin password before a destructive action
      await action();
      onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal-card" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">
          <form onSubmit={handleSubmit} className="form">
            {note && <p className="hint">{note}</p>}
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
            <button type="submit" className={danger ? "danger" : undefined} disabled={busy}>
              {busy ? busyLabel : confirmLabel}
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
  const [showInvites, setShowInvites] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [deleting, setDeleting] = useState(null); // slot name, or null
  const [showAdminSettings, setShowAdminSettings] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [confirmingUpdate, setConfirmingUpdate] = useState(false);
  const [updating, setUpdating] = useState(false);
  const navigate = useNavigate();

  // Hosted only; a self-hosted dashboard has no /_platform and just never
  // shows the banner.
  useEffect(() => {
    getUpdateStatus()
      .then((data) => setUpdateAvailable(Boolean(data?.update_available)))
      .catch(() => {});
  }, []);

  async function refresh() {
    try {
      const data = await listSlots();
      setSlots(data.slots);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
    // Admin-only: just no badge without an admin session.
    listInvites()
      .then((data) => setPendingCount(data.invites.filter((i) => i.state === "pending").length))
      .catch(() => {});
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

  // Approved invitees show up too -- logging in takes them to linking.
  const claimed = slots.filter((s) => ["claimed", "approved", "linking"].includes(s.state));

  return (
    <div className="profile-select">
      <div className="profile-admin-btn">
        <button
          type="button"
          className="ghost icon-btn invites-btn"
          onClick={() => setShowInvites(true)}
          aria-label="Invites"
          title="Invites"
        >
          ✉{pendingCount > 0 && <span className="profile-circle-badge">{pendingCount}</span>}
        </button>
        <button
          type="button"
          className="ghost icon-btn"
          onClick={() => setShowAdminSettings(true)}
          aria-label="Admin settings"
          title="Admin settings"
        >
          ⚙
        </button>
      </div>
      <h1 className="profile-select-title">Who's playing?</h1>
      {error && <p className="error">{error}</p>}
      {updating ? (
        <p className="hint">Updating... the dashboard comes back on its own in about a minute.</p>
      ) : (
        updateAvailable && (
          <p className="hint">
            A new version of the bot is out.{" "}
            <button type="button" className="ghost" onClick={() => setConfirmingUpdate(true)}>
              Update now
            </button>
          </p>
        )
      )}

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

        {claimed.length === 0 && <p className="hint">No profiles yet -- send an invite with ✉.</p>}
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

      {showInvites && <InvitesModal onClose={() => setShowInvites(false)} onChanged={refresh} />}

      {deleting && (
        <AdminConfirmModal
          title={`Delete "${deleting}"?`}
          confirmLabel="Confirm delete"
          busyLabel="Deleting..."
          danger
          action={() => deleteSlot(deleting)}
          onClose={() => setDeleting(null)}
          onDone={() => {
            setDeleting(null);
            refresh();
          }}
        />
      )}

      {confirmingUpdate && (
        <AdminConfirmModal
          title="Update the bot?"
          note="It restarts on the new version: anyone listening loses the music for about a minute, then reconnects with /connect."
          confirmLabel="Update now"
          busyLabel="Updating..."
          action={updateBot}
          onClose={() => setConfirmingUpdate(false)}
          onDone={() => {
            setConfirmingUpdate(false);
            setUpdating(true);
          }}
        />
      )}

      {showAdminSettings && <AdminSettingsModal onClose={() => setShowAdminSettings(false)} />}
    </div>
  );
}

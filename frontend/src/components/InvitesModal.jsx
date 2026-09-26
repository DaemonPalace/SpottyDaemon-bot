import { useState } from "react";
import { approveInvite, createInvite, denyInvite, listInvites, login } from "../api/client";
import Modal from "./Modal";

const inviteUrl = (token) => `${window.location.origin}/invite/${token}`;

function CopyButton({ text, label = "Copy" }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="ghost"
      onClick={() =>
        // No clipboard API over plain-HTTP LAN installs -- fall back to a copyable prompt.
        navigator.clipboard ? navigator.clipboard.writeText(text).then(() => setCopied(true)) : window.prompt("Copy:", text)
      }
    >
      {copied ? "Copied" : label}
    </button>
  );
}

/** Admin: hand out one-time invite links and review sign-ups. Approving
 * doesn't touch Spotify -- add the full name + email to the listed Spotify
 * app's allowlist (developer.spotify.com > User Management) first, since
 * linking fails for accounts that aren't on it. Same password-unlocks-a-
 * session pattern as AdminSettingsModal.jsx: always asks, even with a
 * live admin cookie. */
export default function InvitesModal({ onClose, onChanged }) {
  const [unlocked, setUnlocked] = useState(false);
  const [password, setPassword] = useState("");
  const [invites, setInvites] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setInvites((await listInvites()).invites);
    setUnlocked(true);
  }

  async function run(action) {
    setError(null);
    setBusy(true);
    try {
      await action();
      await refresh();
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!unlocked) {
    return (
      <Modal title="Invites" onClose={onClose}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(() => login(password));
          }}
          className="form"
        >
          <label>
            Admin password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus />
          </label>
          {error && <p className="error">{error === "wrong password" ? "Wrong password." : error}</p>}
          <button type="submit" disabled={busy}>
            Unlock
          </button>
        </form>
      </Modal>
    );
  }

  const pending = invites.filter((i) => i.state === "pending");
  const open = invites.filter((i) => i.state === "invited");

  return (
    <Modal title="Invites" onClose={onClose} wide>
      <div className="form">
        <button type="button" disabled={busy} onClick={() => run(createInvite)}>
          Create invite link
        </button>
        {error && <p className="error">{error}</p>}

        <h3>Waiting for approval</h3>
        {pending.length === 0 && <p className="hint">Nobody yet.</p>}
        {pending.map((i) => (
          <div key={i.index} className="card invite-card">
            <strong>{i.name}</strong>
            <div className="invite-row">
              <span>{i.full_name}</span>
              <CopyButton text={i.full_name} />
            </div>
            <div className="invite-row">
              <span>{i.email}</span>
              <CopyButton text={i.email} />
            </div>
            <p className="hint">
              Add them to Spotify app <code>{i.spotify_app || "(none configured)"}</code> under User Management, then
              approve.
            </p>
            <div className="invite-row">
              <button type="button" disabled={busy} onClick={() => run(() => approveInvite(i.index))}>
                Approve
              </button>
              <button type="button" className="danger" disabled={busy} onClick={() => run(() => denyInvite(i.index))}>
                Deny
              </button>
            </div>
          </div>
        ))}

        <h3>Unused links</h3>
        {open.length === 0 && <p className="hint">None.</p>}
        {open.map((i) => (
          <div key={i.index} className="invite-row">
            <code className="invite-url">{inviteUrl(i.token)}</code>
            <CopyButton text={inviteUrl(i.token)} />
            <button type="button" className="ghost" disabled={busy} onClick={() => run(() => denyInvite(i.index))}>
              Revoke
            </button>
          </div>
        ))}
      </div>
    </Modal>
  );
}

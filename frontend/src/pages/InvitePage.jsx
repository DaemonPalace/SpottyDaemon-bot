import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { checkInvite, registerInvite } from "../api/client";

/** /invite/:token -- a one-time invite link from the admin (InvitesModal.jsx).
 * The invitee picks a profile name + password and gives the name/email of
 * their Spotify account, which the admin allowlists in the Spotify app
 * before approving. Linking happens on first login after approval. */
export default function InvitePage() {
  const { token } = useParams();
  const [valid, setValid] = useState(null); // null = checking
  const [form, setForm] = useState({ slot_name: "", password: "", full_name: "", email: "" });
  const [done, setDone] = useState(null); // profile name once registered
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    checkInvite(token)
      .then(() => setValid(true))
      .catch((err) => {
        setValid(false);
        setError(err.message);
      });
  }, [token]);

  const field = (key) => ({ value: form[key], onChange: (e) => setForm({ ...form, [key]: e.target.value }), required: true });

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await registerInvite(token, { ...form, slot_name: form.slot_name.trim().toLowerCase() });
      setDone(data.name);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="screen">
        <h1>Almost there</h1>
        <p className="hint">
          Your request is waiting for approval. Once it's approved, log in as <strong>{done}</strong> with your
          password and you'll be asked to link Spotify.
        </p>
        <Link to="/slots">Go to profiles</Link>
      </div>
    );
  }

  return (
    <div className="screen">
      <h1>You're invited</h1>
      {valid === null && <p className="hint">Checking invite...</p>}
      {valid === false && <p className="error">{error}</p>}
      {valid && (
        <form onSubmit={handleSubmit} className="form">
          <label>
            Profile name
            <input {...field("slot_name")} placeholder="e.g. alice" autoFocus />
          </label>
          <label>
            Password
            <input type="password" {...field("password")} />
          </label>
          <p className="hint">The Spotify account you'll link -- the admin needs these to give it access.</p>
          <label>
            Full name on Spotify
            <input {...field("full_name")} />
          </label>
          <label>
            Spotify account email
            <input type="email" {...field("email")} />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? "Sending..." : "Request access"}
          </button>
        </form>
      )}
    </div>
  );
}

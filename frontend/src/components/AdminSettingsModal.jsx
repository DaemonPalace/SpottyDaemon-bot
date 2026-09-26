import { useEffect, useState } from "react";
import { changeAdminPassword, getAdminSettings, login, updateAdminSettings } from "../api/client";
import Modal from "./Modal";

const SECRET_KEYS = new Set(["DISCORD_TOKEN"]);

/** Admin-only settings: bot token, Spotify Web API credentials, a few
 * self-host knobs, and the admin password -- everything else in
 * .env.example is either internal (API_TOKEN) or dev-only (DEV_GUILD_ID)
 * and stays out of the UI on purpose.
 *
 * Two-step like SlotList.jsx's delete-confirm: password unlocks an admin
 * session (POST /login), then the form itself relies on that session
 * cookie (supervisor/auth.py) rather than asking for the password again
 * on every field. */
export default function AdminSettingsModal({ onClose }) {
  const [unlocked, setUnlocked] = useState(false);
  const [password, setPassword] = useState("");
  const [fields, setFields] = useState(null); // { KEY: { value, isSet } }
  const [edits, setEdits] = useState({});
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (unlocked) getAdminSettings().then(setFields).catch((err) => setError(err.message));
  }, [unlocked]);

  async function handleUnlock(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(password);
      setUnlocked(true);
    } catch {
      setError("Wrong password.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSave(e) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      if (Object.keys(edits).length > 0) {
        const result = await updateAdminSettings(edits);
        setEdits({});
        setFields(await getAdminSettings());
        setNotice(result.restarted ? "Saved -- bot restarted to apply." : "Saved.");
      }
      if (newPassword) {
        if (newPassword.length < 8) throw new Error("new admin password must be at least 8 characters");
        await changeAdminPassword(newPassword);
        setNewPassword("");
        setNotice((n) => (n ? `${n} Admin password changed.` : "Admin password changed."));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!unlocked) {
    return (
      <Modal title="Admin settings" onClose={onClose}>
        <form onSubmit={handleUnlock} className="form">
          <label>
            Admin password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoFocus
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? "Unlocking..." : "Unlock"}
          </button>
        </form>
      </Modal>
    );
  }

  if (!fields) return null;

  function field(key, label, opts = {}) {
    const secret = SECRET_KEYS.has(key);
    return (
      <label key={key}>
        {label}
        {secret && fields[key].isSet && !(key in edits) && (
          <span className="hint" style={{ display: "block" }}>
            Currently set -- leave blank to keep it.
          </span>
        )}
        <input
          type={secret ? "password" : opts.type || "text"}
          placeholder={secret ? "••••••••" : fields[key].value}
          value={key in edits ? edits[key] : secret ? "" : fields[key].value}
          onChange={(e) => setEdits({ ...edits, [key]: e.target.value })}
        />
      </label>
    );
  }

  // Several Spotify apps, since a development-mode app only serves ~5
  // allowlisted users. Slot N uses app (N-1)/5 -- see bot/config.py's
  // SPOTIFY_APPS. A blank secret keeps the one saved for that client id.
  const apps = edits.SPOTIFY_APPS ?? fields.SPOTIFY_APPS.value.map((a) => ({ ...a, secret: "" }));
  const setApps = (next) => setEdits({ ...edits, SPOTIFY_APPS: next });
  const setApp = (i, patch) => setApps(apps.map((a, j) => (j === i ? { ...a, ...patch } : a)));

  return (
    <Modal title="Admin settings" onClose={onClose} wide>
      <form onSubmit={handleSave} className="form">
        <h3>Discord</h3>
        {field("DISCORD_TOKEN", "Bot token")}

        <h3>Spotify Web API</h3>
        <p className="hint">
          Now-playing, queue, search, playlists, library. Register an app at developer.spotify.com and add the Redirect
          URI below to it.
        </p>
        <p className="hint">
          Each app only lets the 5 users on its User Management list in, so add one app per 5 slots and raise Max slots
          to match. Slots are tied to an app by position -- removing or reordering apps means those users relink.
        </p>
        {apps.map((app, i) => (
          <fieldset key={i} className="form" style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "0.75rem" }}>
            <legend className="hint">
              App {i + 1} -- slots {i * 5 + 1}-{i * 5 + 5}
            </legend>
            <label>
              Client ID
              <input value={app.clientId} onChange={(e) => setApp(i, { clientId: e.target.value })} />
            </label>
            <label>
              Client secret
              <input
                type="password"
                placeholder={app.secretSet ? "Currently set -- leave blank to keep it" : "Optional"}
                value={app.secret}
                onChange={(e) => setApp(i, { secret: e.target.value })}
              />
            </label>
            <button type="button" className="ghost" onClick={() => setApps(apps.filter((_, j) => j !== i))}>
              Remove app
            </button>
          </fieldset>
        ))}
        <button type="button" className="ghost" onClick={() => setApps([...apps, { clientId: "", secret: "" }])}>
          Add Spotify app
        </button>
        {field("SPOTIFY_WEB_API_REDIRECT_URI", "Redirect URI")}

        <h3>Public dashboard</h3>
        <p className="hint">
          Domain this dashboard is served on. When set, /jam posts an "Open dashboard" link anyone in the channel can use
          without the slot password, and the Redirect URI above is set to its callback so Web API linking finishes on
          its own (no pasting urls back).
        </p>
        {field("PUBLIC_DASHBOARD_URL", "Public URL (e.g. https://music.example.com)")}

        <h3>Bot settings</h3>
        {field("MAX_SLOTS", "Max slots", { type: "number" })}
        {field("IDLE_SHUTDOWN_MINUTES", "Idle shutdown (minutes)", { type: "number" })}
        <label>
          Auto-shutdown on idle
          <select
            value={"ENABLE_AUTO_SHUTDOWN" in edits ? edits.ENABLE_AUTO_SHUTDOWN : fields.ENABLE_AUTO_SHUTDOWN.value || "true"}
            onChange={(e) => setEdits({ ...edits, ENABLE_AUTO_SHUTDOWN: e.target.value })}
          >
            <option value="true">On</option>
            <option value="false">Off</option>
          </select>
        </label>

        <h3>Admin password</h3>
        <label>
          New admin password (leave blank to keep current)
          <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        </label>

        {error && <p className="error">{error}</p>}
        {notice && <p className="hint">{notice}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Saving..." : "Save changes"}
        </button>
      </form>
    </Modal>
  );
}

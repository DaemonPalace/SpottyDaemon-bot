import { useState } from "react";
import { updateSlotSettings } from "../api/client";
import Modal from "./Modal";
import ProfileCircle from "./ProfileCircle";

/** Username/password/avatar editor. Requires the current password to apply
 * any change -- same confirm-before-change pattern as slot delete. Avatar
 * is a plain image URL, not an upload (see the settings screen note in the
 * plan): the slot already gets a real picture for free from the linked
 * Spotify account (bot/api.py's _seed_profile_info); this field only
 * overrides that. */
export default function SettingsModal({ name, avatarUrl, onClose, onUpdated }) {
  const [newName, setNewName] = useState(name);
  const [newPassword, setNewPassword] = useState("");
  const [newAvatarUrl, setNewAvatarUrl] = useState(avatarUrl || "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await updateSlotSettings(name, {
        currentPassword,
        newName: newName.trim() !== name ? newName.trim() : undefined,
        newPassword: newPassword || undefined,
        avatarUrl: newAvatarUrl !== (avatarUrl || "") ? newAvatarUrl.trim() : undefined,
      });
      onUpdated(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Profile settings" onClose={onClose}>
      <form onSubmit={handleSubmit} className="form">
        <div className="settings-avatar-row">
          <ProfileCircle name={newName} avatarUrl={newAvatarUrl || null} size="md" />
          <label style={{ flex: 1 }}>
            Profile picture URL
            <input
              value={newAvatarUrl}
              onChange={(e) => setNewAvatarUrl(e.target.value)}
              placeholder="https://..."
            />
          </label>
        </div>
        <label>
          Username
          <input value={newName} onChange={(e) => setNewName(e.target.value)} required />
        </label>
        <label>
          New password (leave blank to keep current)
          <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        </label>
        <label>
          Current password (required to save)
          <input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
            autoFocus
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Saving..." : "Save changes"}
        </button>
      </form>
    </Modal>
  );
}

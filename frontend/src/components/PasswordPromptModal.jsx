import { useState } from "react";
import { selectSlot } from "../api/client";
import Modal from "./Modal";

export default function PasswordPromptModal({ name, onClose, onUnlocked }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await selectSlot(name, password);
      onUnlocked(data);
    } catch (err) {
      setError(err.status === 401 ? "Wrong password." : err.message);
      setBusy(false);
    }
  }

  return (
    <Modal title={name} onClose={onClose}>
      <form onSubmit={handleSubmit} className="form">
        <label>
          Password
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

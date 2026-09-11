import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { submitSetup } from "../api/client";

export default function SetupWizard() {
  const [discordToken, setDiscordToken] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await submitSetup(discordToken.trim(), adminPassword);
      navigate("/starting");
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  }

  return (
    <div className="screen">
      <h1>Set up your bot</h1>
      <p className="hint">
        Grab your bot token from the Discord Developer Portal (your app → Bot page → Reset Token).
        Set an admin password to protect this control panel.
      </p>
      <form onSubmit={handleSubmit} className="form">
        <label>
          Discord bot token
          <input
            type="password"
            value={discordToken}
            onChange={(e) => setDiscordToken(e.target.value)}
            required
            autoComplete="off"
          />
        </label>
        <label>
          Admin password (min 8 characters)
          <input
            type="password"
            value={adminPassword}
            onChange={(e) => setAdminPassword(e.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Starting..." : "Start the bot"}
        </button>
      </form>
    </div>
  );
}

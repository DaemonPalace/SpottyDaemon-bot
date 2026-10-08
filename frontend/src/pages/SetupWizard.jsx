import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getStatus, submitSetup } from "../api/client";
import EndpointUrl from "../components/EndpointUrl";

export default function SetupWizard() {
  const [discordToken, setDiscordToken] = useState("");
  const [publicKey, setPublicKey] = useState("");
  // Hosted only: where Discord sends this bot's slash commands.
  const [endpointUrl, setEndpointUrl] = useState(null);
  const [adminPassword, setAdminPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  // Hosted installs: the one-time token from the welcome email's setup link
  // (supervisor/config.py's SETUP_TOKEN_SHA256). Absent on a self-host install.
  const [searchParams] = useSearchParams();
  const setupToken = searchParams.get("token") || "";

  useEffect(() => {
    getStatus()
      .then((data) => setEndpointUrl(data.interactions_endpoint_url || null))
      .catch(() => {});
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await submitSetup(discordToken.trim(), adminPassword, setupToken, publicKey.trim());
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
        {endpointUrl && " Copy the Public Key from the General Information page too."} Set an admin
        password to protect this control panel.
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
        {endpointUrl && (
          <label>
            Discord public key
            <input
              value={publicKey}
              onChange={(e) => setPublicKey(e.target.value)}
              required
              pattern="[0-9a-fA-F]{64}"
              title="64 letters and numbers, from your app's General Information page"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        )}
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
        {endpointUrl && (
          <EndpointUrl url={endpointUrl}>
            After you press Start, paste this into Interactions Endpoint URL on the same General Information
            page and save. Discord checks it against the public key above.
          </EndpointUrl>
        )}
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Starting..." : "Start the bot"}
        </button>
      </form>
    </div>
  );
}

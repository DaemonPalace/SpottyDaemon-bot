import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  addToQueue,
  finishWebApiLink,
  getPlayerState,
  regenerateJamToken,
  selectSlot,
  startWebApiLink,
} from "../api/client";
import { useSlotPlayer } from "../hooks/useSlotPlayer";

function PasswordGate({ name, onUnlocked }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    try {
      const data = await selectSlot(name, password);
      onUnlocked(data);
    } catch (err) {
      setError("Wrong password.");
    }
  }

  return (
    <div className="screen">
      <h1>{name}</h1>
      <form onSubmit={handleSubmit} className="form">
        <label>
          Slot password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit">Unlock</button>
      </form>
    </div>
  );
}

function WebApiLinkFlow({ name, onLinked }) {
  const [step, setStep] = useState("start");
  const [message, setMessage] = useState("");
  const [pastedUrl, setPastedUrl] = useState("");
  const [userId, setUserId] = useState(null);
  const [error, setError] = useState(null);

  async function handleStart() {
    setError(null);
    try {
      const data = await startWebApiLink(name);
      if (!data.success) throw new Error(data.message);
      setMessage(data.message);
      setUserId(data.user_id);
      setStep("finish");
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleFinish(e) {
    e.preventDefault();
    setError(null);
    try {
      const data = await finishWebApiLink(name, userId, pastedUrl.trim());
      if (!data.success) throw new Error(data.message);
      onLinked();
    } catch (err) {
      setError(err.message);
    }
  }

  if (step === "start") {
    return (
      <div className="card">
        <p>Spotify Web API isn't linked for this slot yet -- needed for now-playing/queue.</p>
        {error && <p className="error">{error}</p>}
        <button onClick={handleStart}>Link Spotify Web API</button>
      </div>
    );
  }

  return (
    <form onSubmit={handleFinish} className="form">
      <p className="hint" style={{ whiteSpace: "pre-wrap" }}>{message}</p>
      <label>
        Failed-redirect url
        <input value={pastedUrl} onChange={(e) => setPastedUrl(e.target.value)} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit">Finish linking</button>
    </form>
  );
}

function PlayerPanel({ name }) {
  const { nowPlaying, queue, error, refresh } = useSlotPlayer({
    name,
    fetchers: { getPlayerState },
  });
  const [uri, setUri] = useState("");

  async function handleAdd(e) {
    e.preventDefault();
    if (!uri.trim()) return;
    await addToQueue(name, uri.trim());
    setUri("");
    refresh();
  }

  if (error && error.status === 400) {
    return null; // not web-api-linked yet -- handled by the caller
  }

  return (
    <div className="card">
      <h2>Now playing</h2>
      {nowPlaying?.item ? (
        <p>
          <strong>{nowPlaying.item.name}</strong> -- {nowPlaying.item.artists?.map((a) => a.name).join(", ")}
        </p>
      ) : (
        <p className="hint">Nothing playing right now.</p>
      )}

      <h2>Queue</h2>
      <ul>
        {(queue?.queue || []).slice(0, 10).map((track, i) => (
          <li key={i}>{track.name} -- {track.artists?.map((a) => a.name).join(", ")}</li>
        ))}
      </ul>

      <form onSubmit={handleAdd} className="form inline">
        <input value={uri} onChange={(e) => setUri(e.target.value)} placeholder="spotify:track:..." />
        <button type="submit">Add to queue</button>
      </form>
    </div>
  );
}

export default function SlotProfile() {
  const { name } = useParams();
  const [unlocked, setUnlocked] = useState(null); // select() response
  const [webApiLinked, setWebApiLinked] = useState(null);

  if (!unlocked) {
    return (
      <PasswordGate
        name={name}
        onUnlocked={(data) => {
          setUnlocked(data);
          setWebApiLinked(data.web_api_linked);
        }}
      />
    );
  }

  async function handleRegenerateJam() {
    const data = await regenerateJamToken(name);
    setUnlocked({ ...unlocked, jam_token: data.jam_token });
  }

  const jamUrl = `${window.location.origin}/jam/${unlocked.jam_token}`;

  return (
    <div className="screen">
      <h1>{name}</h1>
      <p className="hint">
        To actually join a voice channel, run <code>/connect {name}</code> in Discord with this slot's password.
      </p>

      {webApiLinked ? (
        <PlayerPanel name={name} />
      ) : (
        <WebApiLinkFlow name={name} onLinked={() => setWebApiLinked(true)} />
      )}

      <div className="card">
        <h2>Jam mode</h2>
        <p className="hint">Anyone with this link can see now-playing and add to the queue -- no login needed.</p>
        <input readOnly value={jamUrl} onFocus={(e) => e.target.select()} />
        <button onClick={handleRegenerateJam}>Regenerate link</button>
      </div>
    </div>
  );
}

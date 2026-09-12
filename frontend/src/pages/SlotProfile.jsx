import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  addToQueue,
  getAlbum,
  getLibraryAlbums,
  getPlayerState,
  regenerateJamToken,
  searchTracks,
  selectSlot,
} from "../api/client";
import WebApiLinkFlow from "../components/WebApiLinkFlow";
import { AlbumLibrary, NowPlayingHero, QueueList, TrackSearch } from "../components/Player";
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

function PlayerPanel({ name }) {
  const { nowPlaying, queue, error, refresh } = useSlotPlayer({
    name,
    fetchers: { getPlayerState },
  });

  if (error && error.status === 400) {
    return null; // not web-api-linked yet -- handled by the caller
  }

  async function handleAdd(uri) {
    await addToQueue(name, uri);
    refresh();
  }

  return (
    <>
      <NowPlayingHero nowPlaying={nowPlaying} />
      <QueueList queue={queue} />
      <TrackSearch search={(q) => searchTracks(name, q)} onAdd={handleAdd} />
      <AlbumLibrary
        name={name}
        getAlbums={getLibraryAlbums}
        getAlbumDetail={getAlbum}
        onAdd={handleAdd}
        relinkPrompt={
          <WebApiLinkFlow
            name={name}
            prompt="Your library needs a fresh Spotify permission to show saved albums."
            onLinked={() => window.location.reload()}
          />
        }
      />
    </>
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
        <div className="card">
          <WebApiLinkFlow
            name={name}
            prompt="Spotify Web API isn't linked for this slot yet -- needed for now-playing, queue, search, and your library."
            onLinked={() => setWebApiLinked(true)}
          />
        </div>
      )}

      <div className="card">
        <h3>Jam mode</h3>
        <p className="hint">Anyone with this link can see now-playing and add to the queue -- no login needed.</p>
        <input readOnly value={jamUrl} onFocus={(e) => e.target.select()} />
        <button onClick={handleRegenerateJam}>Regenerate link</button>
      </div>
    </div>
  );
}

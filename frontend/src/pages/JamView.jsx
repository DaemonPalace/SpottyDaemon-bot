import { useState } from "react";
import { useParams } from "react-router-dom";
import { addToJamQueue, getJamPlayerState } from "../api/client";
import { useSlotPlayer } from "../hooks/useSlotPlayer";

export default function JamView() {
  const { jamToken } = useParams();
  const [uri, setUri] = useState("");
  const { nowPlaying, queue, error, refresh } = useSlotPlayer({
    jamToken,
    fetchers: { getPlayerState: getJamPlayerState },
  });

  async function handleAdd(e) {
    e.preventDefault();
    if (!uri.trim()) return;
    await addToJamQueue(jamToken, uri.trim());
    setUri("");
    refresh();
  }

  if (error && error.status === 404) {
    return (
      <div className="screen">
        <h1>Jam link not found</h1>
        <p className="hint">This link is invalid or has been regenerated. Ask for a fresh one.</p>
      </div>
    );
  }

  return (
    <div className="screen">
      <h1>Jam mode</h1>

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
    </div>
  );
}

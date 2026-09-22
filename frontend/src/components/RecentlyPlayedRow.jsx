import { useEffect, useState } from "react";
import { TrackThumb, trackArtists } from "./Player";

export default function RecentlyPlayedRow({ name, getRecentlyPlayed, onAdd, onPlayNow, relinkPrompt }) {
  const [state, setState] = useState({ status: "loading", tracks: [] });
  const [addedUri, setAddedUri] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getRecentlyPlayed(name);
        if (cancelled) return;
        if (data.tracks === null) {
          setState({ status: "needs_reauth", tracks: [] });
        } else {
          setState({ status: "ready", tracks: data.tracks });
        }
      } catch (err) {
        if (!cancelled) setState({ status: "error", tracks: [], error: err.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name, getRecentlyPlayed]);

  async function handleAdd(track) {
    try {
      await onAdd(track.uri);
      setAddedUri(track.uri);
      setTimeout(() => setAddedUri((current) => (current === track.uri ? null : current)), 1500);
    } catch {
      // swallow -- errors surface via the section it's shown in
    }
  }

  async function handlePlayNow(track) {
    try {
      await onPlayNow(track.uri);
    } catch {
      // swallow -- errors surface via the section it's shown in
    }
  }

  if (state.status === "loading" || state.status === "error" || state.tracks.length === 0) {
    if (state.status === "needs_reauth") {
      return (
        <section className="dash-section">
          <h3>Recently played</h3>
          {relinkPrompt}
        </section>
      );
    }
    return null; // quiet skip for loading/empty/error -- playlists below carry the section
  }

  return (
    <section className="dash-section">
      <h3>Recently played</h3>
      <div className="recent-row">
        {state.tracks.slice(0, 12).map((track, i) => (
          <div
            key={`${track.uri}-${i}`}
            className="recent-tile"
            role="button"
            tabIndex={0}
            onClick={() => handleAdd(track)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                handleAdd(track);
              }
            }}
          >
            <TrackThumb
              images={track.album?.images}
              alt={track.album?.name}
              size="md"
              onPlay={(e) => {
                e.stopPropagation();
                handlePlayNow(track);
              }}
            />
            <span className="album-tile-name">{track.name}</span>
            <span className="album-tile-artist">{trackArtists(track)}</span>
            {addedUri === track.uri && <span className="badge running recent-added-badge">Added</span>}
          </div>
        ))}
      </div>
    </section>
  );
}

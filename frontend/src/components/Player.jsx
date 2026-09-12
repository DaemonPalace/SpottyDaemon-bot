import { useEffect, useState } from "react";

export function trackArtists(track) {
  return (track.artists || []).map((a) => a.name).join(", ");
}

export function formatDuration(ms) {
  if (!ms && ms !== 0) return "";
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function artUrl(images, size) {
  if (!images || images.length === 0) return null;
  if (size === "sm") return images[images.length - 1].url;
  return images[0].url;
}

export function AlbumArt({ images, alt, size = "md", glow = false }) {
  const url = artUrl(images, size);
  const className = `album-art album-art-${size}${glow ? " album-art-glow" : ""}`;
  if (!url) {
    return (
      <div className={`${className} album-art-empty`} aria-hidden="true">
        <svg viewBox="0 0 24 24" width="40%" height="40%" fill="currentColor">
          <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" />
        </svg>
      </div>
    );
  }
  return <img className={className} src={url} alt={alt} loading="lazy" />;
}

export function NowPlayingHero({ nowPlaying }) {
  const item = nowPlaying?.item;

  if (!item) {
    return (
      <div className="hero card">
        <AlbumArt images={null} alt="" size="lg" />
        <div className="hero-info">
          <p className="hero-eyebrow">Now playing</p>
          <h2 className="hero-title hero-title-wrap">Nothing playing right now</h2>
          <p className="hint">Start something from Discord, search below, or pick an album from your library.</p>
        </div>
      </div>
    );
  }

  const progressPct = item.duration_ms ? Math.min(100, (nowPlaying.progress_ms / item.duration_ms) * 100) : 0;

  return (
    <div className="hero card">
      <AlbumArt images={item.album?.images} alt={item.album?.name} size="lg" glow={nowPlaying.is_playing} />
      <div className="hero-info">
        <p className="hero-eyebrow">Now playing</p>
        <h2 className="hero-title">{item.name}</h2>
        <p className="hero-subtitle">
          {trackArtists(item)}
          {item.album?.name ? ` — ${item.album.name}` : ""}
        </p>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${progressPct}%` }} />
        </div>
        <div className="progress-times">
          <span>{formatDuration(nowPlaying.progress_ms)}</span>
          <span>{formatDuration(item.duration_ms)}</span>
        </div>
      </div>
    </div>
  );
}

export function QueueList({ queue }) {
  const tracks = (queue?.queue || []).slice(0, 10);
  return (
    <div className="card">
      <h3>Up next</h3>
      {tracks.length === 0 ? (
        <p className="hint">Queue's empty -- add something below.</p>
      ) : (
        <ul className="track-list">
          {tracks.map((track, i) => (
            <li key={`${track.uri}-${i}`} className="track-row">
              <AlbumArt images={track.album?.images} alt={track.album?.name} size="sm" />
              <div className="track-info">
                <span className="track-name">{track.name}</span>
                <span className="track-artist">{trackArtists(track)}</span>
              </div>
              <span className="track-duration">{formatDuration(track.duration_ms)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TrackSearch({ search, onAdd }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [addedUri, setAddedUri] = useState(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setBusy(true);
    const timer = setTimeout(async () => {
      try {
        const data = await search(trimmed);
        if (!cancelled) {
          setResults(data.tracks || []);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setBusy(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, search]);

  async function handleAdd(track) {
    setError(null);
    try {
      await onAdd(track.uri);
      setAddedUri(track.uri);
      setTimeout(() => setAddedUri((current) => (current === track.uri ? null : current)), 1500);
    } catch {
      setError("Couldn't add that -- make sure the bot is connected to a voice channel first.");
    }
  }

  return (
    <div className="card">
      <h3>Search</h3>
      <input
        type="search"
        className="search-input"
        placeholder="Find a song..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {error && <p className="error">{error}</p>}
      {busy && <p className="hint">Searching...</p>}
      {results.length > 0 && (
        <ul className="track-list">
          {results.slice(0, 8).map((track) => (
            <li key={track.uri} className="track-row">
              <AlbumArt images={track.album?.images} alt={track.album?.name} size="sm" />
              <div className="track-info">
                <span className="track-name">{track.name}</span>
                <span className="track-artist">{trackArtists(track)}</span>
              </div>
              <button onClick={() => handleAdd(track)} disabled={addedUri === track.uri}>
                {addedUri === track.uri ? "Added" : "Add"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AlbumLibrary({ name, getAlbums, getAlbumDetail, onAdd, relinkPrompt }) {
  const [state, setState] = useState({ status: "loading", albums: [] });
  const [selected, setSelected] = useState(null); // { album, tracks } or null
  const [addedUri, setAddedUri] = useState(null);
  const [addError, setAddError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getAlbums(name);
        if (cancelled) return;
        if (data.albums === null) {
          setState({ status: "needs_reauth", albums: [] });
        } else {
          setState({ status: "ready", albums: data.albums });
        }
      } catch (err) {
        if (!cancelled) setState({ status: "error", albums: [], error: err.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name, getAlbums]);

  async function openAlbum(album) {
    setSelected({ album, tracks: null });
    const detail = await getAlbumDetail(name, album.id);
    setSelected({ album, tracks: detail.tracks?.items || [] });
  }

  async function handleAdd(track) {
    setAddError(null);
    try {
      await onAdd(track.uri);
      setAddedUri(track.uri);
      setTimeout(() => setAddedUri((current) => (current === track.uri ? null : current)), 1500);
    } catch {
      setAddError("Couldn't add that -- make sure the bot is connected to a voice channel first.");
    }
  }

  if (state.status === "loading") {
    return (
      <div className="card">
        <h3>Your library</h3>
        <p className="hint">Loading your saved albums...</p>
      </div>
    );
  }

  if (state.status === "needs_reauth") {
    return (
      <div className="card">
        <h3>Your library</h3>
        {relinkPrompt}
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="card">
        <h3>Your library</h3>
        <p className="error">{state.error}</p>
      </div>
    );
  }

  if (state.albums.length === 0) {
    return (
      <div className="card">
        <h3>Your library</h3>
        <p className="hint">No saved albums on this account yet -- save some on Spotify and check back.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Your library</h3>
      <div className="album-grid">
        {state.albums.map((album) => (
          <button key={album.id} className="album-tile" onClick={() => openAlbum(album)}>
            <AlbumArt images={album.images} alt={album.name} size="md" />
            <span className="album-tile-name">{album.name}</span>
            <span className="album-tile-artist">{trackArtists(album)}</span>
          </button>
        ))}
      </div>

      {selected && (
        <div className="album-detail">
          <div className="album-detail-header">
            <AlbumArt images={selected.album.images} alt={selected.album.name} size="sm" />
            <div>
              <p className="track-name">{selected.album.name}</p>
              <p className="hint">{trackArtists(selected.album)}</p>
            </div>
            <button className="ghost" onClick={() => setSelected(null)}>Close</button>
          </div>
          {addError && <p className="error">{addError}</p>}
          {selected.tracks === null ? (
            <p className="hint">Loading tracks...</p>
          ) : (
            <ul className="track-list">
              {selected.tracks.map((track) => (
                <li key={track.uri} className="track-row">
                  <span className="track-number">{track.track_number}</span>
                  <div className="track-info">
                    <span className="track-name">{track.name}</span>
                    <span className="track-artist">{trackArtists(track)}</span>
                  </div>
                  <button onClick={() => handleAdd(track)} disabled={addedUri === track.uri}>
                    {addedUri === track.uri ? "Added" : "Add"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

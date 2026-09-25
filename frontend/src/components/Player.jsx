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

/** AlbumArt with a small corner play button overlaid bottom-right --
 * "play this now" (play_uri), distinct from the row's own onAdd
 * ("queue this"). onPlay is omitted where playing now doesn't make sense
 * (e.g. the queue list). */
export function TrackThumb({ images, alt, size = "sm", onPlay }) {
  return (
    <div className="track-thumb">
      <AlbumArt images={images} alt={alt} size={size} />
      {onPlay && (
        <button className="track-thumb-play" onClick={onPlay} aria-label="Play now" title="Play now">
          <svg viewBox="0 0 24 24" width="60%" height="60%" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>
      )}
    </div>
  );
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

function TrackRowBody({ track }) {
  return (
    <>
      <AlbumArt images={track.album?.images} alt={track.album?.name} size="sm" />
      <div className="track-info">
        <span className="track-name">{track.name}</span>
        <span className="track-artist">{trackArtists(track)}</span>
      </div>
      <span className="track-duration">{formatDuration(track.duration_ms)}</span>
    </>
  );
}

function QueueSection({ title, hint, tracks }) {
  return (
    <section className="queue-section">
      <h3>{title}</h3>
      {tracks.length === 0 ? (
        <p className="hint">{hint}</p>
      ) : (
        <ul className="track-list">
          {tracks.map((track, i) => (
            <li key={`${track.uri}-${i}`} className="track-row">
              <TrackRowBody track={track} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Left panel: songs queued from the Spotify app, the bot's own Up next
 * (reorderable -- Spotify's API can't reorder its queue, see bot/up_next.py),
 * then the rest of the playing playlist/album. The first Up next entry may
 * be "staged": already handed to Spotify's queue, so it can't move anymore.
 * onMove/onRemove omitted (jam guests) = read-only. */
export function QueueList({ queue, onMove, onRemove }) {
  const [dragId, setDragId] = useState(null);
  const upNext = queue?.upNext || [];
  const firstMovable = upNext.findIndex((e) => !e.staged);
  const editable = Boolean(onMove && onRemove);

  function drop(targetIndex) {
    const id = dragId;
    setDragId(null);
    if (id === null || targetIndex < firstMovable) return;
    // Server indexes count only the unstaged entries.
    onMove(id, targetIndex - firstMovable);
  }

  return (
    <div className="card">
      <QueueSection
        title="Spotify App Queue"
        hint="Nothing queued from the Spotify app."
        tracks={queue?.appQueue || []}
      />

      <section className="queue-section">
        <h3>Up next</h3>
        {upNext.length === 0 ? (
          <p className="hint">Empty -- add songs with search or a playlist.</p>
        ) : (
          <ul className="track-list">
            {upNext.map((entry, i) => {
              const movable = editable && !entry.staged;
              return (
                <li
                  key={entry.id}
                  className={`track-row${dragId === entry.id ? " dragging" : ""}`}
                  draggable={movable}
                  onDragStart={() => setDragId(entry.id)}
                  onDragEnd={() => setDragId(null)}
                  onDragOver={(e) => movable && dragId !== null && e.preventDefault()}
                  onDrop={() => drop(i)}
                >
                  <TrackRowBody track={entry.track} />
                  {entry.staged ? (
                    <span className="queue-badge" title="Already handed to Spotify's queue">next</span>
                  ) : (
                    editable && (
                      <span className="queue-actions">
                        <button
                          type="button"
                          className="ghost"
                          aria-label="Move up"
                          disabled={i === firstMovable}
                          onClick={() => onMove(entry.id, i - firstMovable - 1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          aria-label="Move down"
                          disabled={i === upNext.length - 1}
                          onClick={() => onMove(entry.id, i - firstMovable + 1)}
                        >
                          ↓
                        </button>
                        <button type="button" className="ghost" aria-label="Remove" onClick={() => onRemove(entry.id)}>
                          ✕
                        </button>
                      </span>
                    )
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <QueueSection title="Playlist" hint="Nothing else coming up." tracks={(queue?.playlist || []).slice(0, 10)} />
    </div>
  );
}

export function TrackSearch({ search, onAdd, onPlayNow }) {
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

  async function handlePlayNow(track) {
    setError(null);
    try {
      await onPlayNow(track.uri);
    } catch {
      setError("Couldn't play that -- make sure the bot is connected to a voice channel first.");
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
              <TrackThumb
                images={track.album?.images}
                alt={track.album?.name}
                size="sm"
                onPlay={() => handlePlayNow(track)}
              />
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

export function AlbumLibrary({ name, getAlbums, getAlbumDetail, onAdd, onPlayNow, relinkPrompt }) {
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

  async function handlePlayNow(track) {
    setAddError(null);
    try {
      await onPlayNow(track.uri);
    } catch {
      setAddError("Couldn't play that -- make sure the bot is connected to a voice channel first.");
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
                  <button className="ghost track-play-now" onClick={() => handlePlayNow(track)} aria-label="Play now" title="Play now">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  </button>
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

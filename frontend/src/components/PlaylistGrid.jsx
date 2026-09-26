import { useEffect, useState } from "react";
import { AlbumArt, TrackThumb, playlistTotal, playlistTracks, trackArtists } from "./Player";

/** Playlist grid + track-list detail, mirroring AlbumLibrary's shape
 * (getPlaylists/getPlaylist instead of getAlbums/getAlbumDetail). */
export default function PlaylistGrid({
  name,
  getPlaylists,
  getPlaylist,
  onAdd,
  onPlayNow,
  onPlayPlaylist,
  onQueuePlaylist,
  relinkPrompt,
}) {
  const [state, setState] = useState({ status: "loading", playlists: [] });
  const [selected, setSelected] = useState(null); // { playlist, tracks } or null
  const [addedUri, setAddedUri] = useState(null);
  const [addError, setAddError] = useState(null);
  const [playlistActionError, setPlaylistActionError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getPlaylists(name);
        if (cancelled) return;
        if (data.playlists === null) {
          setState({ status: "needs_reauth", playlists: [] });
          return;
        }
        setState({ status: "ready", playlists: data.playlists });
      } catch (err) {
        if (!cancelled) setState({ status: "error", playlists: [], error: err.message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name, getPlaylists]);

  async function openPlaylist(playlist) {
    setSelected({ playlist, tracks: null });
    const detail = await getPlaylist(name, playlist.id);
    setSelected({ playlist, tracks: playlistTracks(detail) });
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

  async function handlePlayPlaylist(playlist) {
    setPlaylistActionError(null);
    try {
      await onPlayPlaylist(playlist.id);
    } catch {
      setPlaylistActionError("Couldn't play that -- make sure the bot is connected to a voice channel first.");
    }
  }

  async function handleQueuePlaylist(playlist) {
    setPlaylistActionError(null);
    try {
      await onQueuePlaylist(playlist.id);
    } catch {
      setPlaylistActionError("Couldn't queue that -- make sure the bot is connected to a voice channel first.");
    }
  }

  if (state.status === "loading") {
    return (
      <section className="dash-section">
        <h3>Your playlists</h3>
        <p className="hint">Loading playlists...</p>
      </section>
    );
  }

  if (state.status === "needs_reauth") {
    return (
      <section className="dash-section">
        <h3>Your playlists</h3>
        {relinkPrompt}
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="dash-section">
        <h3>Your playlists</h3>
        <p className="error">{state.error}</p>
      </section>
    );
  }

  if (state.playlists.length === 0) {
    return (
      <section className="dash-section">
        <h3>Your playlists</h3>
        <p className="hint">No playlists on this account yet.</p>
      </section>
    );
  }

  return (
    <section className="dash-section">
      <h3>Your playlists</h3>
      <div className="album-grid">
        {state.playlists.map((playlist) => (
          <button key={playlist.id} className="album-tile" onClick={() => openPlaylist(playlist)}>
            <AlbumArt images={playlist.images} alt={playlist.name} size="md" />
            <span className="album-tile-name">{playlist.name}</span>
            <span className="album-tile-artist">{playlistTotal(playlist) ?? "…"} tracks</span>
          </button>
        ))}
      </div>

      {selected && (
        <div className="album-detail">
          <div className="album-detail-header">
            <AlbumArt images={selected.playlist.images} alt={selected.playlist.name} size="sm" />
            <div>
              <p className="track-name">{selected.playlist.name}</p>
              <p className="hint">{selected.tracks?.length ?? "…"} tracks</p>
            </div>
            <button onClick={() => handlePlayPlaylist(selected.playlist)}>Play</button>
            <button className="ghost" onClick={() => handleQueuePlaylist(selected.playlist)}>Queue all</button>
            <button className="ghost" onClick={() => setSelected(null)}>Close</button>
          </div>
          {playlistActionError && <p className="error">{playlistActionError}</p>}
          {addError && <p className="error">{addError}</p>}
          {selected.tracks === null ? (
            <p className="hint">Loading tracks...</p>
          ) : (
            <ul className="track-list">
              {selected.tracks.map((track, i) => (
                <li key={`${track.uri}-${i}`} className="track-row">
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
      )}
    </section>
  );
}

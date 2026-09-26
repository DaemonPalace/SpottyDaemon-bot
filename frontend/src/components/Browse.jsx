import { useEffect, useState } from "react";
import { AlbumArt, TrackThumb, formatDuration } from "./Player";

const PLAY_ERROR = "Couldn't do that -- make sure the bot is connected to a voice channel first.";

/** One /search call (all four types, 10 each) plus per-type Show more
 * paging. search(q, type, offset) is the slot-bound searchSpotify. */
function useSearch(search, q, type) {
  const [state, setState] = useState({ data: null, loading: false, error: null });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!q) return;
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    search(q, type).then(
      (data) => !cancelled && setState({ data, loading: false, error: null }),
      (err) => !cancelled && setState((s) => ({ ...s, loading: false, error: err.message }))
    );
    return () => {
      cancelled = true;
    };
  }, [search, q, type, attempt]);

  // key: "tracks" | "artists" | "albums" | "playlists"
  async function more(key) {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const page = (await search(q, key.slice(0, -1), state.data[key].items.length))[key];
      setState((s) => ({
        loading: false,
        error: null,
        data: { ...s.data, [key]: { items: [...s.data[key].items, ...page.items], next: page.next } },
      }));
    } catch (err) {
      setState((s) => ({ ...s, loading: false, error: err.message }));
    }
  }

  return { ...state, retry: () => setAttempt((a) => a + 1), more };
}

/** Runs a view-level action (Play album, Add all...) and reports the
 * outcome in one line under the header. */
function useAction() {
  const [message, setMessage] = useState(null);
  async function run(fn, success) {
    setMessage(null);
    try {
      const result = await fn();
      if (success) setMessage({ ok: true, text: success(result) });
    } catch {
      setMessage({ ok: false, text: PLAY_ERROR });
    }
  }
  const node = message && <p className={message.ok ? "hint" : "error"}>{message.text}</p>;
  return [run, node];
}

function ArtistLinks({ artists, onOpen }) {
  return (artists || []).map((artist, i) => (
    <span key={artist.id || i}>
      {i > 0 && ", "}
      {artist.id ? (
        <button type="button" className="link-btn" onClick={() => onOpen({ kind: "artist", item: artist })}>
          {artist.name}
        </button>
      ) : (
        artist.name
      )}
    </span>
  ));
}

/** Song rows: art (with Play now for owners), title, artist links, length, Add. */
export function SongList({ tracks, actions }) {
  const [addedUri, setAddedUri] = useState(null);
  const [error, setError] = useState(null);

  async function handle(fn, uri) {
    setError(null);
    try {
      await fn(uri);
      return true;
    } catch {
      setError(PLAY_ERROR);
      return false;
    }
  }

  async function add(track) {
    if (!(await handle(actions.onAdd, track.uri))) return;
    setAddedUri(track.uri);
    setTimeout(() => setAddedUri((current) => (current === track.uri ? null : current)), 1500);
  }

  return (
    <>
      {error && <p className="error">{error}</p>}
      <ul className="track-list">
        {tracks.map((track, i) => (
          <li key={`${track.uri}-${i}`} className="track-row">
            <TrackThumb
              images={track.album?.images}
              alt={track.album?.name}
              size="sm"
              onPlay={actions.guest ? undefined : () => handle(actions.onPlayNow, track.uri)}
            />
            <div className="track-info">
              <span className="track-name">{track.name}</span>
              <span className="track-artist">
                <ArtistLinks artists={track.artists} onOpen={actions.onOpen} />
              </span>
            </div>
            <span className="track-duration">{formatDuration(track.duration_ms)}</span>
            <button onClick={() => add(track)} disabled={addedUri === track.uri}>
              {addedUri === track.uri ? "Added" : "Add"}
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

function tileSubtitle(kind, item) {
  if (kind === "artist") return item.genres?.[0] || "Artist";
  if (kind === "album") return `${item.release_date?.slice(0, 4) || ""} · ${(item.artists || []).map((a) => a.name).join(", ")}`;
  return `by ${item.owner?.display_name || "unknown"}`;
}

function TileGrid({ kind, items, onOpen }) {
  return (
    <div className="album-grid">
      {items.map((item) => (
        <button
          key={item.id}
          className={`album-tile${kind === "artist" ? " album-tile-round" : ""}`}
          onClick={() => onOpen({ kind, item })}
        >
          <AlbumArt images={item.images} alt={item.name} size="md" />
          <span className="album-tile-name">{item.name}</span>
          <span className="album-tile-artist">{tileSubtitle(kind, item)}</span>
        </button>
      ))}
    </div>
  );
}

function ShowMore({ page, loading, onMore }) {
  if (!page?.next) return null;
  return (
    <button className="ghost show-more" onClick={onMore} disabled={loading}>
      {loading ? "Loading..." : "Show more"}
    </button>
  );
}

const TABS = [
  { key: "all", label: "All" },
  { key: "tracks", label: "Songs", kind: "song" },
  { key: "artists", label: "Artists", kind: "artist" },
  { key: "albums", label: "Albums", kind: "album" },
  { key: "playlists", label: "Playlists", kind: "playlist" },
];

function TopResult({ q, data, actions }) {
  const artist = data.artists?.items[0];
  if (artist && artist.name.toLowerCase() === q.toLowerCase()) {
    return (
      <button className="top-result" onClick={() => actions.onOpen({ kind: "artist", item: artist })}>
        <AlbumArt images={artist.images} alt={artist.name} size="md" />
        <span className="top-result-name">{artist.name}</span>
        <span className="hint">Artist</span>
      </button>
    );
  }
  const track = data.tracks?.items[0];
  return track ? <SongList tracks={[track]} actions={actions} /> : null;
}

/** UC-01..04: one query, an All tab (top result + 5 of each type) and a
 * tab per type showing 10 at a time with Show more. */
export function SearchResults({ q, search, actions }) {
  const [tab, setTab] = useState("all");
  const { data, loading, error, retry, more } = useSearch(search, q);

  const empty = data && TABS.slice(1).every((t) => !data[t.key]?.items.length);

  function section(t, items) {
    if (!items.length) return null;
    return (
      <section key={t.key} className="dash-section">
        <div className="section-head">
          <h3>{t.label}</h3>
          {tab === "all" && (
            <button className="ghost" onClick={() => setTab(t.key)}>
              Show all
            </button>
          )}
        </div>
        {t.key === "tracks" ? (
          <SongList tracks={items} actions={actions} />
        ) : (
          <TileGrid kind={t.kind} items={items} onOpen={actions.onOpen} />
        )}
      </section>
    );
  }

  return (
    <div className={`search-results${loading ? " is-loading" : ""}`}>
      <div className="search-tabs" role="tablist" aria-label="Search result types">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={`chip${tab === t.key ? " chip-on" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <p className="error">
          Search failed: {error}{" "}
          <button className="ghost" onClick={retry}>
            Retry
          </button>
        </p>
      )}

      {!data && loading && <p className="hint">Searching...</p>}

      {empty && (
        <p className="hint">
          No songs match '{q}'. Check the spelling or search the artist.
        </p>
      )}

      {data && !empty && tab === "all" && (
        <>
          <TopResult q={q} data={data} actions={actions} />
          {TABS.slice(1).map((t) => section(t, (data[t.key]?.items || []).slice(0, 5)))}
        </>
      )}

      {data && !empty && tab !== "all" && (
        <>
          {data[tab]?.items.length ? (
            section(
              TABS.find((t) => t.key === tab),
              data[tab].items
            )
          ) : (
            <p className="hint">
              No {TABS.find((t) => t.key === tab).label.toLowerCase()} match '{q}'.
            </p>
          )}
          <ShowMore page={data[tab]} loading={loading} onMore={() => more(tab)} />
        </>
      )}
    </div>
  );
}

function DetailHeader({ images, round, eyebrow, title, subtitle, children }) {
  return (
    <div className="detail-header">
      <div className={round ? "album-tile-round" : undefined}>
        <AlbumArt images={images} alt={title} size="lg" />
      </div>
      <div className="detail-info">
        <p className="hero-eyebrow">{eyebrow}</p>
        <h2 className="hero-title">{title}</h2>
        {subtitle && <p className="hero-subtitle">{subtitle}</p>}
        <div className="detail-actions">{children}</div>
      </div>
    </div>
  );
}

function useDetail(fetcher, id) {
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null });
    fetcher(id).then(
      (data) => !cancelled && setState({ data, error: null }),
      (err) => !cancelled && setState({ data: null, error: err.message })
    );
    return () => {
      cancelled = true;
    };
  }, [fetcher, id]);
  return state;
}

const newestFirst = (a, b) => (b.release_date || "").localeCompare(a.release_date || "");

/** UC-02. No top-tracks endpoint any more, so "Songs by" is an
 * artist:"Name" search -- labelled as such, never "Popular". */
function ArtistView({ item, api, actions }) {
  const { data, error } = useDetail(api.getArtist, item.id);
  const artist = data?.artist || item;
  const songs = useSearch(api.search, `artist:"${artist.name}"`, "track");
  const [run, message] = useAction();
  const albums = (data?.albums || []).filter((a) => a.album_type !== "single").sort(newestFirst);
  const singles = (data?.albums || []).filter((a) => a.album_type === "single").sort(newestFirst);

  return (
    <>
      <DetailHeader
        images={artist.images}
        round
        eyebrow="Artist"
        title={artist.name}
        subtitle={(artist.genres || []).join(", ")}
      >
        {!actions.guest && <button onClick={() => run(() => api.playContext(artist.uri))}>Play artist</button>}
      </DetailHeader>
      {message}
      {error && <p className="error">{error}</p>}

      <section className="dash-section">
        <h3>Songs by {artist.name}</h3>
        {songs.error && <p className="error">{songs.error}</p>}
        {songs.data ? (
          <>
            <SongList tracks={songs.data.tracks.items} actions={actions} />
            <ShowMore page={songs.data.tracks} loading={songs.loading} onMore={() => songs.more("tracks")} />
          </>
        ) : (
          <p className="hint">Loading songs...</p>
        )}
      </section>
      {albums.length > 0 && (
        <section className="dash-section">
          <h3>Albums</h3>
          <TileGrid kind="album" items={albums} onOpen={actions.onOpen} />
        </section>
      )}
      {singles.length > 0 && (
        <section className="dash-section">
          <h3>Singles &amp; EPs</h3>
          <TileGrid kind="album" items={singles} onOpen={actions.onOpen} />
        </section>
      )}
    </>
  );
}

/** UC-03 */
function AlbumView({ item, api, actions }) {
  const { data, error } = useDetail(api.getAlbum, item.id);
  const album = data || item;
  const [run, message] = useAction();
  // Album track objects have no album of their own -- give them this one's art.
  const tracks = (data?.tracks?.items || []).map((t) => ({ ...t, album }));
  const totalMs = tracks.reduce((sum, t) => sum + (t.duration_ms || 0), 0);

  return (
    <>
      <DetailHeader
        images={album.images}
        eyebrow="Album"
        title={album.name}
        subtitle={
          <>
            <ArtistLinks artists={album.artists} onOpen={actions.onOpen} />
            {album.release_date && ` · ${album.release_date.slice(0, 4)}`}
            {album.total_tracks && ` · ${album.total_tracks} songs`}
            {totalMs > 0 && `, ${Math.round(totalMs / 60000)} min`}
          </>
        }
      >
        {!actions.guest && (
          <>
            <button onClick={() => run(() => api.playContext(album.uri))}>Play album</button>
            <button
              className="ghost"
              onClick={() => run(() => api.queueAlbum(album.id), (r) => `Added ${r.queued} songs to Up next.`)}
            >
              Add album to Up next
            </button>
          </>
        )}
      </DetailHeader>
      {message}
      {error && <p className="error">{error}</p>}
      {data ? <SongList tracks={tracks} actions={actions} /> : !error && <p className="hint">Loading songs...</p>}
    </>
  );
}

/** UC-04. Spotify only lists songs of playlists the linked account owns
 * or collaborates on; others come back without items and can only be played. */
function PlaylistView({ item, api, actions }) {
  const { data, error } = useDetail(api.getPlaylist, item.id);
  const playlist = data || item;
  const [run, message] = useAction();
  const tracks = (data?.tracks?.items || []).map((i) => i.track).filter(Boolean);
  const listable = tracks.length > 0;

  return (
    <>
      <DetailHeader
        images={playlist.images}
        eyebrow="Playlist"
        title={playlist.name}
        subtitle={`by ${playlist.owner?.display_name || "unknown"}${listable ? ` · ${tracks.length} songs` : ""}`}
      >
        {!actions.guest && (
          <>
            <button onClick={() => run(() => api.playPlaylist(playlist.id))}>Play playlist</button>
            {listable && (
              <button
                className="ghost"
                onClick={() => run(() => api.queuePlaylist(playlist.id), (r) => `Added ${r.queued} songs to Up next.`)}
              >
                Add all to Up next
              </button>
            )}
          </>
        )}
      </DetailHeader>
      {message}
      {!data && !error && <p className="hint">Loading songs...</p>}
      {(error || (data && !listable)) && (
        <p className="hint">
          Spotify only shares the song list of playlists you own or collaborate on, so this one can be played but
          not added song by song.
        </p>
      )}
      {listable && <SongList tracks={tracks} actions={actions} />}
    </>
  );
}

const VIEWS = { artist: ArtistView, album: AlbumView, playlist: PlaylistView };

export function DetailView({ view, api, actions, onBack }) {
  const View = VIEWS[view.kind];
  return (
    <div className="detail-view">
      <button className="ghost back-btn" onClick={onBack}>
        ← Back
      </button>
      <View key={view.item.id} item={view.item} api={api} actions={actions} />
    </div>
  );
}

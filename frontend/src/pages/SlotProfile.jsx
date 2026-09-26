import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  addToQueue,
  finishLink,
  startLink,
  moveUpNext,
  removeUpNext,
  getAlbum,
  getArtist,
  getJamSlot,
  getLibraryAlbums,
  getPlayerState,
  getPlaylist,
  getPlaylists,
  getPlaylistTrackCount,
  getRecentlyPlayed,
  setSlotToken,
  nextTrack,
  pausePlayback,
  playPlayback,
  playContext,
  playPlaylist,
  playTrack,
  previousTrack,
  queueAlbum,
  queuePlaylist,
  searchSpotify,
  seekPlayback,
  setPlaybackVolume,
} from "../api/client";
import AdminSettingsModal from "../components/AdminSettingsModal";
import { DetailView, SearchResults } from "../components/Browse";
import NowPlayingBar from "../components/NowPlayingBar";
import PasswordPromptModal from "../components/PasswordPromptModal";
import { AlbumLibrary, QueueList } from "../components/Player";
import PlaylistGrid from "../components/PlaylistGrid";
import ProfileCircle from "../components/ProfileCircle";
import RecentlyPlayedRow from "../components/RecentlyPlayedRow";
import SearchBar from "../components/SearchBar";
import SettingsModal from "../components/SettingsModal";
import WebApiLinkFlow from "../components/WebApiLinkFlow";
import { useSlotPlayer } from "../hooks/useSlotPlayer";

/** Mounted at /slots/:name (password-gated owner view) and /jam/:jamToken
 * (guest view from a /jam panel's "Open dashboard" link -- the token stands
 * in for the password, and owner-only controls are hidden). */
export default function SlotProfile() {
  const { name: routeName, jamToken } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  // select() response. History state from before slot tokens existed (no
  // slot_token) can't make authenticated calls -- re-prompt instead.
  const [unlocked, setUnlocked] = useState(jamToken || !location.state?.slot_token ? null : location.state);
  const [jamError, setJamError] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showAdminSettings, setShowAdminSettings] = useState(false);
  const name = jamToken ? unlocked?.name : routeName;
  const [query, setQuery] = useState("");
  // Artist/album/playlist views opened from search, innermost last; Back pops.
  const [views, setViews] = useState([]);
  const onQueryChange = useCallback((q) => {
    setQuery(q);
    setViews([]);
  }, []);
  // Slot-bound fetchers for Browse.jsx -- memoized so its fetch effects
  // only re-run when the slot changes.
  const browseApi = useMemo(
    () => ({
      search: (q, type, offset) => searchSpotify(name, q, type, offset),
      getArtist: (id) => getArtist(name, id),
      getAlbum: (id) => getAlbum(name, id),
      getPlaylist: (id) => getPlaylist(name, id),
      playContext: (uri) => playContext(name, uri),
      queueAlbum: (id) => queueAlbum(name, id),
      playPlaylist: (id) => playPlaylist(name, id),
      queuePlaylist: (id) => queuePlaylist(name, id),
    }),
    [name]
  );
  // Set during render, not in an effect: child components' fetch effects
  // run before this component's own effects would.
  setSlotToken(jamToken || unlocked?.slot_token);

  useEffect(() => {
    if (jamToken) getJamSlot(jamToken).then(setUnlocked).catch(setJamError);
  }, [jamToken]);

  const { nowPlaying, queue, error: playerError, refresh } = useSlotPlayer({
    name: unlocked ? name : undefined,
    fetchers: { getPlayerState },
  });

  // Rejected credential: the jam ended, or the slot session expired/its
  // password changed elsewhere.
  useEffect(() => {
    if (playerError?.status !== 401) return;
    if (jamToken) setJamError({ status: 404 });
    setUnlocked(null);
  }, [playerError, jamToken]);

  if (jamToken && !unlocked) {
    return (
      <div className="screen">
        {jamError ? (
          <>
            <h1>{jamError.status === 404 ? "This jam has ended" : "Couldn't open this jam"}</h1>
            <p className="hint">
              {jamError.status === 404 ? "Ask for a fresh link -- run /jam in Discord again." : jamError.message}
            </p>
          </>
        ) : (
          <p className="hint">Joining jam...</p>
        )}
      </div>
    );
  }

  if (!unlocked) {
    return (
      <PasswordPromptModal
        name={name}
        onClose={() => navigate("/slots")}
        onUnlocked={setUnlocked}
      />
    );
  }

  // Approved from an invite but not linked yet: the player link comes first.
  if (!jamToken && unlocked.state !== "claimed") {
    return (
      <div className="screen">
        <h1>Welcome, {name}</h1>
        <WebApiLinkFlow
          name={name}
          prompt="You're approved! Link your Spotify account to finish setting up this profile."
          start={startLink}
          finish={(_name, userId, url) => finishLink(userId, url || null)}
          onLinked={(direct) => setUnlocked({ ...unlocked, state: "claimed", web_api_linked: direct })}
        />
      </div>
    );
  }

  const webApiLinked = unlocked.web_api_linked;

  // Guests can't re-authorize someone else's Spotify -- just say what's missing.
  function relinkPrompt(prompt) {
    if (jamToken) return <p className="hint">{prompt}</p>;
    return <WebApiLinkFlow name={name} prompt={prompt} onLinked={() => window.location.reload()} />;
  }

  async function handleAdd(uri) {
    await addToQueue(name, uri);
    refresh();
  }

  async function handleMoveUpNext(id, to) {
    await moveUpNext(name, id, to).catch(() => {}); // entry got staged meanwhile -- refresh shows why
    refresh();
  }

  async function handleRemoveUpNext(id) {
    await removeUpNext(name, id).catch(() => {});
    refresh();
  }

  async function handlePlayNow(uri) {
    await playTrack(name, uri);
    refresh();
  }

  async function handlePlayPlaylist(playlistId) {
    await playPlaylist(name, playlistId);
    refresh();
  }

  async function handleQueuePlaylist(playlistId) {
    await queuePlaylist(name, playlistId);
    refresh();
  }

  const browseActions = {
    guest: Boolean(jamToken),
    onAdd: handleAdd,
    onPlayNow: handlePlayNow,
    onOpen: (view) => setViews((current) => [...current, view]),
  };

  async function handlePlayPause() {
    if (nowPlaying?.is_playing) await pausePlayback(name);
    else await playPlayback(name);
    refresh();
  }

  async function handleSettingsUpdated(data) {
    setShowSettings(false);
    if (data.name !== name) {
      setUnlocked({ ...unlocked, name: data.name, avatar_url: data.avatar_url, slot_token: data.slot_token });
      navigate(`/slots/${data.name}`, {
        state: { ...unlocked, name: data.name, avatar_url: data.avatar_url, slot_token: data.slot_token },
        replace: true,
      });
    } else {
      setUnlocked({ ...unlocked, avatar_url: data.avatar_url, slot_token: data.slot_token });
    }
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <SearchBar onQueryChange={onQueryChange} />
        {jamToken ? (
          <div className="settings-btn">
            <ProfileCircle name={name} avatarUrl={unlocked.avatar_url} size="sm" />
            <span>Jamming on {unlocked.display_name || name}</span>
          </div>
        ) : (
          <>
            <button className="ghost settings-btn" onClick={() => setShowSettings(true)}>
              <ProfileCircle name={name} avatarUrl={unlocked.avatar_url} size="sm" />
              <span>{name}</span>
            </button>
            <button
              type="button"
              className="ghost icon-btn"
              onClick={() => setShowAdminSettings(true)}
              aria-label="Admin settings"
              title="Admin settings"
            >
              ⚙
            </button>
          </>
        )}
      </header>

      <div className="dashboard-body">
        <aside className="dashboard-queue">
          <QueueList
            queue={queue}
            onMove={jamToken ? undefined : handleMoveUpNext}
            onRemove={jamToken ? undefined : handleRemoveUpNext}
          />
        </aside>

        <main className="dashboard-main">
          {webApiLinked ? (
            <>
              {playerError && playerError.status !== 400 && <p className="error">{playerError.message}</p>}
              {views.length > 0 ? (
                <DetailView
                  view={views[views.length - 1]}
                  api={browseApi}
                  actions={browseActions}
                  onBack={() => setViews((current) => current.slice(0, -1))}
                />
              ) : query ? (
                <SearchResults q={query} search={browseApi.search} actions={browseActions} />
              ) : (
                <>
                  <RecentlyPlayedRow
                    name={name}
                    getRecentlyPlayed={getRecentlyPlayed}
                    onAdd={handleAdd}
                    onPlayNow={handlePlayNow}
                    relinkPrompt={relinkPrompt("Recently played needs a fresh Spotify permission.")}
                  />
                  <PlaylistGrid
                    name={name}
                    getPlaylists={getPlaylists}
                    getPlaylist={getPlaylist}
                    getPlaylistTrackCount={getPlaylistTrackCount}
                    onAdd={handleAdd}
                    onPlayNow={handlePlayNow}
                    onPlayPlaylist={handlePlayPlaylist}
                    onQueuePlaylist={handleQueuePlaylist}
                    relinkPrompt={relinkPrompt("Playlists need a fresh Spotify permission.")}
                  />
                  <AlbumLibrary
                    name={name}
                    getAlbums={getLibraryAlbums}
                    getAlbumDetail={getAlbum}
                    onAdd={handleAdd}
                    onPlayNow={handlePlayNow}
                    relinkPrompt={relinkPrompt("Your library needs a fresh Spotify permission to show saved albums.")}
                  />
                </>
              )}
            </>
          ) : (
            <div className="card">
              <WebApiLinkFlow
                name={name}
                prompt="Spotify Web API isn't linked for this profile yet -- needed for now-playing, queue, search, playlists, and your library."
                onLinked={() => setUnlocked({ ...unlocked, web_api_linked: true })}
              />
            </div>
          )}
        </main>
      </div>

      <NowPlayingBar
        nowPlaying={nowPlaying}
        onPlayPause={handlePlayPause}
        onPrev={() => previousTrack(name).then(refresh)}
        onNext={() => nextTrack(name).then(refresh)}
        onSeek={(positionMs) => seekPlayback(name, positionMs).then(refresh)}
        onVolume={(percent) => setPlaybackVolume(name, percent)}
      />

      {showSettings && (
        <SettingsModal
          name={name}
          avatarUrl={unlocked.avatar_url}
          onClose={() => setShowSettings(false)}
          onUpdated={handleSettingsUpdated}
        />
      )}

      {showAdminSettings && <AdminSettingsModal onClose={() => setShowAdminSettings(false)} />}
    </div>
  );
}

import { useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  addToQueue,
  getAlbum,
  getLibraryAlbums,
  getPlayerState,
  getPlaylist,
  getPlaylists,
  getPlaylistTrackCount,
  getRecentlyPlayed,
  nextTrack,
  pausePlayback,
  playPlayback,
  playPlaylist,
  playTrack,
  previousTrack,
  queuePlaylist,
  searchTracks,
  seekPlayback,
  setPlaybackVolume,
} from "../api/client";
import AdminSettingsModal from "../components/AdminSettingsModal";
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

export default function SlotProfile() {
  const { name } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [unlocked, setUnlocked] = useState(location.state || null); // select() response
  const [showSettings, setShowSettings] = useState(false);
  const [showAdminSettings, setShowAdminSettings] = useState(false);

  const { nowPlaying, queue, error: playerError, refresh } = useSlotPlayer({
    name: unlocked ? name : undefined,
    fetchers: { getPlayerState },
  });

  if (!unlocked) {
    return (
      <PasswordPromptModal
        name={name}
        onClose={() => navigate("/slots")}
        onUnlocked={setUnlocked}
      />
    );
  }

  const webApiLinked = unlocked.web_api_linked;

  async function handleAdd(uri) {
    await addToQueue(name, uri);
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

  async function handlePlayPause() {
    if (nowPlaying?.is_playing) await pausePlayback(name);
    else await playPlayback(name);
    refresh();
  }

  async function handleSettingsUpdated(data) {
    setShowSettings(false);
    if (data.name !== name) {
      navigate(`/slots/${data.name}`, { state: { ...unlocked, name: data.name, avatar_url: data.avatar_url }, replace: true });
    } else {
      setUnlocked({ ...unlocked, avatar_url: data.avatar_url });
    }
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <SearchBar search={(q) => searchTracks(name, q)} onAdd={handleAdd} onPlayNow={handlePlayNow} />
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
      </header>

      <div className="dashboard-body">
        <aside className="dashboard-queue">
          <QueueList queue={queue} />
        </aside>

        <main className="dashboard-main">
          {webApiLinked ? (
            <>
              {playerError && playerError.status !== 400 && <p className="error">{playerError.message}</p>}
              <RecentlyPlayedRow
                name={name}
                getRecentlyPlayed={getRecentlyPlayed}
                onAdd={handleAdd}
                onPlayNow={handlePlayNow}
                relinkPrompt={
                  <WebApiLinkFlow
                    name={name}
                    prompt="Recently played needs a fresh Spotify permission."
                    onLinked={() => window.location.reload()}
                  />
                }
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
                relinkPrompt={
                  <WebApiLinkFlow
                    name={name}
                    prompt="Playlists need a fresh Spotify permission."
                    onLinked={() => window.location.reload()}
                  />
                }
              />
              <AlbumLibrary
                name={name}
                getAlbums={getLibraryAlbums}
                getAlbumDetail={getAlbum}
                onAdd={handleAdd}
                onPlayNow={handlePlayNow}
                relinkPrompt={
                  <WebApiLinkFlow
                    name={name}
                    prompt="Your library needs a fresh Spotify permission to show saved albums."
                    onLinked={() => window.location.reload()}
                  />
                }
              />
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

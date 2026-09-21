import { useState } from "react";
import { AlbumArt, formatDuration, trackArtists } from "./Player";

function IconPlay() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

function IconPause() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
      <path d="M6 5h4v14H6zm8 0h4v14h-4z" />
    </svg>
  );
}

function IconSkip({ flip }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" style={flip ? { transform: "scaleX(-1)" } : undefined}>
      <path d="M6 5v14l10-7zM18 5v14h-2V5z" />
    </svg>
  );
}

/** Spotify-style bottom transport bar. Playback state comes from the same
 * 3s poll everything else uses (useSlotPlayer) -- controls are optimistic
 * about the play/pause icon only, everything else just waits for the next
 * poll to catch up rather than maintaining a second source of truth. */
export default function NowPlayingBar({ nowPlaying, onPlayPause, onPrev, onNext, onSeek, onVolume }) {
  const [busy, setBusy] = useState(false);
  const [volume, setVolume] = useState(70);
  const item = nowPlaying?.item;
  const isPlaying = !!nowPlaying?.is_playing;
  const progressPct = item?.duration_ms ? Math.min(100, (nowPlaying.progress_ms / item.duration_ms) * 100) : 0;

  async function guarded(fn) {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
    } catch {
      // best-effort -- e.g. no active device; next poll reflects reality
    } finally {
      setBusy(false);
    }
  }

  function handleSeekClick(e) {
    if (!item?.duration_ms) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    guarded(() => onSeek(Math.round(ratio * item.duration_ms)));
  }

  function handleVolumeChange(e) {
    const value = Number(e.target.value);
    setVolume(value);
  }

  function handleVolumeCommit(e) {
    guarded(() => onVolume(Number(e.target.value)));
  }

  return (
    <div className="now-playing-bar">
      <div className="npb-track">
        <AlbumArt images={item?.album?.images} alt={item?.album?.name} size="sm" />
        <div className="track-info">
          <span className="track-name">{item?.name || "Nothing playing"}</span>
          <span className="track-artist">{item ? trackArtists(item) : " "}</span>
        </div>
      </div>

      <div className="npb-center">
        <div className="npb-controls">
          <button className="npb-icon" onClick={() => guarded(onPrev)} disabled={busy} aria-label="Previous">
            <IconSkip flip />
          </button>
          <button className="npb-icon npb-play" onClick={() => guarded(onPlayPause)} disabled={busy} aria-label={isPlaying ? "Pause" : "Play"}>
            {isPlaying ? <IconPause /> : <IconPlay />}
          </button>
          <button className="npb-icon" onClick={() => guarded(onNext)} disabled={busy} aria-label="Next">
            <IconSkip />
          </button>
        </div>
        <div className="npb-progress">
          <span className="npb-time">{formatDuration(nowPlaying?.progress_ms)}</span>
          <div className="progress-track npb-progress-track" onClick={handleSeekClick}>
            <div className="progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <span className="npb-time">{formatDuration(item?.duration_ms)}</span>
        </div>
      </div>

      <div className="npb-right">
        <input
          type="range"
          className="npb-volume"
          min="0"
          max="100"
          value={volume}
          onChange={handleVolumeChange}
          onMouseUp={handleVolumeCommit}
          onTouchEnd={handleVolumeCommit}
          aria-label="Volume"
        />
      </div>
    </div>
  );
}

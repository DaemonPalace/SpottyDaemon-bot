import { useParams } from "react-router-dom";
import { addToJamQueue, getJamPlayerState, searchJamTracks } from "../api/client";
import { NowPlayingHero, QueueList, TrackSearch } from "../components/Player";
import { useSlotPlayer } from "../hooks/useSlotPlayer";

export default function JamView() {
  const { jamToken } = useParams();
  const { nowPlaying, queue, error, refresh } = useSlotPlayer({
    jamToken,
    fetchers: { getPlayerState: getJamPlayerState },
  });

  async function handleAdd(uri) {
    await addToJamQueue(jamToken, uri);
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
      <NowPlayingHero nowPlaying={nowPlaying} />
      <QueueList queue={queue} />
      <TrackSearch search={(q) => searchJamTracks(jamToken, q)} onAdd={handleAdd} />
    </div>
  );
}

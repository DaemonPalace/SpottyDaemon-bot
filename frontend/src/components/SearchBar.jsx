import { useEffect, useRef, useState } from "react";
import { TrackThumb, trackArtists } from "./Player";

/** Top search bar with a results dropdown, styled after Spotify's header
 * search rather than TrackSearch's inline card (same search()/onAdd()
 * contract, just a different shell). */
export default function SearchBar({ search, onAdd, onPlayNow }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(null);
  const [addedUri, setAddedUri] = useState(null);
  const boxRef = useRef(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const data = await search(trimmed);
        if (!cancelled) {
          setResults(data.tracks || []);
          setError(null);
          setOpen(true);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, search]);

  useEffect(() => {
    function onClickOutside(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

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
    <div className="search-bar" ref={boxRef}>
      <input
        type="search"
        className="search-bar-input"
        placeholder="Search for a song..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
      />
      {open && (results.length > 0 || error) && (
        <div className="search-bar-results">
          {error && <p className="error">{error}</p>}
          {results.slice(0, 8).map((track) => (
            <div key={track.uri} className="track-row">
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
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

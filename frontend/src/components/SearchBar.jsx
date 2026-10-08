import { useEffect, useRef, useState } from "react";

/** Header search box. Reports the trimmed query 250 ms after typing stops;
 * results render in the main area (Browse.jsx's SearchResults). "/"
 * focuses it from anywhere on the page. */
export default function SearchBar({ onQueryChange }) {
  const [query, setQuery] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    const timer = setTimeout(() => onQueryChange(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query, onQueryChange]);

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key !== "/" || e.target.closest("input, textarea, select, [contenteditable]")) return;
      e.preventDefault();
      inputRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="search-bar">
      <input
        ref={inputRef}
        type="search"
        className="search-bar-input"
        placeholder="Search songs, artists, albums, playlists"
        aria-label="Search Spotify"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
    </div>
  );
}

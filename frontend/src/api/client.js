const JSON_HEADERS = { "Content-Type": "application/json" };

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? JSON_HEADERS : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const message = (data && data.message) || (typeof data === "string" ? data : `request failed (${res.status})`);
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }
  return data;
}

// Supervisor
export const getStatus = () => request("GET", "/api/supervisor/status");
export const submitSetup = (discord_token, admin_password) =>
  request("POST", "/api/supervisor/setup", { discord_token, admin_password });
export const login = (password) => request("POST", "/api/supervisor/login", { password });
export const logout = () => request("POST", "/api/supervisor/logout");
export const getLogs = () => request("GET", "/api/supervisor/logs");
export const startBot = () => request("POST", "/api/supervisor/bot/start");
export const restartBot = () => request("POST", "/api/supervisor/bot/restart");

// Slots (open to the dashboard; deleteSlot is the one route that needs the
// admin session cookie -- see supervisor/auth.py's _is_admin_gated)
export const listSlots = () => request("GET", "/api/slots");
export const startLink = (slot_name, password) => request("POST", "/api/slots/link/start", { slot_name, password });
export const finishLink = (user_id, pasted_url) => request("POST", "/api/slots/link/finish", { user_id, pasted_url });
export const selectSlot = (name, password) => request("POST", `/api/slots/${encodeURIComponent(name)}/select`, { password });
export const deleteSlot = (name) => request("DELETE", `/api/slots/${encodeURIComponent(name)}`);
export const startWebApiLink = (name) => request("POST", `/api/slots/${encodeURIComponent(name)}/web-api-link/start`, {});
export const finishWebApiLink = (name, user_id, pasted_url) =>
  request("POST", `/api/slots/${encodeURIComponent(name)}/web-api-link/finish`, { user_id, pasted_url });
export const updateSlotSettings = (name, { currentPassword, newName, newPassword, avatarUrl }) =>
  request("POST", `/api/slots/${encodeURIComponent(name)}/settings`, {
    current_password: currentPassword,
    new_name: newName,
    new_password: newPassword,
    avatar_url: avatarUrl,
  });

// Player (by slot name) -- now-playing + queue in one request, see
// bot/api.py's _player_state
export const getPlayerState = (name) => request("GET", `/api/slots/${encodeURIComponent(name)}/player-state`);
export const addToQueue = (name, uri) => request("POST", `/api/slots/${encodeURIComponent(name)}/queue`, { uri });
export const searchTracks = (name, q) =>
  request("GET", `/api/slots/${encodeURIComponent(name)}/search?q=${encodeURIComponent(q)}`);
export const playPlayback = (name) => request("POST", `/api/slots/${encodeURIComponent(name)}/player/play`, {});
export const pausePlayback = (name) => request("POST", `/api/slots/${encodeURIComponent(name)}/player/pause`, {});
export const nextTrack = (name) => request("POST", `/api/slots/${encodeURIComponent(name)}/player/next`, {});
export const previousTrack = (name) => request("POST", `/api/slots/${encodeURIComponent(name)}/player/previous`, {});
export const seekPlayback = (name, positionMs) =>
  request("POST", `/api/slots/${encodeURIComponent(name)}/player/seek`, { position_ms: positionMs });
export const setPlaybackVolume = (name, percent) =>
  request("POST", `/api/slots/${encodeURIComponent(name)}/player/volume`, { percent });

// Library (admin only -- needs the user-library-read scope, see
// bot/spotify_player_api.py's get_saved_albums)
export const getLibraryAlbums = (name) => request("GET", `/api/slots/${encodeURIComponent(name)}/library/albums`);
export const getAlbum = (name, albumId) =>
  request("GET", `/api/slots/${encodeURIComponent(name)}/albums/${encodeURIComponent(albumId)}`);
export const getRecentlyPlayed = (name) => request("GET", `/api/slots/${encodeURIComponent(name)}/recently-played`);
export const getPlaylists = (name) => request("GET", `/api/slots/${encodeURIComponent(name)}/playlists`);
export const getPlaylist = (name, playlistId) =>
  request("GET", `/api/slots/${encodeURIComponent(name)}/playlists/${encodeURIComponent(playlistId)}`);

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
export const regenerateJamToken = (name) =>
  request("POST", `/api/slots/${encodeURIComponent(name)}/jam-token/regenerate`, {});

// Player (by slot name) -- now-playing + queue in one request, see
// bot/api.py's _player_state
export const getPlayerState = (name) => request("GET", `/api/slots/${encodeURIComponent(name)}/player-state`);
export const addToQueue = (name, uri) => request("POST", `/api/slots/${encodeURIComponent(name)}/queue`, { uri });

// Jam mode (no admin session, token-scoped)
export const getJamPlayerState = (token) => request("GET", `/api/jam/${encodeURIComponent(token)}/player-state`);
export const addToJamQueue = (token, uri) => request("POST", `/api/jam/${encodeURIComponent(token)}/queue`, { uri });

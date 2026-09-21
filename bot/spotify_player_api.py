"""Thin wrappers around the Spotify Web API playback endpoints (now-playing,
queue read/add). Token lifecycle (OAuth, refresh) is bot/spotify_web_api.py's
job -- this module only knows how to call Spotify once it already has a
valid access token, keeping the two concerns separate.
"""

import logging

import aiohttp

log = logging.getLogger("spotify_player_api")

API_BASE = "https://api.spotify.com/v1"


async def get_now_playing(access_token: str) -> dict | None:
    """None if nothing is currently playing (Spotify returns 204 for that)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/me/player",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 204:
                return None
            if resp.status >= 300:
                raise RuntimeError(f"get_now_playing failed ({resp.status}): {await resp.text()}")
            return await resp.json()


async def get_queue(access_token: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/me/player/queue",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"get_queue failed ({resp.status}): {await resp.text()}")
            return await resp.json()


async def search_tracks(access_token: str, query: str) -> list[dict]:
    # No `limit` param -- as of writing, Spotify's /search rejects it
    # outright ("Invalid limit") regardless of value. Its default page size
    # (20) is plenty for an inline dropdown; the frontend trims further.
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/search",
            params={"q": query, "type": "track"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"search_tracks failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
            return body.get("tracks", {}).get("items", [])


async def get_saved_albums(access_token: str, limit: int = 50) -> list[dict] | None:
    """None means the token's scope predates user-library-read -- the slot
    needs to re-run Web API linking to pick up the newer scope."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/me/albums",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 403:
                return None
            if resp.status >= 300:
                raise RuntimeError(f"get_saved_albums failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
            return [item["album"] for item in body.get("items", [])]


async def get_album(access_token: str, album_id: str) -> dict:
    """Full album object, including its track list -- one request covers
    both the album header (name/art/artists) and what's on it."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/albums/{album_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"get_album failed ({resp.status}): {await resp.text()}")
            return await resp.json()


async def add_to_queue(access_token: str, track_uri: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/me/player/queue",
            params={"uri": track_uri},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"add_to_queue failed ({resp.status}): {await resp.text()}")


async def _player_put(
    access_token: str, path: str, params: dict | None = None, json_body: dict | None = None
) -> None:
    """204/202/200 all mean success for the transport-control endpoints
    below; a 404 means no active device (librespot isn't connected right
    now), surfaced as-is for the caller to turn into a user-facing message."""
    async with aiohttp.ClientSession() as session:
        async with session.put(
            f"{API_BASE}{path}",
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"{path} failed ({resp.status}): {await resp.text()}")


async def play(access_token: str) -> None:
    await _player_put(access_token, "/me/player/play")


async def play_uri(access_token: str, track_uri: str, device_id: str | None = None) -> None:
    """Plays a specific track immediately, replacing whatever's active --
    used by /play's "Play now" mode. Unlike play()'s bare resume, this needs
    a JSON body naming the track (Spotify's "start/resume playback" endpoint
    doubles as both). Pass device_id (see find_device_id) when the slot
    might not already be Spotify's "currently active device" -- without
    it, Spotify has to implicitly resolve one, which doesn't exist yet the
    very first time a slot plays anything (or any time it's gone fully
    idle) and makes that activation slower and audibly rougher than a
    warm transition between two already-active tracks."""
    params = {"device_id": device_id} if device_id else None
    await _player_put(access_token, "/me/player/play", params=params, json_body={"uris": [track_uri]})


async def get_devices(access_token: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/me/player/devices",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"get_devices failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
            return body.get("devices", [])


async def find_device_id(access_token: str, device_name: str) -> str | None:
    """Resolves librespot's Spotify Connect device_id by the name it
    registered with (SpotifySlot.name, e.g. "discord-bot-1", not the
    slot's friendly Discord-facing name) -- see play_uri for why passing
    this explicitly matters."""
    devices = await get_devices(access_token)
    for device in devices:
        if device.get("name") == device_name:
            return device.get("id")
    return None


async def pause(access_token: str) -> None:
    await _player_put(access_token, "/me/player/pause")


async def seek(access_token: str, position_ms: int) -> None:
    await _player_put(access_token, "/me/player/seek", {"position_ms": position_ms})


async def set_volume(access_token: str, volume_percent: int) -> None:
    await _player_put(access_token, "/me/player/volume", {"volume_percent": volume_percent})


async def next_track(access_token: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/me/player/next",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"next_track failed ({resp.status}): {await resp.text()}")


async def previous_track(access_token: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/me/player/previous",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"previous_track failed ({resp.status}): {await resp.text()}")


async def get_recently_played(access_token: str, limit: int = 20) -> list[dict] | None:
    """None means the token's scope predates user-read-recently-played --
    same re-link signal as get_saved_albums."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/me/player/recently-played",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 403:
                return None
            if resp.status >= 300:
                raise RuntimeError(f"get_recently_played failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
            return [item["track"] for item in body.get("items", [])]


async def get_playlists(access_token: str, limit: int = 50) -> list[dict] | None:
    """None means the token's scope predates playlist-read-private."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/me/playlists",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 403:
                return None
            if resp.status >= 300:
                raise RuntimeError(f"get_playlists failed ({resp.status}): {await resp.text()}")
            body = await resp.json()
            return body.get("items", [])


async def get_playlist(access_token: str, playlist_id: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/playlists/{playlist_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"get_playlist failed ({resp.status}): {await resp.text()}")
            return await resp.json()


async def get_current_user_profile(access_token: str) -> dict | None:
    """None means the token's scope predates user-read-private (images are
    only reliably populated with that scope)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 403:
                return None
            if resp.status >= 300:
                raise RuntimeError(f"get_current_user_profile failed ({resp.status}): {await resp.text()}")
            return await resp.json()

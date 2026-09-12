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

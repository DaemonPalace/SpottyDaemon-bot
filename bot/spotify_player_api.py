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

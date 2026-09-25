"""Bot-managed "Up next" list per slot, plus the heuristic that splits
Spotify's queue into songs someone queued from the Spotify app and the
rest of the playing playlist/album.

Spotify's Web API can read the queue and append to it -- no reorder, no
remove -- so every song added from the dashboard or Discord lands in Up
next first, where it can be reordered or removed. The bot keeps exactly
Up-next song "staged" in Spotify's real queue at a time, handed over only
when nothing queued from the app is ahead of it and the current song is
about to end (HANDOFF_MS) -- until then it stays reorderable here.
Anything queued from the app plays ahead of the rest of Up next.

Spotify's queue response doesn't say which entries were queued and which
are just the upcoming context, so QueueTracker infers it by diffing
successive snapshots -- see observe().
"""

import asyncio
import json
import logging
import os
import secrets
import time
from collections import Counter

import spotify_player_api
from config import LIBRESPOT_CACHE_DIR
from slot_store import SlotStore
from spotify_web_api import WebApiLinkManager

log = logging.getLogger("up_next")

UP_NEXT_PATH = os.environ.get("UP_NEXT_PATH", os.path.join(LIBRESPOT_CACHE_DIR, "up_next.json"))
POLL_SECONDS = 3
# Hand the next Up-next song to Spotify once the current song has this
# little left -- several polls' worth, so a slow poll can't miss it.
HANDOFF_MS = 15_000
# A pushed song that never shows up in Spotify's queue (the add silently
# didn't stick) stops being waited on after this many snapshots.
PENDING_MAX_OBSERVATIONS = 3
ANCHOR_SLACK = 5

APP = "app"
BOT = "bot"


class QueueTracker:
    """Pure snapshot-diffing state for one slot -- no I/O, see test_up_next.py.

    `front` is the queued block at the head of Spotify's queue (queued
    songs always play before the context resumes), as (uri, source)
    pairs. A song counts as newly queued when it appears right after the
    known front AND the snapshot's multiset gained a copy of it -- a
    Counter, not a set, so re-queueing a song already coming up in the
    playlist still registers (its count goes 1 -> 2), while reordering the
    playlist in the app changes no counts and is never mistaken for
    queueing. Songs entering at the tail as the window slides forward
    aren't after the front, so they're ignored too.

    A context switch (new playlist/album, shuffle toggle) is its own
    signal: head songs that survive it are queued, which also recovers
    queued songs the tracker had missed (e.g. queued before a restart).

    Known blind spots: a song queued in the same gap as a context switch,
    a jump that isn't a plain advance to the next song, and the very first
    snapshot have no trustworthy baseline -- anything queued right then
    reads as playlist. A song that happens to open both the old and the
    new playlist can be misread as queued on a switch."""

    def __init__(self, label: str = ""):
        self.label = label  # TEMP-DIAG: slot name in the diagnostic logs
        self.context_key = None
        self.current_uri = None
        self.window: list[str] = []
        self.front: list[tuple[str, str]] = []
        self.pending_bot: dict[str, int] = {}  # uri -> snapshots waited

    def expect_bot(self, uri: str) -> None:
        """The bot just pushed `uri` into Spotify's queue -- when it
        appears, it's ours, not the app's."""
        self.pending_bot[uri] = 0

    def bot_waiting(self) -> bool:
        return bool(self.pending_bot) or any(source == BOT for _, source in self.front)

    def observe(self, context_key, current_uri: str | None, window: list[str]) -> None:
        old_window, old_front = self.window, self.front
        # Switching playlist/album (or toggling shuffle) replaces the whole
        # upcoming context but leaves the queued block at the head -- so
        # a head song that was also in the previous snapshot survived the
        # switch, which only queued songs do.
        context_switched = context_key != self.context_key and self.current_uri is not None
        survivors = Counter(old_window) if context_switched else Counter()
        trusted = context_key == self.context_key and self.current_uri is not None
        if current_uri != self.current_uri:
            if old_window and old_window[0] == current_uri:
                # Plain advance: the head of the old queue is now playing.
                old_window = old_window[1:]
                if old_front and old_front[0][0] == current_uri:
                    log.info("[UPNEXT-DIAG] %s: queued %s started playing", self.label, current_uri)  # TEMP-DIAG
                    old_front = old_front[1:]
            else:
                trusted = False
                log.info("[UPNEXT-DIAG] %s: jump %s -> %s, no baseline this snapshot", self.label, self.current_uri, current_uri)  # TEMP-DIAG
        if context_switched:
            log.info("[UPNEXT-DIAG] %s: context switch %s -> %s", self.label, self.context_key, context_key)  # TEMP-DIAG

        # Anchors: songs already known to be queued (matched by content, not
        # position) and the bot's own pushes, looked for near the head.
        # Spotify always plays queued songs before the context, so anything
        # sitting ABOVE an anchor is queued too -- a newly queued song, or a
        # playlist song someone dragged up into the queue in the app.
        # ponytail: anchors are only searched ANCHOR_SLACK past the old
        # queued block; drag more than that many songs up at once and the
        # lower queued ones read as playlist until the next change.
        known = Counter(uri for uri, _ in old_front)
        sources: dict[str, list[str]] = {}
        for uri, source in old_front:
            sources.setdefault(uri, []).append(source)
        added = Counter(window) - Counter(old_window)
        matched: dict[int, str] = {}
        last_anchor = -1
        for i in range(min(len(window), len(old_front) + ANCHOR_SLACK)):
            uri = window[i]
            if known[uri] > 0:
                known[uri] -= 1
                matched[i] = sources[uri].pop(0)
            elif uri in self.pending_bot:  # the bot knows it queued it -- no baseline needed
                del self.pending_bot[uri]
                matched[i] = BOT
                log.info("[UPNEXT-DIAG] %s: %s handed-over Up next song showed up in Spotify's queue", self.label, uri)  # TEMP-DIAG
            else:
                continue
            last_anchor = i
        front = []
        for i in range(last_anchor + 1):
            uri = window[i]
            survivors[uri] -= 1
            if i not in matched:
                # Only an unmatched song can be a new copy -- an anchor was
                # already in the last snapshot, so it mustn't use up the
                # new-copy count a second queueing of the same song needs.
                added[uri] -= 1
                log.info("[UPNEXT-DIAG] %s: %s -> Queued (sits above a queued song, pos %d)", self.label, uri, i)  # TEMP-DIAG
            front.append((uri, matched.get(i, APP)))

        # Below the last anchor: a contiguous run of newly queued songs.
        i = last_anchor + 1
        while i < len(window):
            uri = window[i]
            if uri in self.pending_bot:
                del self.pending_bot[uri]
                source = BOT
                log.info("[UPNEXT-DIAG] %s: %s handed-over Up next song showed up in Spotify's queue", self.label, uri)  # TEMP-DIAG
            elif trusted and added[uri] > 0:
                source = APP
                log.info("[UPNEXT-DIAG] %s: %s -> Queued (new copy at head, pos %d)", self.label, uri, i)  # TEMP-DIAG
            elif survivors[uri] > 0:
                source = APP
                log.info("[UPNEXT-DIAG] %s: %s -> Queued (survived context switch, pos %d)", self.label, uri, i)  # TEMP-DIAG
            else:
                break
            added[uri] -= 1
            survivors[uri] -= 1
            front.append((uri, source))
            i += 1

        for uri, count in known.items():  # TEMP-DIAG: whatever wasn't re-matched lost its Queued status
            for _ in range(count):
                where = "back to Playlist" if uri in window else "left the queue"
                log.info(
                    "[UPNEXT-DIAG] %s: %s declassified -> %s (head now %s, current %s, trusted=%s)",
                    self.label, uri, where, window[: i + 3], current_uri, trusted,
                )

        for uri in list(self.pending_bot):
            self.pending_bot[uri] += 1
            if self.pending_bot[uri] >= PENDING_MAX_OBSERVATIONS:
                del self.pending_bot[uri]

        if window[:8] != self.window[:8] or front != self.front:  # TEMP-DIAG: every change at the head
            log.info(
                "[UPNEXT-DIAG] %s: snapshot current=%s trusted=%s head=%s queued=%s",
                self.label, current_uri, trusted, window[:8], [f"{u}({src})" for u, src in front],
            )
        self.context_key, self.current_uri, self.window, self.front = context_key, current_uri, window, front


def _compact_track(track: dict) -> dict:
    """Just what the queue panel renders -- keeps up_next.json small."""
    return {
        "uri": track["uri"],
        "name": track.get("name"),
        "artists": [{"name": a.get("name")} for a in track.get("artists", [])],
        "album": {"name": track.get("album", {}).get("name"), "images": track.get("album", {}).get("images", [])},
        "duration_ms": track.get("duration_ms"),
    }


class UpNextManager:
    def __init__(self, slot_store: SlotStore, web_api_link_manager: WebApiLinkManager, path: str = UP_NEXT_PATH):
        self.slot_store = slot_store
        self.web_api_link_manager = web_api_link_manager
        self.path = path
        # slot index -> {"claimed_at", "entries": [{"id", "track"}], "staged": entry | None}
        self._state: dict[int, dict] = self._load()
        self._trackers: dict[int, QueueTracker] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_refresh: dict[int, float] = {}

    def _load(self) -> dict[int, dict]:
        if not os.path.exists(self.path):
            return {}
        with open(self.path) as f:
            return {int(k): v for k, v in json.load(f).items()}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump({str(k): v for k, v in self._state.items()}, f)
        os.replace(tmp_path, self.path)

    def _slot_state(self, slot_index: int) -> dict:
        """Keyed to the slot's current claim, so a re-claimed slot never
        inherits the previous owner's Up next."""
        meta = self.slot_store.get_by_index(slot_index)
        claimed_at = meta.claimed_at if meta is not None else None
        state = self._state.get(slot_index)
        if state is None or state.get("claimed_at") != claimed_at:
            state = {"claimed_at": claimed_at, "entries": [], "staged": None}
            self._state[slot_index] = state
        return state

    def _lock(self, slot_index: int) -> asyncio.Lock:
        return self._locks.setdefault(slot_index, asyncio.Lock())

    async def add(self, slot_index: int, tracks: list[dict]) -> None:
        async with self._lock(slot_index):
            state = self._slot_state(slot_index)
            state["entries"].extend({"id": secrets.token_hex(6), "track": _compact_track(t)} for t in tracks)
            self._save()
        await self.refresh(slot_index)  # hand one over right away if nothing's staged

    async def move(self, slot_index: int, entry_id: str, to_index: int) -> bool:
        async with self._lock(slot_index):
            entries = self._slot_state(slot_index)["entries"]
            index = next((i for i, e in enumerate(entries) if e["id"] == entry_id), None)
            if index is None:
                return False
            entry = entries.pop(index)
            entries.insert(max(0, min(to_index, len(entries))), entry)
            self._save()
            return True

    async def remove(self, slot_index: int, entry_id: str) -> bool:
        async with self._lock(slot_index):
            entries = self._slot_state(slot_index)["entries"]
            kept = [e for e in entries if e["id"] != entry_id]
            if len(kept) == len(entries):
                return False
            entries[:] = kept
            self._save()
            return True

    async def refresh(self, slot_index: int) -> dict | None:
        """One snapshot: read Spotify's player + queue, update the tracker,
        hand the next Up-next song over if none is waiting, and return the
        three-section view. None if the slot's Web API isn't linked."""
        token = await self.web_api_link_manager.get_access_token(slot_index)
        if token is None:
            return None
        async with self._lock(slot_index):
            now_playing, queue = await asyncio.gather(
                spotify_player_api.get_now_playing(token),
                spotify_player_api.get_queue(token),
            )
            state = self._slot_state(slot_index)
            tracker = self._trackers.get(slot_index)
            if tracker is None:
                tracker = self._trackers[slot_index] = QueueTracker(f"slot{slot_index}")
                if state["staged"] is not None:  # staged before a restart -- still ours
                    tracker.expect_bot(state["staged"]["track"]["uri"])
            queued = [t for t in (queue or {}).get("queue", []) if t and t.get("uri")]
            context_key = (
                ((now_playing or {}).get("context") or {}).get("uri"),
                (now_playing or {}).get("shuffle_state"),
            )
            current = ((now_playing or {}).get("item") or {}).get("uri")
            tracker.observe(context_key, current, [t["uri"] for t in queued])

            if state["staged"] is not None and not tracker.bot_waiting():
                log.info("[UPNEXT-DIAG] slot%s: handed-over %s is playing / gone", slot_index, state["staged"]["track"]["uri"])  # TEMP-DIAG
                state["staged"] = None  # it's playing now (or was removed in the app)
                self._save()
            item = (now_playing or {}).get("item") or {}
            remaining_ms = (item.get("duration_ms") or 0) - ((now_playing or {}).get("progress_ms") or 0)
            # Hand over only at the last moment -- nothing queued from the app
            # still ahead of it and the current song about to end -- so the
            # song stays reorderable/removable in Up next as long as possible.
            if (
                state["staged"] is None
                and state["entries"]
                and current is not None
                and not tracker.front
                and remaining_ms <= HANDOFF_MS
            ):
                entry = state["entries"][0]
                log.info("[UPNEXT-DIAG] slot%s: handing %s to Spotify (%d ms left)", slot_index, entry["track"]["uri"], remaining_ms)  # TEMP-DIAG
                try:
                    await spotify_player_api.add_to_queue(token, entry["track"]["uri"])
                except Exception:
                    log.warning("slot %s: couldn't hand Up next song to Spotify", slot_index, exc_info=True)
                else:
                    state["entries"].pop(0)
                    state["staged"] = entry
                    tracker.expect_bot(entry["track"]["uri"])
                    self._save()

            self._last_refresh[slot_index] = time.monotonic()
            front_len = len(tracker.front)
            return {
                "now_playing": now_playing,
                "app_queue": [queued[i] for i, (_, source) in enumerate(tracker.front) if source == APP],
                "up_next": ([{**state["staged"], "staged": True}] if state["staged"] else []) + state["entries"],
                "playlist": queued[front_len:],
            }

    def start(self) -> None:
        asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        """Keeps feeding Up next with no dashboard open. Only slots that
        have something to hand over are polled, and a slot the dashboard
        just refreshed is skipped."""
        while True:
            await asyncio.sleep(POLL_SECONDS)
            for slot_index in list(self._state):
                state = self._slot_state(slot_index)
                if not state["entries"] and state["staged"] is None:
                    continue
                if time.monotonic() - self._last_refresh.get(slot_index, 0) < POLL_SECONDS - 1:
                    continue
                try:
                    await self.refresh(slot_index)
                except Exception:
                    log.warning("slot %s: Up next poll failed", slot_index, exc_info=True)

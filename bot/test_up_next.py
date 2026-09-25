"""Self-check for up_next.QueueTracker's queued-vs-playlist heuristic. Run
from the repo root: `python bot/test_up_next.py` (no Discord or Spotify
needed -- the tracker is pure snapshot diffing)."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("LIBRESPOT_CACHE_DIR", tempfile.mkdtemp())

from up_next import APP, BOT, QueueTracker  # noqa: E402

CTX = ("spotify:playlist:p", False)


def tracker_at(current, window):
    t = QueueTracker()
    t.observe(CTX, current, window)  # first snapshot: baseline only
    assert t.front == []
    return t


# Queued from the app: appears at the head, count went up.
t = tracker_at("c0", ["p1", "p2", "p3"])
t.observe(CTX, "c0", ["x", "p1", "p2"])
assert t.front == [("x", APP)], t.front

# A second queued song lands after the first.
t.observe(CTX, "c0", ["x", "y", "p1"])
assert t.front == [("x", APP), ("y", APP)], t.front

# Advancing plays the first queued song; the window slides, a new tail
# song enters -- not mistaken for queueing.
t.observe(CTX, "x", ["y", "p1", "p2"])
assert t.front == [("y", APP)], t.front

# Reordering the playlist in the app changes no counts -> nothing queued.
t = tracker_at("c0", ["p1", "p2", "p3"])
t.observe(CTX, "c0", ["p3", "p1", "p2"])
assert t.front == [], t.front

# Re-queueing a song already coming up in the playlist is still detected.
t = tracker_at("c0", ["p1", "p2", "p3"])
t.observe(CTX, "c0", ["p2", "p1", "p2"])
assert t.front == [("p2", APP)], t.front

# The bot's own push is tagged as the bot's, even on an untrusted snapshot.
t = QueueTracker()
t.expect_bot("u")
t.observe(CTX, "c0", ["u", "p1"])
assert t.front == [("u", BOT)] and t.bot_waiting(), t.front
t.observe(CTX, "u", ["p1", "p2"])  # it started playing
assert t.front == [] and not t.bot_waiting(), t.front

# Removed from the queue in the app -> dropped from the front.
t = tracker_at("c0", ["p1", "p2"])
t.observe(CTX, "c0", ["x", "p1", "p2"])
t.observe(CTX, "c0", ["p1", "p2"])
assert t.front == [], t.front

# Switching playlist: no baseline, so nothing new is classified, but songs
# already known to be queued stay queued.
t = tracker_at("c0", ["p1", "p2"])
t.observe(CTX, "c0", ["x", "p1"])
t.observe(("spotify:album:a", False), "c0", ["x", "a1", "a2"])
assert t.front == [("x", APP)], t.front

print("up next checks passed")

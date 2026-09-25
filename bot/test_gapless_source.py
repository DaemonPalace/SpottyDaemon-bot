"""Self-check for commands._GaplessSource: an idle pipe yields silence, not
end of stream, and a real EOF still ends it. Run from the repo root:
`python bot/test_gapless_source.py` (no Discord, Spotify or ffmpeg needed)."""

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("LIBRESPOT_CACHE_DIR", tempfile.mkdtemp())

from commands import _GaplessSource  # noqa: E402

SILENCE = _GaplessSource._SILENCE
FRAME = b"\x01" * len(SILENCE)


class FakeInner:
    """Hands out one frame, then blocks (Spotify paused) until released,
    then EOFs."""

    def __init__(self):
        self.frames = [FRAME]
        self.release = threading.Event()
        self.cleaned = False

    def read(self):
        if self.frames:
            return self.frames.pop()
        self.release.wait()
        return b""

    def cleanup(self):
        self.cleaned = True
        self.release.set()


def read_until(source, want, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = source.read()
        if got == want:
            return
        time.sleep(0.01)
    raise AssertionError(f"never read {want[:1]!r}")


inner = FakeInner()
src = _GaplessSource(inner)
read_until(src, FRAME)
# Paused: inner.read() blocks, but read() must still return silence at once.
for _ in range(5):
    assert src.read() == SILENCE
# Real EOF ends the stream.
inner.release.set()
read_until(src, b"")

# cleanup() stops the pump even while it's blocked in inner.read().
inner = FakeInner()
src = _GaplessSource(inner)
read_until(src, FRAME)
src.cleanup()
assert inner.cleaned

print("ok")

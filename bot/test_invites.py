"""Self-check for bot/slot_store.py's invite lifecycle. Run from the repo
root: `DISCORD_TOKEN=x python bot/test_invites.py`."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("LIBRESPOT_CACHE_DIR", tempfile.mkdtemp())

from slot_store import (  # noqa: E402
    STATE_APPROVED,
    STATE_FREE,
    STATE_INVITED,
    STATE_LINKING,
    STATE_PENDING,
    SlotStore,
)


async def main():
    path = os.path.join(tempfile.mkdtemp(), "slots.json")
    store = SlotStore(max_slots=2, path=path)

    a = await store.create_invite()
    b = await store.create_invite()
    assert a.state == b.state == STATE_INVITED and a.invite_token != b.invite_token
    assert await store.create_invite() is None  # full
    assert store.get_by_invite(a.invite_token) is a and store.get_by_invite("") is None

    token = a.invite_token
    assert await store.register(a.index, "Bad Name", "pw", "Al", "a@x") is not None
    assert await store.register(a.index, "alice", "pw", "Alice A", "a@x") is None
    assert a.state == STATE_PENDING and store.get_by_invite(token) is None  # link is single-use
    assert await store.register(a.index, "alice", "pw", "Alice A", "a@x") == "This invite was already used."
    assert await store.register(b.index, "alice", "pw", "B", "b@x") == "That name is already taken."
    assert store.verify_password("alice", "pw")

    # Deny frees the slot.
    await store.reset(b.index)
    assert store.get_by_index(b.index).state == STATE_FREE

    # Approved -> linking survives a restart as approved (owner retries), with sign-up info intact.
    await store.set_state(a.index, STATE_LINKING)
    reloaded = SlotStore(max_slots=2, path=path).get_by_name("alice")
    assert reloaded.state == STATE_APPROVED and reloaded.spotify_email == "a@x" and reloaded.full_name == "Alice A"

    await store.claim(a.index, "web:1")
    assert store.claimed_indexes() == {a.index} and store.verify_password("alice", "pw")
    print("invite checks passed")


asyncio.run(main())

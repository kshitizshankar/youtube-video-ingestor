"""Regression test: list_active() must NOT silently evict errored ingest
records on a TTL. The user's failure history is the only thing they have
to debug a crashed ingest, and auto-eviction made errors disappear after
60 seconds — leaving a Detail page stuck on 'Waiting for transcript'
with no record of what went wrong.

Clean completions still TTL out (the registry is a "what's running now"
view; finished happy-path rows belong in the transcripts list)."""
from __future__ import annotations

import time

import server.state as state_mod


def _reset_state(tmp_path):
    state_mod._INGESTS.clear()
    state_mod._PERSIST_PATH = tmp_path / "_ingests.json"


def test_errored_ingests_are_kept_indefinitely(tmp_path) -> None:
    _reset_state(tmp_path)
    state_mod.begin("aaaaaaaaaaa", url="https://x")
    state_mod.update("aaaaaaaaaaa", phase="downloading", done=True, error="boom")

    # Push last_event_at deep past _DONE_TTL_SEC.
    rec = state_mod._INGESTS["aaaaaaaaaaa"]
    rec.last_event_at = time.time() - (state_mod._DONE_TTL_SEC + 600)

    active = state_mod.list_active()
    ids = [r["id"] for r in active]
    assert "aaaaaaaaaaa" in ids, (
        "errored ingest was evicted -- the user has no way to see the failure"
    )
    rec2 = state_mod._INGESTS["aaaaaaaaaaa"]
    assert rec2.error == "boom"


def test_clean_completions_are_evicted_after_ttl(tmp_path) -> None:
    _reset_state(tmp_path)
    state_mod.begin("bbbbbbbbbbb", url="https://x")
    state_mod.update("bbbbbbbbbbb", phase="done", done=True)

    rec = state_mod._INGESTS["bbbbbbbbbbb"]
    rec.last_event_at = time.time() - (state_mod._DONE_TTL_SEC + 1)

    state_mod.list_active()  # the eviction pass happens inside list_active
    assert "bbbbbbbbbbb" not in state_mod._INGESTS, (
        "clean completion should have been evicted from the active strip"
    )


def test_active_inflight_jobs_are_kept(tmp_path) -> None:
    _reset_state(tmp_path)
    state_mod.begin("cccccccccccc"[:11], url="https://x")
    state_mod.update("cccccccccccc"[:11], phase="downloading")

    # Even with stale last_event_at, non-done jobs aren't evicted -- only
    # annotated as stalled (and only if they're past the queued/starting phase).
    rec = state_mod._INGESTS["cccccccccccc"[:11]]
    rec.last_event_at = time.time() - (state_mod._STALL_SEC + 60)

    active = state_mod.list_active()
    ids = [r["id"] for r in active]
    assert "cccccccccccc"[:11] in ids

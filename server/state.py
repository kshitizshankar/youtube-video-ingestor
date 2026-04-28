"""In-memory state of active transcription jobs, so the Library UI can
show progress without having to subscribe to the SSE stream that only the
originating browser tab owns.

All access is guarded by a single lock. One uvicorn worker process only
(we're not running with --workers > 1), so this is safe without needing
a cross-process store.

Optionally persisted to a JSON file on every mutation so that server
restarts surface orphaned jobs (the worker threads don't survive a
restart — users can see them as errored and hit Retry rather than
re-pasting the URL into the modal).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock


log = logging.getLogger("server.state")


@dataclass
class IngestStatus:
    id: str
    url: str | None = None
    title: str | None = None
    duration_sec: float | None = None
    phase: str = "starting"
    started_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)
    segments: int = 0
    last_segment_end: float = 0.0
    done: bool = False
    error: str | None = None
    # Cooperative cancel: request_cancel() flips this; the worker checks it
    # at each phase boundary and aborts cleanly. Python threads can't be
    # force-killed, so mid-transcribe cancel only takes effect when the
    # current phase finishes (model-load / download / transcribe / diarize).
    cancel_requested: bool = False


_INGESTS: dict[str, IngestStatus] = {}
_LOCK = Lock()
_DONE_TTL_SEC = 60.0
# Non-done jobs with no event this long are marked stalled in list_active().
_STALL_SEC = 180.0
_PERSIST_PATH: Path | None = None


def configure_persistence(path: Path) -> None:
    """Call once at startup. Loads any existing entries and marks non-done
    ones as orphaned (their worker threads died with the previous process)."""
    global _PERSIST_PATH
    _PERSIST_PATH = path
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("failed to load %s: %s", path, e)
        return
    with _LOCK:
        for row in data:
            try:
                st = IngestStatus(**row)
            except TypeError:
                continue  # old/unknown schema — skip
            if not st.done:
                st.done = True
                st.error = st.error or "orphaned (server restart)"
            _INGESTS[st.id] = st
    log.info("loaded %d ingest records from %s", len(_INGESTS), path)


def _persist_locked() -> None:
    """Call while holding _LOCK. Best-effort; errors are logged, not raised."""
    if _PERSIST_PATH is None:
        return
    try:
        rows = [asdict(st) for st in _INGESTS.values()]
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PERSIST_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PERSIST_PATH)
    except Exception as e:
        log.warning("failed to persist ingests: %s", e)


def begin(video_id: str, url: str | None = None) -> IngestStatus:
    with _LOCK:
        st = IngestStatus(id=video_id, url=url)
        _INGESTS[video_id] = st
        _persist_locked()
        return st


def update(video_id: str, **kwargs) -> None:
    with _LOCK:
        st = _INGESTS.get(video_id)
        if st is None:
            return
        for k, v in kwargs.items():
            setattr(st, k, v)
        st.last_event_at = time.time()
        _persist_locked()


def segment_received(video_id: str, segment_end: float) -> None:
    with _LOCK:
        st = _INGESTS.get(video_id)
        if st is None:
            return
        st.segments += 1
        st.last_segment_end = segment_end
        st.last_event_at = time.time()
        # Skip persist on every segment — batched mode bursts hundreds per second.


def finish(video_id: str, error: str | None = None) -> None:
    with _LOCK:
        st = _INGESTS.get(video_id)
        if st:
            st.done = True
            st.error = error
            st.last_event_at = time.time()
            _persist_locked()


def rekey(old_id: str, new_id: str) -> None:
    """yt-dlp can resolve a different canonical id than we guessed from the
    raw URL (e.g. user pasted a playlist-context URL). Move the entry."""
    if old_id == new_id:
        return
    with _LOCK:
        st = _INGESTS.get(old_id)
        if st is None:
            return
        st.id = new_id
        _INGESTS[new_id] = st
        _INGESTS.pop(old_id, None)
        _persist_locked()


def get(video_id: str) -> IngestStatus | None:
    with _LOCK:
        return _INGESTS.get(video_id)


def drop(video_id: str) -> bool:
    """Force-remove an entry. Returns True if something was removed."""
    with _LOCK:
        if video_id in _INGESTS:
            del _INGESTS[video_id]
            _persist_locked()
            return True
        return False


def has_active(video_id: str) -> bool:
    """True if there's a non-done record for this id. Used by stream_transcription
    to refuse duplicate concurrent ingests."""
    with _LOCK:
        st = _INGESTS.get(video_id)
        return bool(st and not st.done)


def request_cancel(video_id: str) -> bool:
    """Signal the worker to abort at the next phase boundary. Returns False
    if there's no record or the job is already done."""
    with _LOCK:
        st = _INGESTS.get(video_id)
        if st is None or st.done:
            return False
        st.cancel_requested = True
        st.last_event_at = time.time()
        _persist_locked()
        return True


def is_cancel_requested(video_id: str) -> bool:
    with _LOCK:
        st = _INGESTS.get(video_id)
        return bool(st and st.cancel_requested)


def list_active() -> list[dict]:
    """Active + recently-done (kept for a minute so the UI can show the
    completion briefly). Any non-done job whose last_event_at is older than
    _STALL_SEC is annotated as stalled."""
    now = time.time()
    with _LOCK:
        mutated = False
        for k in list(_INGESTS.keys()):
            st = _INGESTS[k]
            # Clean completions disappear from the active strip after the
            # brief "just finished" window — by then the row is in the
            # transcripts list and the user has navigated to the video.
            # Errored / cancelled rows STAY until explicitly dropped via
            # DELETE /api/ingests/{id}, so the user can see what happened
            # even if they weren't watching during the failure window.
            if st.done and not st.error and (now - st.last_event_at) > _DONE_TTL_SEC:
                del _INGESTS[k]
                mutated = True
                continue
            if not st.done and (now - st.last_event_at) > _STALL_SEC:
                # Queued + awaiting-GPU jobs aren't stalled -- they're
                # waiting for a worker slot (download / transcribe pool).
                # Only mark genuine stalls (a phase that's actively doing
                # work with no progress for a long time).
                if (st.phase or "") in ("queued", "starting", "awaiting_gpu"):
                    continue
                if "stalled" not in (st.phase or ""):
                    st.phase = f"stalled ({st.phase or 'working'})"
                    mutated = True
                if not st.error:
                    st.error = f"no events for {int(now - st.last_event_at)}s"
                    mutated = True
        if mutated:
            _persist_locked()
        return [asdict(st) for st in _INGESTS.values()]

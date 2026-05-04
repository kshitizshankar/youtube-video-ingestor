"""Move protocol + crash recovery.

Tests exercise:
  * happy path: video physically moves between project folders
  * refusals: same project, missing project, missing video, in-flight ingest
  * crash recovery, every case from the spec (A through E)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server import state as state_mod
from server.db import open_connection, run_migrations
from server.layout import forget_video_path
from server.moves import MoveError, move_video, recover_in_flight_moves
from server.projects import ensure_inbox


@pytest.fixture
def db(tmp_path: Path):
    out_dir = tmp_path
    conn = open_connection(out_dir / "app.db")
    run_migrations(conn)
    ensure_inbox(out_dir, conn=conn)
    # The registry can carry state from a previous test in the same
    # process; clear it so has_active() returns False as expected.
    with state_mod._LOCK:  # type: ignore[attr-defined]
        state_mod._INGESTS.clear()  # type: ignore[attr-defined]
    forget_video_path(None)
    yield out_dir, conn
    conn.close()


def _make_project(conn, project_id: str) -> None:
    conn.execute(
        "INSERT INTO projects(id, name, created_at, updated_at) VALUES(?, ?, ?, ?)",
        (project_id, project_id.replace("-", " ").title(), "2026-05-04", "2026-05-04"),
    )


def _make_video_at(out_dir: Path, conn, video_id: str, project_id: str, *, with_files: bool = True) -> Path:
    folder = out_dir / "projects" / project_id / video_id
    folder.mkdir(parents=True, exist_ok=True)
    if with_files:
        (folder / "transcript.json").write_text('{"id": "%s"}' % video_id, encoding="utf-8")
        (folder / "audio.mp3").write_bytes(b"\x00" * 1024)
    conn.execute(
        "INSERT INTO videos(id, url, title, created_at, updated_at, project_id, path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (video_id, f"https://x/{video_id}", video_id, "2026-05-04", "2026-05-04",
         project_id, str(folder)),
    )
    return folder


def test_happy_path_inbox_to_project(db) -> None:
    out_dir, conn = db
    _make_project(conn, "show-a")
    src = _make_video_at(out_dir, conn, "vid_aaa", "inbox")

    result = move_video(out_dir, "vid_aaa", "show-a")
    assert result["from_project"] == "inbox"
    assert result["to_project"] == "show-a"

    # Source folder is gone, destination has the files.
    assert not src.exists()
    dst = out_dir / "projects" / "show-a" / "vid_aaa"
    assert (dst / "transcript.json").exists()
    assert (dst / "audio.mp3").exists()

    # DB reflects the new project + path.
    row = conn.execute(
        "SELECT project_id, path, move_state FROM videos WHERE id=?",
        ("vid_aaa",),
    ).fetchone()
    assert row["project_id"] == "show-a"
    assert row["path"] == str(dst)
    assert row["move_state"] is None

    # move_log audited the trip, status='committed'.
    log_row = conn.execute(
        "SELECT status, from_project, to_project FROM move_log WHERE video_id=?",
        ("vid_aaa",),
    ).fetchone()
    assert log_row["status"] == "committed"
    assert log_row["from_project"] == "inbox"
    assert log_row["to_project"] == "show-a"

    # events_since_build bumped on both projects.
    inbox_evt = conn.execute(
        "SELECT events_since_build FROM projects WHERE id='inbox'"
    ).fetchone()["events_since_build"]
    show_evt = conn.execute(
        "SELECT events_since_build FROM projects WHERE id='show-a'"
    ).fetchone()["events_since_build"]
    assert inbox_evt == 1
    assert show_evt == 1


def test_refuses_same_project(db) -> None:
    out_dir, conn = db
    _make_video_at(out_dir, conn, "vid_same", "inbox")
    with pytest.raises(MoveError, match="already in project"):
        move_video(out_dir, "vid_same", "inbox")


def test_refuses_unknown_project(db) -> None:
    out_dir, conn = db
    _make_video_at(out_dir, conn, "vid_orphan", "inbox")
    with pytest.raises(MoveError, match="unknown project"):
        move_video(out_dir, "vid_orphan", "no-such-project")


def test_refuses_unknown_video(db) -> None:
    out_dir, conn = db
    _make_project(conn, "show-x")
    with pytest.raises(MoveError, match="unknown video"):
        move_video(out_dir, "no-such-video", "show-x")


def test_refuses_when_ingest_active(db) -> None:
    out_dir, conn = db
    _make_project(conn, "show-b")
    _make_video_at(out_dir, conn, "vid_busy", "inbox")
    state_mod.begin("vid_busy")  # active record (done=False by default)
    with pytest.raises(MoveError, match="ingest active"):
        move_video(out_dir, "vid_busy", "show-b")


def test_refuses_when_dst_exists(db) -> None:
    out_dir, conn = db
    _make_project(conn, "show-c")
    _make_video_at(out_dir, conn, "vid_dst", "inbox")
    # Pre-populate the destination so the move thinks it's already
    # there.
    dst = out_dir / "projects" / "show-c" / "vid_dst"
    dst.mkdir(parents=True)
    (dst / "stranger.txt").write_text("oops", encoding="utf-8")
    with pytest.raises(MoveError, match="destination already exists"):
        move_video(out_dir, "vid_dst", "show-c")


# ---------------------------------------------------------------------------
# Crash recovery cases (simulate by writing the intermediate state directly)
# ---------------------------------------------------------------------------


def _seed_in_flight(
    out_dir: Path,
    conn,
    video_id: str,
    *,
    from_project: str,
    to_project: str,
    status: str,
    src_exists: bool,
    dst_exists: bool,
    db_at_src: bool = True,
) -> tuple[Path, Path]:
    """Create the on-disk + DB state of a half-finished move."""
    src = out_dir / "projects" / from_project / video_id
    dst = out_dir / "projects" / to_project / video_id
    if src_exists:
        src.mkdir(parents=True, exist_ok=True)
        (src / "transcript.json").write_text("{}", encoding="utf-8")
    if dst_exists:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "transcript.json").write_text("{}", encoding="utf-8")
    # videos row reflects state at moment of crash.
    conn.execute(
        "INSERT INTO videos(id, url, title, created_at, updated_at, project_id, path, move_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'moving')",
        (
            video_id,
            f"https://x/{video_id}",
            video_id,
            "2026-05-04",
            "2026-05-04",
            from_project if db_at_src else to_project,
            str(src) if db_at_src else str(dst),
        ),
    )
    conn.execute(
        "INSERT INTO move_log(video_id, from_project, to_project, "
        "from_path, to_path, started_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            video_id, from_project, to_project,
            str(src), str(dst), "2026-05-04", status,
        ),
    )
    return src, dst


def test_recovery_case_a_started_only_src(db) -> None:
    """status='started', src exists, dst doesn't -- copy never started.
    Roll back: clear move_state, leave src intact, mark log rolled_back."""
    out_dir, conn = db
    _make_project(conn, "show-r")
    src, dst = _seed_in_flight(
        out_dir, conn, "vid_a",
        from_project="inbox", to_project="show-r",
        status="started", src_exists=True, dst_exists=False,
    )
    counts = recover_in_flight_moves(out_dir)
    assert counts["rolled_back"] == 1

    row = conn.execute(
        "SELECT project_id, path, move_state FROM videos WHERE id='vid_a'"
    ).fetchone()
    assert row["move_state"] is None
    assert row["project_id"] == "inbox"
    assert row["path"] == str(src)
    assert src.exists()
    assert not dst.exists()


def test_recovery_case_b_started_both_exist(db) -> None:
    """status='started', both exist -- copy was partial. Discard dst,
    keep src, roll back."""
    out_dir, conn = db
    _make_project(conn, "show-r")
    src, dst = _seed_in_flight(
        out_dir, conn, "vid_b",
        from_project="inbox", to_project="show-r",
        status="started", src_exists=True, dst_exists=True,
    )
    counts = recover_in_flight_moves(out_dir)
    assert counts["rolled_back"] == 1

    row = conn.execute(
        "SELECT project_id, path FROM videos WHERE id='vid_b'"
    ).fetchone()
    assert row["project_id"] == "inbox"
    assert src.exists()
    assert not dst.exists()


def test_recovery_case_c_verified_both_exist(db) -> None:
    """status='verified', both exist -- verify ran but commit didn't.
    Roll forward, clean up the source."""
    out_dir, conn = db
    _make_project(conn, "show-r")
    src, dst = _seed_in_flight(
        out_dir, conn, "vid_c",
        from_project="inbox", to_project="show-r",
        status="verified", src_exists=True, dst_exists=True,
    )
    counts = recover_in_flight_moves(out_dir)
    assert counts["recovered"] == 1

    row = conn.execute(
        "SELECT project_id, path, move_state FROM videos WHERE id='vid_c'"
    ).fetchone()
    assert row["project_id"] == "show-r"
    assert row["path"] == str(dst)
    assert row["move_state"] is None
    assert dst.exists()
    assert not src.exists()


def test_recovery_case_d_verified_only_dst(db) -> None:
    """status='verified', only dst exists -- FS finished, DB never
    updated. Roll forward."""
    out_dir, conn = db
    _make_project(conn, "show-r")
    src, dst = _seed_in_flight(
        out_dir, conn, "vid_d",
        from_project="inbox", to_project="show-r",
        status="verified", src_exists=False, dst_exists=True,
    )
    counts = recover_in_flight_moves(out_dir)
    assert counts["recovered"] == 1

    row = conn.execute(
        "SELECT project_id, path FROM videos WHERE id='vid_d'"
    ).fetchone()
    assert row["project_id"] == "show-r"
    assert row["path"] == str(dst)


def test_recovery_case_e_started_neither_exists(db) -> None:
    """status='started', neither side has the folder -- ambiguous; mark
    failed so an operator decides. Latch is still cleared."""
    out_dir, conn = db
    _make_project(conn, "show-r")
    _seed_in_flight(
        out_dir, conn, "vid_e",
        from_project="inbox", to_project="show-r",
        status="started", src_exists=False, dst_exists=False,
    )
    counts = recover_in_flight_moves(out_dir)
    assert counts["manual_review"] == 1

    row = conn.execute(
        "SELECT move_state FROM videos WHERE id='vid_e'"
    ).fetchone()
    assert row["move_state"] is None
    log_row = conn.execute(
        "SELECT status, error FROM move_log WHERE video_id='vid_e'"
    ).fetchone()
    assert log_row["status"] == "failed"
    assert "manual review" in (log_row["error"] or "")

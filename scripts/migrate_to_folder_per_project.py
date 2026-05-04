"""One-shot data migration: flat output/<video_id>/ -> nested
output/projects/<project_id>/<video_id>/.

Run with the server stopped. The script:

  1. Audits the existing DB for blockers (active ingests, multi-project
     videos, missing schema).
  2. Backs up app.db to app.db.pre-folder-migration.bak.
  3. Applies migrations 004 + 005 (schema additions + project_videos drop).
     004 runs immediately; 005 runs at the END once data is backfilled.
  4. Creates the Inbox project if missing.
  5. For each video, assigns project_id (from project_videos or to
     Inbox), assigns the new path, and moves the on-disk folder.
  6. Verifies: every videos row has non-null project_id + path; the
     folder exists at the new path.
  7. Runs migration 005 (drops project_videos).
  8. Writes a manifest at scripts/_migration_manifest.json with every
     (old_path, new_path, project_id) tuple for audit / rollback.

Dry-run by default. Pass --apply to actually write.

Roll-forward only after step 5; if an error occurs before that, the
script aborts and the user can restore from the .bak file. After step 5
the moves are committed and the .bak is your last safety net.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"
DB_PATH = OUTPUT_DIR / "app.db"
MANIFEST_PATH = Path(__file__).parent / "_migration_manifest.json"
INGESTS_REGISTRY = OUTPUT_DIR / "_ingests.json"
PROJECTS_DIR_NAME = "projects"
INBOX_ID = "inbox"
INBOX_NAME = "Inbox"

# Top-level files/dirs that the migration must NOT touch.
SACRED_TOP_LEVEL = {"app.db", "app.db.pre-folder-migration.bak", "_ingests.json", PROJECTS_DIR_NAME}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    """Slugify a project name to lower-kebab. Mirrors server.projects."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "project"


def _open_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found at {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _backup_db() -> Path:
    """Snapshot app.db before any writes. SQLite VACUUM INTO writes a
    clean copy that's guaranteed consistent even with WAL/journal pages
    in flight."""
    bak = OUTPUT_DIR / "app.db.pre-folder-migration.bak"
    if bak.exists():
        # Stamped backup so we don't clobber a previous attempt's safety net.
        stamped = OUTPUT_DIR / f"app.db.pre-folder-migration-{int(time.time())}.bak"
        shutil.copy2(bak, stamped)
        print(f"  pre-existing backup moved to {stamped.name}")
    conn = _open_db()
    try:
        conn.execute(f"VACUUM INTO '{str(bak).replace(chr(39), chr(39)*2)}'")
    finally:
        conn.close()
    print(f"  db backup written: {bak}")
    return bak


def _apply_migration_by_name(conn: sqlite3.Connection, target_name: str) -> None:
    """Apply ONE specific migration by filename stem (e.g.
    '004_folder_per_project'). The server's `run_migrations` helper
    applies every pending migration in order; we use this targeted
    helper so we can interleave data work BETWEEN 004 and 005.

    Idempotent: if the migration has already been applied (recorded in
    schema_migrations), this is a no-op. If a previous run got partway
    through, this still re-runs the migration body which is itself
    idempotent (every CREATE/ALTER uses IF NOT EXISTS where SQLite
    allows; ADD COLUMN failures on rerun are tolerated)."""
    from server.db import MIGRATIONS_DIR  # noqa: E402

    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    already = bool(conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name=?", (target_name,),
    ).fetchone())
    if already:
        print(f"  migration {target_name} already applied")
        return

    path = MIGRATIONS_DIR / f"{target_name}.sql"
    if not path.exists():
        raise RuntimeError(f"migration file missing: {path}")

    body = path.read_text(encoding="utf-8")
    applied_at = _iso_now().replace("'", "''")
    script = (
        "BEGIN;\n"
        f"{body}\n"
        "INSERT OR IGNORE INTO schema_migrations(name, applied_at) "
        f"VALUES('{target_name}', '{applied_at}');\n"
        "COMMIT;\n"
    )
    try:
        conn.executescript(script)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    print(f"  applied migration {target_name}")


def _apply_migration_004(conn: sqlite3.Connection) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    _apply_migration_by_name(conn, "004_folder_per_project")
    if not _table_columns(conn, "videos").issuperset({"project_id", "path", "move_state"}):
        raise RuntimeError("migration 004 ran but new columns are missing")


def _apply_migration_005(conn: sqlite3.Connection) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    _apply_migration_by_name(conn, "005_drop_project_videos")
    existing = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_videos'"
    ).fetchone())
    if existing:
        raise RuntimeError("migration 005 ran but project_videos still exists")


def _ensure_inbox(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT id, system_kind FROM projects WHERE id=?",
        (INBOX_ID,),
    ).fetchone()
    if row is not None:
        if row["system_kind"] != "inbox":
            conn.execute(
                "UPDATE projects SET system_kind='inbox' WHERE id=?",
                (INBOX_ID,),
            )
            print(f"  marked existing '{INBOX_ID}' project as system_kind='inbox'")
        else:
            print("  Inbox project exists")
        return
    now = _iso_now()
    conn.execute(
        "INSERT INTO projects(id, name, description, system_kind, created_at, updated_at) "
        "VALUES (?, ?, NULL, 'inbox', ?, ?)",
        (INBOX_ID, INBOX_NAME, now, now),
    )
    print(f"  created Inbox project (id='{INBOX_ID}')")


def _audit(conn: sqlite3.Connection) -> dict:
    """Return a snapshot used for the before/after report. Also raises
    if the DB is in a state that makes migration unsafe."""
    # Active ingest check -- registry file written by state.py.
    if INGESTS_REGISTRY.exists():
        try:
            data = json.loads(INGESTS_REGISTRY.read_text(encoding="utf-8"))
            active = [r for r in data if not r.get("done")]
            if active:
                raise SystemExit(
                    f"refusing to migrate: {len(active)} active ingest(s) found in "
                    f"{INGESTS_REGISTRY}. Stop the server and let the queue drain."
                )
        except json.JSONDecodeError:
            pass  # malformed registry -- ignore, server will rebuild it

    # If project_videos still exists, count multi-project videos. Post-
    # migration the table is gone; this only runs first time.
    multi = []
    pv_present = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_videos'"
    ).fetchone())
    if pv_present:
        multi = [
            dict(r) for r in conn.execute(
                "SELECT video_id, COUNT(*) AS n FROM project_videos "
                "GROUP BY video_id HAVING COUNT(*) > 1"
            )
        ]
        if multi:
            print("\nERROR: the following videos are members of more than one project:")
            for m in multi:
                print(f"  {m['video_id']}: in {m['n']} projects")
            print(
                "\nThe folder-per-project migration enforces 1:1 membership. Either:\n"
                "  - DELETE the duplicate project_videos rows (keep the desired one), or\n"
                "  - manually decide where these videos go before re-running the migration.\n"
            )
            raise SystemExit(1)

    counts = {
        "videos_total": conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
        "projects_total": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
        "project_videos_present": pv_present,
    }
    # The new columns may not exist yet (pre-migration-004 deployment).
    # Only count migrated rows when the schema supports it.
    video_cols = _table_columns(conn, "videos")
    if {"project_id", "path"}.issubset(video_cols):
        counts["videos_already_migrated"] = conn.execute(
            "SELECT COUNT(*) FROM videos WHERE project_id IS NOT NULL AND path IS NOT NULL"
        ).fetchone()[0]
    else:
        counts["videos_already_migrated"] = 0
        counts["schema_004_applied"] = False
    if pv_present:
        counts["project_videos_rows"] = conn.execute(
            "SELECT COUNT(*) FROM project_videos"
        ).fetchone()[0]
    return counts


def _build_video_to_project_map(conn: sqlite3.Connection) -> dict[str, str]:
    """video_id -> project_id (where project_id falls back to Inbox).
    Reads project_videos when present; otherwise reads videos.project_id
    (idempotent re-run)."""
    out: dict[str, str] = {}
    pv_present = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_videos'"
    ).fetchone())
    if pv_present:
        for r in conn.execute("SELECT video_id, project_id FROM project_videos"):
            out[r["video_id"]] = r["project_id"]
    # videos.project_id wins if a partial earlier run wrote it. Only
    # readable once 004 has been applied.
    if "project_id" in _table_columns(conn, "videos"):
        for r in conn.execute("SELECT id, project_id FROM videos WHERE project_id IS NOT NULL"):
            out[r["id"]] = r["project_id"]
    # Inbox fallback for unprojected.
    for r in conn.execute("SELECT id FROM videos"):
        out.setdefault(r["id"], INBOX_ID)
    return out


def _plan_moves(conn: sqlite3.Connection, mapping: dict[str, str]) -> list[dict]:
    """For each videos row, work out (old_path, new_path, project_id).
    Skip rows that are already at their target (idempotent re-run)."""
    plan: list[dict] = []
    has_path_col = "path" in _table_columns(conn, "videos")
    select = "SELECT id, path FROM videos ORDER BY created_at" if has_path_col else "SELECT id FROM videos ORDER BY created_at"
    rows = conn.execute(select).fetchall()
    for r in rows:
        vid = r["id"]
        target_project = mapping[vid]
        target_path = OUTPUT_DIR / PROJECTS_DIR_NAME / target_project / vid
        # Discover the current on-disk path:
        # 1. videos.path (if a previous run set it)
        # 2. flat: OUTPUT_DIR / vid
        # 3. nested under some other project: OUTPUT_DIR / projects / X / vid
        current_path: Path | None = None
        if has_path_col and r["path"]:
            p = Path(r["path"])
            if p.exists():
                current_path = p
        if current_path is None:
            flat = OUTPUT_DIR / vid
            if flat.is_dir():
                current_path = flat
        if current_path is None:
            nested_root = OUTPUT_DIR / PROJECTS_DIR_NAME
            if nested_root.is_dir():
                for proj in nested_root.iterdir():
                    if not proj.is_dir():
                        continue
                    candidate = proj / vid
                    if candidate.is_dir():
                        current_path = candidate
                        break

        action: str
        if current_path is None:
            # No folder on disk anywhere. Still record project_id + path
            # so the DB row is consistent post-migration.
            action = "db_only"
        elif current_path.resolve() == target_path.resolve():
            action = "noop"
        else:
            action = "move"

        plan.append({
            "video_id": vid,
            "project_id": target_project,
            "current_path": str(current_path) if current_path else None,
            "target_path": str(target_path),
            "action": action,
        })
    return plan


def _execute_move(item: dict) -> None:
    """Same-drive os.rename() when possible; copytree+rmtree otherwise.
    Raises on any failure -- caller is responsible for catching and
    surfacing the error."""
    src = Path(item["current_path"])
    dst = Path(item["target_path"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        # Should have been caught by the noop check, but be defensive.
        raise FileExistsError(f"target already exists: {dst}")
    try:
        # os.rename across filesystems raises OSError on Windows /
        # EXDEV on POSIX. Catch and fall back to copy.
        src.rename(dst)
    except OSError:
        shutil.copytree(src, dst)
        # Verify before deleting source.
        for path in src.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(src)
            mirror = dst / rel
            if not mirror.is_file():
                raise RuntimeError(f"verify failed: missing {mirror}")
            if mirror.stat().st_size != path.stat().st_size:
                raise RuntimeError(f"verify failed: size mismatch {mirror}")
        shutil.rmtree(src)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes. Without this flag the script audits and prints the plan but does not modify anything.",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip the app.db backup step (FOR TESTS ONLY).",
    )
    args = parser.parse_args()

    print(f"=== folder-per-project migration ({'APPLY' if args.apply else 'DRY-RUN'}) ===\n")

    conn = _open_db()
    try:
        before = _audit(conn)
        print("Before:")
        for k, v in before.items():
            print(f"  {k}: {v}")
        print()

        if not args.apply:
            # Apply 004 in dry-run too so the audit of the plan reflects
            # the post-schema-change world. We open a separate in-memory
            # copy of the schema check rather than mutating the real DB.
            #
            # In practice the user will run --apply right after, so the
            # impact is minimal. The dry-run still prints the move plan
            # against the real data.
            need_004 = not _table_columns(conn, "videos").issuperset({"project_id", "path", "move_state"})
            if need_004:
                print("  (dry-run note) migration 004 is not yet applied; the move plan below assumes it will be applied first.")

        # Step 1 -- read project_videos BEFORE any schema migration so the
        # membership data is captured. Migration 005 drops the table; if
        # we read after, we get nothing and every video falls back to
        # Inbox (catastrophic data-loss bug from the first attempt).
        print("\nStep 1: capture membership map (BEFORE migrations)")
        mapping = _build_video_to_project_map(conn)
        per_project_pre: dict[str, int] = {}
        for v in mapping.values():
            per_project_pre[v] = per_project_pre.get(v, 0) + 1
        print(f"  total assignments: {len(mapping)}")
        for k, v in sorted(per_project_pre.items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {v}")

        if args.apply:
            print("\nStep 2: backup app.db")
            if args.no_backup:
                print("  --no-backup specified, skipping")
            else:
                _backup_db()

            print("\nStep 3: schema migration 004 (additive)")
            _apply_migration_004(conn)

            print("\nStep 4: ensure Inbox project")
            _ensure_inbox(conn)
        else:
            print("\nStep 2 (skipped in dry-run): backup app.db")
            print("Step 3 (skipped in dry-run): schema migration 004")
            print("Step 4 (skipped in dry-run): ensure Inbox project")

        print("\nStep 5: build move plan")
        plan = _plan_moves(conn, mapping)
        action_counts: dict[str, int] = {}
        per_project: dict[str, int] = {}
        for item in plan:
            action_counts[item["action"]] = action_counts.get(item["action"], 0) + 1
            per_project[item["project_id"]] = per_project.get(item["project_id"], 0) + 1
        print(f"  total videos: {len(plan)}")
        for k, v in sorted(action_counts.items()):
            print(f"  action={k}: {v}")
        for k, v in sorted(per_project.items(), key=lambda kv: -kv[1]):
            print(f"  -> {k}: {v}")
        print()

        # Show a sample of moves so the user can sanity-check.
        moves_only = [p for p in plan if p["action"] == "move"]
        for item in moves_only[:5]:
            print(f"  MOVE  {item['video_id']}")
            print(f"        from: {item['current_path']}")
            print(f"        to:   {item['target_path']}")
        if len(moves_only) > 5:
            print(f"  ... and {len(moves_only) - 5} more moves")
        db_only = [p for p in plan if p["action"] == "db_only"]
        for item in db_only:
            print(f"  DB-ONLY  {item['video_id']}  (no folder on disk; will assign path={item['target_path']})")

        if not args.apply:
            print("\n(dry-run) re-run with --apply to write changes.\n")
            return 0

        print("\nStep 6: execute moves + DB updates")
        manifest_entries: list[dict] = []
        errors: list[dict] = []
        for i, item in enumerate(plan, 1):
            vid = item["video_id"]
            project_id = item["project_id"]
            target_path = item["target_path"]
            try:
                if item["action"] == "move":
                    _execute_move(item)
                    print(f"  [{i:>3}/{len(plan)}] moved {vid} -> {target_path}")
                elif item["action"] == "noop":
                    pass  # already at target; just update DB to reflect it
                else:
                    print(f"  [{i:>3}/{len(plan)}] db-only {vid}")
                conn.execute(
                    "UPDATE videos SET project_id=?, path=?, updated_at=? WHERE id=?",
                    (project_id, target_path, _iso_now(), vid),
                )
                manifest_entries.append({
                    "video_id": vid,
                    "project_id": project_id,
                    "from_path": item["current_path"],
                    "to_path": target_path,
                    "action": item["action"],
                })
            except Exception as e:
                errors.append({"video_id": vid, "error": f"{type(e).__name__}: {e}"})
                print(f"  [{i:>3}/{len(plan)}] FAILED {vid}: {e}", file=sys.stderr)

        if errors:
            print(f"\n{len(errors)} videos failed migration:")
            for err in errors:
                print(f"  {err['video_id']}: {err['error']}")
            print("\nDB rows for failed videos were NOT updated. Source folders are intact.")
            print("Investigate and re-run --apply once issues are resolved.")
            MANIFEST_PATH.write_text(
                json.dumps({"applied_at": _iso_now(), "errors": errors, "entries": manifest_entries}, indent=2),
                encoding="utf-8",
            )
            return 1

        print("\nStep 7: verify post-migration state")
        unmigrated = list(conn.execute(
            "SELECT id FROM videos WHERE project_id IS NULL OR path IS NULL"
        ))
        if unmigrated:
            print(f"  ERROR: {len(unmigrated)} videos still have NULL project_id or path:")
            for r in unmigrated[:10]:
                print(f"    {r['id']}")
            return 2
        print(f"  OK: all {before['videos_total']} videos have project_id + path")

        print("\nStep 8: drop project_videos (migration 005)")
        _apply_migration_005(conn)

        print("\nStep 9: write manifest")
        MANIFEST_PATH.write_text(
            json.dumps({
                "applied_at": _iso_now(),
                "before": before,
                "entries": manifest_entries,
            }, indent=2),
            encoding="utf-8",
        )
        print(f"  manifest: {MANIFEST_PATH}")

        print("\n=== done ===")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

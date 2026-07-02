from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path


def resolve_db_path(raw: str | None) -> Path:
    value = raw or os.getenv("BOT_DB_PATH") or "navidvpn.db"
    return Path(value).expanduser().resolve()


def backup_database(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.agent-credit-reset.{stamp}.bak")
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(backup_path, timeout=30)
    try:
        src.backup(dst, pages=512, sleep=0.02)
    finally:
        dst.close()
        src.close()
    return backup_path


def require_columns(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('agents')"
        ).fetchall()
    }
    if "agents" not in tables:
        raise RuntimeError("agents table was not found in this database")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
    missing = {"access_level", "credit_limit_toman", "credit_used_toman"} - columns
    if missing:
        raise RuntimeError(f"agents table is missing columns: {', '.join(sorted(missing))}")


def reset_agent_credit(db_path: Path, *, dry_run: bool) -> dict[str, int]:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        require_columns(conn)
        before = conn.execute(
            """
            SELECT
              COUNT(*) AS agents,
              COALESCE(SUM(CASE WHEN lower(COALESCE(access_level,''))='open'
                   OR lower(COALESCE(access_level,'')) LIKE '%.open' THEN 1 ELSE 0 END),0) AS open_agents,
              COALESCE(SUM(credit_limit_toman),0) AS credit_limit_toman,
              COALESCE(SUM(credit_used_toman),0) AS credit_used_toman
            FROM agents
            """
        ).fetchone()
        conn.execute(
            """
            UPDATE agents
            SET access_level='closed',
                credit_limit_toman=0,
                credit_used_toman=0
            """
        )
        changed = conn.execute("SELECT changes() AS c").fetchone()["c"]
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return {
            "agents": int(before["agents"]),
            "open_agents": int(before["open_agents"]),
            "credit_limit_toman": int(before["credit_limit_toman"]),
            "credit_used_toman": int(before["credit_used_toman"]),
            "rows_touched": int(changed),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert every representative to wallet-only mode and zero legacy credit fields."
    )
    parser.add_argument("--db", help="SQLite database path. Defaults to BOT_DB_PATH or ./navidvpn.db")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change, then rollback")
    parser.add_argument("--no-backup", action="store_true", help="Skip the pre-change SQLite backup")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    backup_path: Path | None = None
    if not args.dry_run and not args.no_backup:
        backup_path = backup_database(db_path)

    result = reset_agent_credit(db_path, dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] database: {db_path}")
    if backup_path:
        print(f"backup: {backup_path}")
    print(f"agents touched: {result['rows_touched']}")
    print(f"legacy open agents before: {result['open_agents']}")
    print(f"legacy credit_limit sum before: {result['credit_limit_toman']}")
    print(f"legacy credit_used sum before: {result['credit_used_toman']}")
    print("done: all agents are wallet-only and credit fields are zero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

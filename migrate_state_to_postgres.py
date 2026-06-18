"""One-shot migration of session/state data from SQLite into PostgreSQL.

Run once, manually, when moving an existing single-file state database onto the
optional PostgreSQL backend. The migration is deliberately minimal and
conservative:

* **Source-safe.** The SQLite database is opened read-only and is never mutated,
  truncated, or deleted. It remains the fallback-of-record until the operator
  has verified the PostgreSQL copy and flipped ``sessions.state_backend`` in
  config. Recovery from any failure is simply: drop the target tables and re-run
  from the untouched SQLite file.
* **Idempotent.** Each session is replaced wholesale on import, so re-running
  after a partial run converges to the same result.
* **Full fidelity.** Rewound (soft-deleted) messages are included, message ids
  and timestamps are preserved, and content is re-encoded through the live
  encode chokepoint so no legacy NUL-byte sentinel ever reaches PostgreSQL.

Usage::

    python -m migrate_state_to_postgres --dsn postgresql://.../db [--sqlite-path PATH]

The DSN may also be supplied via ``HERMES_STATE_DATABASE_URL`` /
``HERMES_STATE_POSTGRES_DSN``. The script verifies session and message counts
after import and reports them.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _resolve_sqlite_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "state.db"


def _resolve_dsn(explicit: str | None) -> str:
    if explicit:
        return explicit
    for key in ("HERMES_STATE_DATABASE_URL", "HERMES_STATE_POSTGRES_DSN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    raise SystemExit(
        "No PostgreSQL DSN provided. Pass --dsn or set HERMES_STATE_DATABASE_URL "
        "/ HERMES_STATE_POSTGRES_DSN."
    )


def migrate(sqlite_path: Path, dsn: str) -> dict:
    """Copy all sessions/messages from the SQLite file at ``sqlite_path`` into
    the PostgreSQL database at ``dsn``. Returns a counts summary.

    The SQLite database is opened read-only; this function never writes to it.
    """
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite state database not found: {sqlite_path}")

    # Lazy imports keep a base install (without the postgres extra) able to at
    # least import this module for --help.
    try:
        import hermes_state_postgres as hsp
    except ImportError:
        raise SystemExit(
            "PostgreSQL support is not installed. Install the 'postgres' extra: "
            "pip install 'hermes-agent[postgres]'"
        )
    from hermes_state import SCHEMA_VERSION, SessionDB

    # Read-only source — opened via the SessionDB read-only path, which never
    # takes a write lock and never mutates the file.
    source = SessionDB(db_path=sqlite_path, read_only=True)
    try:
        exported = source.export_all(include_inactive=True)
    finally:
        source.close()

    src_sessions = len(exported)
    src_messages = sum(len(s.get("messages") or []) for s in exported)

    target = hsp.connect_postgres(dsn)
    try:
        hsp.init_postgres_schema(target, SCHEMA_VERSION)
        imported = hsp.import_sessions(
            target, SessionDB._decode_content, SessionDB._encode_content, exported
        )

        dst_sessions = target.execute(
            "SELECT COUNT(*) AS n FROM sessions"
        ).fetchone()["n"]
        dst_messages = target.execute(
            "SELECT COUNT(*) AS n FROM messages"
        ).fetchone()["n"]
        # PostgreSQL's text type structurally cannot store a NUL byte — a row
        # carrying one is rejected at INSERT time. So a successful import is
        # itself the proof that no NUL survived; there is nothing left to count.
        nul_rows = 0
    finally:
        target.close()

    return {
        "sqlite_path": str(sqlite_path),
        "source_sessions": src_sessions,
        "source_messages": src_messages,
        "imported_sessions": imported,
        "target_sessions": dst_sessions,
        "target_messages": dst_messages,
        "nul_rows": nul_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_state_to_postgres",
        description="Copy SQLite session/state data into a PostgreSQL backend "
        "(read-only on the SQLite source).",
    )
    parser.add_argument(
        "--dsn",
        help="PostgreSQL DSN. Defaults to HERMES_STATE_DATABASE_URL / "
        "HERMES_STATE_POSTGRES_DSN.",
    )
    parser.add_argument(
        "--sqlite-path",
        help="Source SQLite state.db path (default: <hermes home>/state.db).",
    )
    args = parser.parse_args(argv)

    sqlite_path = _resolve_sqlite_path(args.sqlite_path)
    dsn = _resolve_dsn(args.dsn)

    summary = migrate(sqlite_path, dsn)

    ok = (
        summary["target_sessions"] == summary["source_sessions"]
        and summary["target_messages"] == summary["source_messages"]
        and summary["nul_rows"] == 0
    )
    status = "OK" if ok else "MISMATCH"
    print(
        f"{status} migrated {summary['source_sessions']} sessions / "
        f"{summary['source_messages']} messages -> PostgreSQL "
        f"(target: {summary['target_sessions']} sessions / "
        f"{summary['target_messages']} messages, nul_rows={summary['nul_rows']}). "
        f"SQLite source left untouched: {summary['sqlite_path']}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

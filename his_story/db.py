"""SQLite connection + numbered migrations (his_story/migrations/NNN_name.sql)."""
import pathlib
import sqlite3
import time

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at INTEGER)")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    for f in sorted(MIGRATIONS.glob("*.sql")):
        if f.name in done:
            continue
        conn.executescript(f.read_text())
        conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (f.name, int(time.time())))
        conn.commit()


def start_run(conn, source, label=None):
    cur = conn.execute("INSERT INTO import_runs (source, label, started_at) VALUES (?, ?, ?)",
                       (source, label, int(time.time())))
    return cur.lastrowid


def finish_run(conn, run_id, stats):
    import json
    conn.execute("UPDATE import_runs SET finished_at = ?, stats_json = ? WHERE id = ?",
                 (int(time.time()), json.dumps(stats), run_id))
    conn.commit()

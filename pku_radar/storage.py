import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import timestamp


class Store:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                provider TEXT NOT NULL, external_id TEXT NOT NULL,
                source_id TEXT, source_name TEXT, title TEXT, published_at TEXT,
                url TEXT, category TEXT, intent_group TEXT, summary TEXT,
                upstream_is_event INTEGER, event_time_text TEXT, event_location TEXT,
                raw_json TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                rank_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(rank_status IN ('pending', 'succeeded', 'failed')),
                recommend INTEGER, priority TEXT, recommendation_reason TEXT, ranked_at TEXT,
                rank_attempts INTEGER NOT NULL DEFAULT 0, last_rank_error TEXT,
                surfaced_at TEXT, PRIMARY KEY(provider, external_id)
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('success', 'partial', 'failed')),
                fetched_count INTEGER NOT NULL DEFAULT 0, new_count INTEGER NOT NULL DEFAULT 0,
                ranked_count INTEGER NOT NULL DEFAULT 0, recommended_count INTEGER NOT NULL DEFAULT 0,
                surfaced_count INTEGER NOT NULL DEFAULT 0, error TEXT
            );
        """)

    def close(self):
        self.db.close()

    def ingest(self, notices, now):
        new_count = 0
        with self.db:
            for notice in notices:
                values = asdict(notice)
                keys = list(values)
                existing = self.db.execute(
                    "SELECT 1 FROM items WHERE provider=? AND external_id=?",
                    (notice.provider, notice.external_id),
                ).fetchone()
                new_count += existing is None
                updates = ", ".join(f"{key}=excluded.{key}" for key in keys[2:])
                self.db.execute(
                    f"INSERT INTO items ({', '.join(keys)}, first_seen_at, last_seen_at) "
                    f"VALUES ({', '.join('?' for _ in range(len(keys) + 2))}) "
                    f"ON CONFLICT(provider, external_id) DO UPDATE SET {updates}, "
                    "last_seen_at=excluded.last_seen_at",
                    [*values.values(), timestamp(now), timestamp(now)],
                )
        return new_count

    def candidates(self, max_attempts, rerank=False):
        condition = "" if rerank else " AND (rank_status='pending' OR (rank_status='failed' AND rank_attempts < ?))"
        return self.db.execute(
            "SELECT * FROM items WHERE surfaced_at IS NULL" + condition + " ORDER BY provider, external_id",
            () if rerank else (max_attempts,),
        ).fetchall()

    def reset_attempts(self, row):
        with self.db:
            self.db.execute("UPDATE items SET rank_attempts=0 WHERE provider=? AND external_id=?",
                            (row["provider"], row["external_id"]))

    def record_rank(self, row, now, result=None, error=None):
        identity = (row["provider"], row["external_id"])
        with self.db:
            if error is not None:
                # A failed rerank must preserve a previous successful decision.
                status = "succeeded" if row["rank_status"] == "succeeded" else "failed"
                self.db.execute(
                    "UPDATE items SET rank_status=?, rank_attempts=rank_attempts+1, last_rank_error=? "
                    "WHERE provider=? AND external_id=?", (status, error, *identity))
            else:
                self.db.execute(
                    "UPDATE items SET rank_status='succeeded', recommend=?, priority=?, "
                    "recommendation_reason=?, ranked_at=?, rank_attempts=rank_attempts+1, last_rank_error=NULL "
                    "WHERE provider=? AND external_id=?",
                    (result.recommend, result.priority, result.reason, timestamp(now), *identity))

    def recommendations(self):
        return self.db.execute("SELECT * FROM items WHERE surfaced_at IS NULL "
                               "AND rank_status='succeeded' AND recommend=1").fetchall()

    def start_run(self, now):
        with self.db:
            return self.db.execute("INSERT INTO runs(started_at, status) VALUES (?, 'failed')",
                                   (timestamp(now),)).lastrowid

    def finish_run(self, run_id, now, status, counts, errors, shown=()):
        with self.db:
            for row in shown:
                self.db.execute("UPDATE items SET surfaced_at=? WHERE provider=? AND external_id=?",
                                (timestamp(now), row["provider"], row["external_id"]))
            self.db.execute(
                "UPDATE runs SET finished_at=?, status=?, fetched_count=?, new_count=?, ranked_count=?, "
                "recommended_count=?, surfaced_count=?, error=? WHERE id=?",
                (timestamp(now), status, counts["fetched"], counts["new"], counts["ranked"],
                 counts["recommended"], len(shown), "\n".join(errors) or None, run_id))

    def run(self, run_id):
        return self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

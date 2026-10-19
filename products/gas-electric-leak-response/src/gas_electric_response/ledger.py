"""SQLite ledger of processed submissions: makes webhook retries and polling idempotent."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class ProcessedLedger:
    TERMINAL = ("DONE", "REJECTED", "GAVE_UP")

    def __init__(self, path: str | Path):
        p = Path(path)
        if str(path) != ":memory:":
            p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
        self._db.execute("""CREATE TABLE IF NOT EXISTS processed(
            global_id TEXT PRIMARY KEY, leak_id TEXT, status TEXT, attempts INTEGER, detail TEXT, updated_at TEXT)""")

    def get(self, global_id: str) -> tuple[str, int] | None:
        with self._lock:
            row = self._db.execute("SELECT status, attempts FROM processed WHERE global_id=?", (global_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def is_terminal(self, global_id: str) -> bool:
        g = self.get(global_id)
        return bool(g and g[0] in self.TERMINAL)

    def claim(self, global_id: str, leak_id: str, lease_s: int = 600) -> bool:
        """Atomically take ownership of a submission so `serve` + `poll` (or two processes) never double-process.
        Returns False if it is already finished or another worker holds a live lease."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT status, updated_at FROM processed WHERE global_id=?", (global_id,)).fetchone()
                if row:
                    status, ts = row
                    if status in self.TERMINAL:
                        self._db.execute("ROLLBACK"); return False
                    age = (now - datetime.fromisoformat(ts)).total_seconds()
                    if status == "IN_PROGRESS" and age < lease_s:
                        self._db.execute("ROLLBACK"); return False
                    self._db.execute("UPDATE processed SET status='IN_PROGRESS', leak_id=?, updated_at=? WHERE global_id=?",
                                     (leak_id, now.isoformat(), global_id))
                else:
                    self._db.execute("INSERT INTO processed(global_id, leak_id, status, attempts, detail, updated_at) VALUES(?,?,?,0,?,?)",
                                     (global_id, leak_id, "IN_PROGRESS", "", now.isoformat()))
                self._db.execute("COMMIT")
                return True
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def mark(self, global_id: str, leak_id: str, status: str, detail: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """INSERT INTO processed(global_id, leak_id, status, attempts, detail, updated_at) VALUES(?,?,?,1,?,?)
                   ON CONFLICT(global_id) DO UPDATE SET leak_id=excluded.leak_id, status=excluded.status,
                   attempts=attempts+1, detail=excluded.detail, updated_at=excluded.updated_at""",
                (global_id, leak_id, status, detail[:500], now))

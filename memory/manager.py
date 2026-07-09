"""
IMQ2 Memory Manager
Long-term semantic memory via ChromaDB (vector similarity).
Episodic/fact storage via SQLite.
Falls back gracefully if ChromaDB isn't installed yet.
"""

import logging
import sqlite3
import datetime
import hashlib
from pathlib import Path
from typing import Optional

from config.loader import config, PROJECT_ROOT

log = logging.getLogger(__name__)

DB_DIR = PROJECT_ROOT / "memory" / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)
SQLITE_PATH = DB_DIR / "imq2.db"
CHROMA_PATH = str(DB_DIR / "chroma")


class MemoryManager:
    def __init__(self):
        self._chroma = None
        self._collection = None
        self._init_sqlite()
        self._init_chroma()

    # ------------------------------------------------------------------
    # SQLite — episodic / structured facts
    # ------------------------------------------------------------------

    def _init_sqlite(self):
        self._db = sqlite3.connect(str(SQLITE_PATH), check_same_thread=False)
        # The webapp subprocess and the voice/text process each open their
        # own connection to this same file (see webapp/server.py's own
        # IMQ2Agent -> MemoryManager). Without a busy timeout, a write from
        # one process while the other holds the lock raises immediately
        # instead of waiting briefly for the lock to clear.
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id               TEXT PRIMARY KEY,
                timestamp        TEXT NOT NULL,
                user_text        TEXT NOT NULL,
                agent_text       TEXT NOT NULL,
                prompt_tokens    INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0
            )
        """)
        # Add token columns to existing DBs that predate this schema.
        # f-string SQL here is safe: col/default are the hardcoded literals
        # above, never user/LLM input — ALTER TABLE ADD COLUMN also doesn't
        # support parameterized column/type names in sqlite3 anyway.
        for col, default in [("prompt_tokens", 0), ("completion_tokens", 0)]:
            try:
                self._db.execute(f"ALTER TABLE conversations ADD COLUMN {col} INTEGER DEFAULT {default}")
                self._db.commit()
                log.info(f"Migrated conversations table: added {col}")
            except Exception:
                pass  # column already exists
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                subject     TEXT NOT NULL,
                category    TEXT,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        # Unique index on subject — new facts about the same subject
        # overwrite rather than accumulate duplicates/contradictions.
        self._db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)
        """)
        # Full audit trail — every time a fact's content changes, the OLD
        # value is recorded here before being overwritten. Append-only,
        # never pruned: facts change rarely enough that this stays tiny.
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS fact_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                subject     TEXT NOT NULL,
                old_content TEXT,
                new_content TEXT NOT NULL,
                category    TEXT,
                changed_at  TEXT NOT NULL
            )
        """)
        self._db.commit()
        log.info(f"SQLite memory at {SQLITE_PATH}")

    # ------------------------------------------------------------------
    # ChromaDB — semantic vector memory
    # ------------------------------------------------------------------

    def _init_chroma(self):
        try:
            import chromadb
            client = chromadb.PersistentClient(path=CHROMA_PATH)
            self._collection = client.get_or_create_collection(
                name="imq2_memory",
                metadata={"hnsw:space": "cosine"},
            )
            log.info(f"ChromaDB memory at {CHROMA_PATH} ({self._collection.count()} entries)")
        except ImportError:
            log.warning("chromadb not installed — long-term semantic memory disabled. "
                        "Run: pip install chromadb")
        except Exception as e:
            log.warning(f"ChromaDB init failed: {e} — falling back to SQLite only.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, user_text: str, agent_text: str, timestamp: Optional[str] = None,
              prompt_tokens: int = 0, completion_tokens: int = 0):
        """Persist one conversation turn including token usage."""
        ts = timestamp or datetime.datetime.utcnow().isoformat()
        turn_id = hashlib.sha256(f"{ts}{user_text}".encode()).hexdigest()[:16]
        combined = f"User: {user_text}\nQ2: {agent_text}"

        # SQLite — a locked/corrupt DB must degrade to "this turn wasn't
        # journaled" rather than crash the whole chat() call; the turn
        # itself already succeeded by the time we get here.
        try:
            self._db.execute(
                "INSERT OR IGNORE INTO conversations "
                "(id, timestamp, user_text, agent_text, prompt_tokens, completion_tokens) "
                "VALUES (?,?,?,?,?,?)",
                (turn_id, ts, user_text, agent_text, prompt_tokens, completion_tokens),
            )
            self._db.commit()
        except sqlite3.Error as e:
            log.warning(f"SQLite store error (turn not journaled): {e}")

        # ChromaDB
        if self._collection is not None:
            try:
                self._collection.add(
                    documents=[combined],
                    ids=[turn_id],
                    metadatas=[{"timestamp": ts}],
                )
            except Exception as e:
                log.debug(f"ChromaDB store error: {e}")

    def retrieve(self, query: str, top_k: Optional[int] = None) -> list[str]:
        """Return the most semantically relevant past exchanges for a query."""
        k = top_k or config.get("memory.retrieval_top_k", 5)

        if self._collection is None or self._collection.count() == 0:
            return []

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(k, self._collection.count()),
            )
            return results["documents"][0] if results["documents"] else []
        except Exception as e:
            log.debug(f"ChromaDB retrieve error: {e}")
            return []

    def store_fact(self, subject: str, content: str, category: str = "general"):
        """
        Store or update a discrete fact, keyed by subject (e.g. subject='cat_name').
        If a fact already exists for this subject and the content has changed,
        the OLD value is recorded in fact_history before being overwritten —
        so corrections are tracked, never silently lost, even though the live
        facts table only ever holds the current value.
        """
        now = datetime.datetime.utcnow().isoformat()
        subject = subject.strip().lower().replace(" ", "_")

        existing = self._db.execute(
            "SELECT content FROM facts WHERE subject=?", (subject,)
        ).fetchone()

        if existing and existing[0] != content:
            self._db.execute(
                """
                INSERT INTO fact_history (subject, old_content, new_content, category, changed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (subject, existing[0], content, category, now),
            )
            log.info(f"Fact changed: [{subject}] '{existing[0]}' -> '{content}'")
        elif not existing:
            # First time this subject has ever been recorded — log it as a
            # creation event too, so /facts history shows the full lifecycle.
            self._db.execute(
                """
                INSERT INTO fact_history (subject, old_content, new_content, category, changed_at)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (subject, content, category, now),
            )

        self._db.execute(
            """
            INSERT INTO facts (subject, category, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subject) DO UPDATE SET
                content = excluded.content,
                category = excluded.category,
                updated_at = excluded.updated_at
            """,
            (subject, category, content, now, now),
        )
        self._db.commit()
        log.debug(f"Fact upserted: [{subject}] {content}")

    def get_fact_history(self, subject: Optional[str] = None) -> list[dict]:
        """Return the full change history for one subject, or all subjects if None."""
        if subject:
            subject = subject.strip().lower().replace(" ", "_")
            rows = self._db.execute(
                "SELECT subject, old_content, new_content, category, changed_at "
                "FROM fact_history WHERE subject=? ORDER BY changed_at ASC",
                (subject,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT subject, old_content, new_content, category, changed_at "
                "FROM fact_history ORDER BY changed_at ASC"
            ).fetchall()
        return [
            {"subject": r[0], "old_content": r[1], "new_content": r[2], "category": r[3], "changed_at": r[4]}
            for r in rows
        ]

    def get_facts(self, category: Optional[str] = None) -> list[str]:
        # Called unconditionally on every turn to build the system prompt
        # (see core/agent.py) — a locked/corrupt DB here must degrade to
        # "no facts this turn" rather than crash the whole chat() call.
        try:
            if category:
                rows = self._db.execute(
                    "SELECT content FROM facts WHERE category=? ORDER BY updated_at DESC", (category,)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT content FROM facts ORDER BY updated_at DESC"
                ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error as e:
            log.warning(f"SQLite get_facts error (continuing with no facts this turn): {e}")
            return []

    def get_facts_detailed(self, category: Optional[str] = None) -> list[dict]:
        """Like get_facts, but returns full rows (subject, category, timestamps) for inspection."""
        if category:
            rows = self._db.execute(
                "SELECT subject, content, category, created_at, updated_at FROM facts "
                "WHERE category=? ORDER BY updated_at DESC", (category,)
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT subject, content, category, created_at, updated_at FROM facts "
                "ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {"subject": r[0], "content": r[1], "category": r[2], "created_at": r[3], "updated_at": r[4]}
            for r in rows
        ]

    def prune_episodic(self, keep_last_n: int = 5000):
        """
        Cap episodic memory size. Deletes the oldest conversation turns beyond
        keep_last_n from both SQLite and ChromaDB. Facts are never pruned —
        only raw conversational episodes.
        """
        try:
            rows = self._db.execute(
                "SELECT id FROM conversations ORDER BY timestamp DESC LIMIT -1 OFFSET ?",
                (keep_last_n,),
            ).fetchall()
        except sqlite3.Error as e:
            log.warning(f"SQLite prune_episodic error, skipping this cycle: {e}")
            return 0
        stale_ids = [r[0] for r in rows]
        if not stale_ids:
            return 0

        try:
            self._db.executemany("DELETE FROM conversations WHERE id=?", [(i,) for i in stale_ids])
            self._db.commit()
        except sqlite3.Error as e:
            log.warning(f"SQLite prune_episodic delete error, skipping this cycle: {e}")
            return 0

        if self._collection is not None:
            try:
                self._collection.delete(ids=stale_ids)
            except Exception as e:
                log.debug(f"ChromaDB prune error: {e}")

        log.info(f"Pruned {len(stale_ids)} stale episodic entries (keeping last {keep_last_n}).")
        return len(stale_ids)

    def token_stats(self, since_days: Optional[int] = None) -> dict:
        """Return total and per-backend token usage. Optionally filter to last N days."""
        if since_days:
            cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=since_days)).isoformat()
            row = self._db.execute(
                "SELECT COUNT(*), SUM(prompt_tokens), SUM(completion_tokens) "
                "FROM conversations WHERE timestamp >= ?", (cutoff,)
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*), SUM(prompt_tokens), SUM(completion_tokens) FROM conversations"
            ).fetchone()
        return {
            "turns":              row[0] or 0,
            "prompt_tokens":      row[1] or 0,
            "completion_tokens":  row[2] or 0,
            "total_tokens":       (row[1] or 0) + (row[2] or 0),
        }

    def episodic_count(self) -> int:
        try:
            return self._db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        except sqlite3.Error as e:
            log.warning(f"SQLite episodic_count error: {e}")
            return 0

    def close(self):
        self._db.close()

"""
IMQ2 Budget Ledger
Tracks gift card balance and enforces spending limits for any purchase Q2
makes. This is the safety-critical piece of the purchasing feature — every
check here happens in code, not just as a prompt instruction, because the
one thing that must never be advisory-only is "did this exceed the budget."

Design principles:
- Q2 NEVER has access to real bank cards or saved payment methods — only a
  gift card balance loaded explicitly by William.
- Every purchase is logged with full detail (item, price, timestamp, status)
  for a complete audit trail, never silently overwritten.
- Balance checks and per-purchase caps are enforced HERE, before any browser
  automation runs — not left to the LLM to "remember" the rules.
"""

import logging
import sqlite3
import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from config.loader import PROJECT_ROOT

log = logging.getLogger(__name__)

DB_DIR = PROJECT_ROOT / "purchasing" / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)
SQLITE_PATH = DB_DIR / "ledger.db"


@dataclass
class BudgetCheckResult:
    approved: bool
    reason: str
    remaining_balance: Optional[float] = None


class BudgetLedger:
    def __init__(self):
        self._db = sqlite3.connect(str(SQLITE_PATH), check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS gift_cards (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                label           TEXT NOT NULL,
                original_amount REAL NOT NULL,
                remaining       REAL NOT NULL,
                added_at        TEXT NOT NULL,
                active          INTEGER NOT NULL DEFAULT 1,
                card_code       TEXT DEFAULT '',
                payment_type    TEXT DEFAULT 'site_gc'
            )
        """)
        # Migrate existing databases gracefully — ADD COLUMN fails silently
        # if the column already exists, which is the behaviour we want.
        for col, default in [
            ("card_code",    "TEXT DEFAULT ''"),
            ("payment_type", "TEXT DEFAULT 'site_gc'"),
        ]:
            try:
                self._db.execute(f"ALTER TABLE gift_cards ADD COLUMN {col} {default}")
                self._db.commit()
            except Exception:
                pass

        self._db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                item_description TEXT NOT NULL,
                merchant        TEXT NOT NULL,
                amount          REAL NOT NULL,
                status          TEXT NOT NULL,
                gift_card_id    INTEGER,
                requested_at    TEXT NOT NULL,
                completed_at    TEXT,
                notes           TEXT,
                FOREIGN KEY (gift_card_id) REFERENCES gift_cards(id)
            )
        """)
        self._db.commit()
        log.info(f"Budget ledger at {SQLITE_PATH}")

    # ------------------------------------------------------------------
    # Gift card management
    # ------------------------------------------------------------------

    def add_gift_card(self, label: str, amount: float, card_code: str = "",
                      payment_type: str = "site_gc") -> int:
        """
        Register a new gift card. payment_type controls how checkout uses it:
          'site_gc'       — site-specific gift card code entered in coupon field
                           (e.g. RotorVillage gift certificate)
          'account_balance' — balance pre-loaded to a site account, used
                             automatically at checkout with no card entry needed
                             (e.g. Amazon gift card redeemed to iamkewtoo account)
          'visa_mc'       — Visa/MC prepaid card, entered as payment method
                           at checkout (needs card number, expiry, CVV)
        """
        now = datetime.datetime.utcnow().isoformat()
        cursor = self._db.execute(
            "INSERT INTO gift_cards (label, original_amount, remaining, added_at, active, card_code, payment_type) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (label, amount, amount, now, card_code, payment_type),
        )
        self._db.commit()
        log.info(f"Gift card added: '{label}' ${amount:.2f} [{payment_type}]")
        return cursor.lastrowid

    def list_gift_cards(self, include_inactive: bool = False) -> list[dict]:
        """Return all gift cards, newest first."""
        query = "SELECT id, label, original_amount, remaining, added_at, active, card_code, payment_type FROM gift_cards"
        if not include_inactive:
            query += " WHERE active = 1"
        query += " ORDER BY added_at DESC"
        rows = self._db.execute(query).fetchall()
        return [
            {
                "id": r[0], "label": r[1], "original_amount": r[2],
                "remaining": r[3], "added_at": r[4], "active": bool(r[5]),
                "card_code": r[6] or "", "payment_type": r[7] or "site_gc",
            }
            for r in rows
        ]

    def total_available_balance(self) -> float:
        """Sum of remaining balance across all active gift cards."""
        row = self._db.execute(
            "SELECT COALESCE(SUM(remaining), 0) FROM gift_cards WHERE active = 1"
        ).fetchone()
        return row[0]

    def deactivate_gift_card(self, gift_card_id: int):
        self._db.execute("UPDATE gift_cards SET active = 0 WHERE id = ?", (gift_card_id,))
        self._db.commit()

    # ------------------------------------------------------------------
    # Spending caps (enforced in code, not the prompt)
    # ------------------------------------------------------------------

    def check_purchase_allowed(self, amount: float, per_purchase_cap: float) -> BudgetCheckResult:
        """
        The critical safety gate. Called BEFORE any browser automation runs.
        Checks both the per-purchase cap and total available balance.
        """
        if amount <= 0:
            return BudgetCheckResult(approved=False, reason="Purchase amount must be positive.")

        if amount > per_purchase_cap:
            return BudgetCheckResult(
                approved=False,
                reason=f"Amount ${amount:.2f} exceeds the per-purchase cap of ${per_purchase_cap:.2f}.",
            )

        available = self.total_available_balance()
        if amount > available:
            return BudgetCheckResult(
                approved=False,
                reason=f"Amount ${amount:.2f} exceeds available gift card balance of ${available:.2f}.",
                remaining_balance=available,
            )

        return BudgetCheckResult(approved=True, reason="OK", remaining_balance=available - amount)

    # ------------------------------------------------------------------
    # Purchase lifecycle — every step logged, never silently skipped
    # ------------------------------------------------------------------

    def record_purchase_request(self, item_description: str, merchant: str, amount: float) -> int:
        """Log that Q2 found an item and is about to ask for confirmation. Returns purchase_id."""
        now = datetime.datetime.utcnow().isoformat()
        cursor = self._db.execute(
            "INSERT INTO purchases (item_description, merchant, amount, status, requested_at) "
            "VALUES (?, ?, ?, 'pending_confirmation', ?)",
            (item_description, merchant, amount, now),
        )
        self._db.commit()
        return cursor.lastrowid

    def mark_confirmed(self, purchase_id: int):
        """User said yes — about to attempt checkout."""
        self._db.execute(
            "UPDATE purchases SET status = 'confirmed' WHERE id = ?", (purchase_id,)
        )
        self._db.commit()

    def mark_rejected(self, purchase_id: int, notes: str = ""):
        """User said no, or the budget check failed."""
        self._db.execute(
            "UPDATE purchases SET status = 'rejected', notes = ? WHERE id = ?",
            (notes, purchase_id),
        )
        self._db.commit()

    def mark_completed(self, purchase_id: int, gift_card_id: int, notes: str = ""):
        """
        Checkout succeeded. Deducts from the gift card balance HERE — this is
        the only place balance is ever decremented, and it only happens after
        a real completed purchase, never speculatively.
        """
        now = datetime.datetime.utcnow().isoformat()
        purchase = self._db.execute(
            "SELECT amount FROM purchases WHERE id = ?", (purchase_id,)
        ).fetchone()
        if purchase is None:
            raise ValueError(f"No purchase found with id {purchase_id}")
        amount = purchase[0]

        self._db.execute(
            "UPDATE purchases SET status = 'completed', completed_at = ?, gift_card_id = ?, notes = ? WHERE id = ?",
            (now, gift_card_id, notes, purchase_id),
        )
        self._db.execute(
            "UPDATE gift_cards SET remaining = remaining - ? WHERE id = ?",
            (amount, gift_card_id),
        )
        self._db.commit()
        log.info(f"Purchase #{purchase_id} completed: ${amount:.2f} from gift card #{gift_card_id}")

    def mark_failed(self, purchase_id: int, notes: str = ""):
        """Checkout was attempted but failed (site error, item out of stock, etc). No balance deducted."""
        self._db.execute(
            "UPDATE purchases SET status = 'failed', notes = ? WHERE id = ?",
            (notes, purchase_id),
        )
        self._db.commit()

    def get_purchase_history(self, limit: int = 50) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, item_description, merchant, amount, status, requested_at, completed_at, notes "
            "FROM purchases ORDER BY requested_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0], "item_description": r[1], "merchant": r[2], "amount": r[3],
                "status": r[4], "requested_at": r[5], "completed_at": r[6], "notes": r[7],
            }
            for r in rows
        ]

    def get_purchase(self, purchase_id: int) -> Optional[dict]:
        row = self._db.execute(
            "SELECT id, item_description, merchant, amount, status, requested_at, completed_at, notes "
            "FROM purchases WHERE id = ?",
            (purchase_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "item_description": row[1], "merchant": row[2], "amount": row[3],
            "status": row[4], "requested_at": row[5], "completed_at": row[6], "notes": row[7],
        }

    def close(self):
        self._db.close()

"""주문 기록 저장소(SQLite). 같은 전표가 여러 프린터로 나가면 한 건으로 묶는다."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash  TEXT NOT NULL,
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL,
    table_no      TEXT,
    order_no      TEXT,
    order_type    TEXT,
    printed_at    TEXT,
    item_summary  TEXT,
    raw_text      TEXT,
    printers      TEXT NOT NULL DEFAULT '[]',
    copies        INTEGER NOT NULL DEFAULT 1,
    has_raster    INTEGER NOT NULL DEFAULT 0,
    source_ip     TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_first_seen ON orders(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_orders_hash ON orders(content_hash, first_seen);
"""


@dataclass
class StoredOrder:
    id: int
    first_seen: float
    last_seen: float
    table_no: str | None
    order_no: str | None
    order_type: str
    printed_at: str | None
    item_summary: str
    raw_text: str
    printers: list[str]
    copies: int
    has_raster: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "table": self.table_no,
            "order_no": self.order_no,
            "order_type": self.order_type,
            "printed_at": self.printed_at,
            "items": self.item_summary,
            "raw_text": self.raw_text,
            "printers": self.printers,
            "copies": self.copies,
            "has_raster": self.has_raster,
        }


class OrderStore:
    def __init__(self, path: str | Path, dedup_window: float = 120.0, retention_hours: float = 48.0):
        self.path = str(path)
        self.dedup_window = dedup_window
        self.retention_hours = retention_hours
        self._lock = threading.Lock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def add(
        self,
        *,
        content_hash: str,
        table_no: str | None,
        order_no: str | None,
        order_type: str,
        printed_at: str | None,
        item_summary: str,
        raw_text: str,
        printer_ip: str,
        source_ip: str = "",
        has_raster: bool = False,
        now: float | None = None,
    ) -> int:
        """주문을 저장하고 id 를 돌려준다. 중복이면 기존 건을 갱신한다."""
        now = now or time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, printers, copies FROM orders "
                "WHERE content_hash = ? AND last_seen >= ? "
                "ORDER BY last_seen DESC LIMIT 1",
                (content_hash, now - self.dedup_window),
            ).fetchone()

            if row is not None:
                printers = json.loads(row["printers"])
                if printer_ip and printer_ip not in printers:
                    printers.append(printer_ip)
                self._conn.execute(
                    "UPDATE orders SET last_seen = ?, printers = ?, copies = ? WHERE id = ?",
                    (now, json.dumps(printers), row["copies"] + 1, row["id"]),
                )
                self._conn.commit()
                return int(row["id"])

            cursor = self._conn.execute(
                "INSERT INTO orders (content_hash, first_seen, last_seen, table_no, order_no,"
                " order_type, printed_at, item_summary, raw_text, printers, copies, has_raster,"
                " source_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    content_hash,
                    now,
                    now,
                    table_no,
                    order_no,
                    order_type,
                    printed_at,
                    item_summary,
                    raw_text,
                    json.dumps([printer_ip] if printer_ip else []),
                    1,
                    int(has_raster),
                    source_ip,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def recent(self, limit: int = 60, table: str | None = None, since_id: int = 0) -> list[StoredOrder]:
        query = "SELECT * FROM orders WHERE id > ?"
        params: list = [since_id]
        if table:
            query += " AND table_no = ?"
            params.append(table)
        query += " ORDER BY first_seen DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_to_order(row) for row in rows]

    def get(self, order_id: int) -> StoredOrder | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return _to_order(row) if row else None

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])

    def tables(self, hours: float = 12.0) -> list[str]:
        """최근에 등장한 테이블 번호 목록(숫자 순)."""
        cutoff = time.time() - hours * 3600
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT table_no FROM orders WHERE table_no IS NOT NULL AND first_seen >= ?",
                (cutoff,),
            ).fetchall()
        values = [row[0] for row in rows]
        return sorted(values, key=lambda v: (not v.isdigit(), int(v) if v.isdigit() else 0, v))

    def purge_old(self, now: float | None = None) -> int:
        """보관 기간이 지난 기록을 지운다(전표에는 전화번호·주소가 있을 수 있다)."""
        now = now or time.time()
        cutoff = now - self.retention_hours * 3600
        with self._lock:
            cursor = self._conn.execute("DELETE FROM orders WHERE last_seen < ?", (cutoff,))
            self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _to_order(row: sqlite3.Row) -> StoredOrder:
    return StoredOrder(
        id=int(row["id"]),
        first_seen=float(row["first_seen"]),
        last_seen=float(row["last_seen"]),
        table_no=row["table_no"],
        order_no=row["order_no"],
        order_type=row["order_type"] or "홀",
        printed_at=row["printed_at"],
        item_summary=row["item_summary"] or "",
        raw_text=row["raw_text"] or "",
        printers=json.loads(row["printers"]),
        copies=int(row["copies"]),
        has_raster=bool(row["has_raster"]),
    )

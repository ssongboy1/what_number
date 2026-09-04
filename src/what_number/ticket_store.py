"""홀 주문서 저장소.

주문서 한 장과 그 안의 메뉴 줄들을 담고, 어느 메뉴가 나갔는지를 기록한다.
원본 전표를 담는 store.py 와는 보관 정책이 달라 파일을 나눠 쓴다.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# 영업일이 바뀌는 시각. 새벽 장사를 전날로 묶는다.
BUSINESS_DAY_START_HOUR = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT    NOT NULL,
    source_key    TEXT    NOT NULL,
    business_day  TEXT    NOT NULL,
    order_no      TEXT    NOT NULL DEFAULT '',
    table_label   TEXT    NOT NULL DEFAULT '',
    table_key     TEXT    NOT NULL DEFAULT '',
    ticket_kind   TEXT    NOT NULL DEFAULT '신규',
    pos_no        TEXT    NOT NULL DEFAULT '',
    station       TEXT    NOT NULL DEFAULT '',
    received_at   REAL    NOT NULL,
    ordered_at    REAL,
    status        TEXT    NOT NULL DEFAULT 'open',
    done_at       REAL,
    item_count    INTEGER NOT NULL DEFAULT 0,
    served_count  INTEGER NOT NULL DEFAULT 0,
    rev           INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticket_key  ON tickets(source, source_key);
CREATE INDEX        IF NOT EXISTS idx_ticket_live ON tickets(status, received_at);
CREATE INDEX        IF NOT EXISTS idx_ticket_day  ON tickets(business_day, received_at);
CREATE INDEX        IF NOT EXISTS idx_ticket_rev  ON tickets(rev);

CREATE TABLE IF NOT EXISTS ticket_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    line_no     INTEGER NOT NULL,
    menu_name   TEXT    NOT NULL,
    option_text TEXT    NOT NULL DEFAULT '',
    quantity    INTEGER NOT NULL DEFAULT 1,
    served      INTEGER NOT NULL DEFAULT 0,
    served_at   REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_item_line ON ticket_items(ticket_id, line_no);
CREATE INDEX        IF NOT EXISTS idx_item_menu ON ticket_items(menu_name, ticket_id);

CREATE TABLE IF NOT EXISTS ticket_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

OPEN, READY, DONE, VOID = "open", "ready", "done", "void"


def business_day(when: float | None = None) -> str:
    """영업일. 새벽 5시 이전은 전날로 친다."""
    moment = datetime.fromtimestamp(when if when is not None else time.time())
    if moment.hour < BUSINESS_DAY_START_HOUR:
        moment -= timedelta(days=1)
    return moment.strftime("%Y-%m-%d")


def clean_option(text: str) -> str:
    """옵션 문구를 다듬는다. 앞뒤 공백과 '(옵션 없음)' 은 지운다."""
    trimmed = (text or "").strip()
    return "" if trimmed in ("(옵션 없음)", "(옵션없음)") else trimmed


def table_key_of(label: str) -> str:
    """테이블별로 묶기 위한 값. '2층-21' 과 '2층 - 21' 을 같게 본다."""
    return "".join((label or "").split()).replace("-", "").lower()


@dataclass
class Item:
    id: int
    line_no: int
    menu_name: str
    option_text: str
    quantity: int
    served: bool
    served_at: float | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "line_no": self.line_no,
            "menu": self.menu_name,
            "option": self.option_text,
            "qty": self.quantity,
            "served": self.served,
            "served_at": self.served_at,
        }


@dataclass
class Ticket:
    id: int
    source: str
    source_key: str
    business_day: str
    order_no: str
    table_label: str
    table_key: str
    ticket_kind: str
    pos_no: str
    station: str
    received_at: float
    ordered_at: float | None
    status: str
    done_at: float | None
    item_count: int
    served_count: int
    rev: int
    items: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "order_no": self.order_no,
            "table": self.table_label,
            "table_key": self.table_key,
            "kind": self.ticket_kind,
            "pos": self.pos_no,
            "station": self.station,
            "received_at": self.received_at,
            "ordered_at": self.ordered_at,
            "status": self.status,
            "item_count": self.item_count,
            "served_count": self.served_count,
            "rev": self.rev,
            "items": [i.to_dict() for i in self.items],
        }


class TicketStore:
    def __init__(self, path: str | Path, retention_days: float = 14.0):
        self.path = str(path)
        self.retention_days = retention_days
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # --- 변경 순번 ---
    def _bump_rev(self) -> int:
        """무엇이든 바뀔 때마다 1 오른다. 화면은 이 숫자만 보고 따라온다."""
        row = self._conn.execute("SELECT value FROM ticket_meta WHERE key = 'rev'").fetchone()
        rev = (int(row["value"]) if row else 0) + 1
        self._conn.execute(
            "INSERT OR REPLACE INTO ticket_meta (key, value) VALUES ('rev', ?)", (str(rev),)
        )
        return rev

    @property
    def rev(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT value FROM ticket_meta WHERE key = 'rev'").fetchone()
        return int(row["value"]) if row else 0

    # --- 주문서 넣기 ---
    def add_ticket(
        self,
        *,
        source: str,
        source_key: str,
        items: list,
        table_label: str = "",
        order_no: str = "",
        ticket_kind: str = "신규",
        pos_no: str = "",
        station: str = "",
        received_at: float | None = None,
        ordered_at: float | None = None,
        day: str | None = None,
    ) -> int:
        """주문서를 저장하고 id 를 돌려준다. 같은 source_key 면 기존 것을 준다."""
        now = received_at if received_at is not None else time.time()
        with self._lock, self._conn:
            found = self._conn.execute(
                "SELECT id FROM tickets WHERE source = ? AND source_key = ?", (source, source_key)
            ).fetchone()
            if found is not None:
                return int(found["id"])

            rev = self._bump_rev()
            cursor = self._conn.execute(
                "INSERT INTO tickets (source, source_key, business_day, order_no, table_label,"
                " table_key, ticket_kind, pos_no, station, received_at, ordered_at, status,"
                " item_count, served_count, rev) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source, source_key, day or business_day(now), order_no, table_label,
                    table_key_of(table_label), ticket_kind, pos_no, station, now, ordered_at,
                    OPEN, len(items), 0, rev,
                ),
            )
            ticket_id = int(cursor.lastrowid)
            for line_no, item in enumerate(items, start=1):
                self._conn.execute(
                    "INSERT INTO ticket_items (ticket_id, line_no, menu_name, option_text,"
                    " quantity) VALUES (?,?,?,?,?)",
                    (
                        ticket_id, line_no, item["menu"],
                        clean_option(item.get("option", "")), int(item.get("qty", 1) or 1),
                    ),
                )
            return ticket_id

    # --- 체크 ---
    def set_served(self, item_id: int, served: bool) -> dict:
        """항목의 나감 여부를 정한다.

        토글이 아니라 절대값이라, 두 번 눌리거나 재전송돼도 결과가 같다.
        마지막 항목을 채운 요청에만 became_ready 가 참으로 돌아간다.
        """
        with self._lock, self._conn:
            item = self._conn.execute(
                "SELECT ticket_id FROM ticket_items WHERE id = ?", (item_id,)
            ).fetchone()
            if item is None:
                return {"ok": False, "reason": "없는 항목입니다"}

            ticket_id = int(item["ticket_id"])
            ticket = self._conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if ticket is None:
                return {"ok": False, "reason": "없는 주문서입니다"}

            was_ready = ticket["status"] == READY
            self._conn.execute(
                "UPDATE ticket_items SET served = ?, served_at = ? WHERE id = ?",
                (1 if served else 0, time.time() if served else None, item_id),
            )
            counts = self._recount(ticket_id)
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "became_ready": counts["status"] == READY and not was_ready,
                "status": counts["status"],
                "served_count": counts["served"],
                "item_count": counts["total"],
            }

    def _recount(self, ticket_id: int) -> dict:
        """항목을 세어 주문서 상태를 다시 정한다. 반드시 쓰기와 같은 트랜잭션 안에서."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, SUM(served) AS served FROM ticket_items"
            " WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        total = int(row["total"] or 0)
        served = int(row["served"] or 0)

        current = self._conn.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()["status"]
        status = current
        if current in (OPEN, READY):
            status = READY if total and served >= total else OPEN

        self._conn.execute(
            "UPDATE tickets SET served_count = ?, item_count = ?, status = ?, rev = ?"
            " WHERE id = ?",
            (served, total, status, self._bump_rev(), ticket_id),
        )
        return {"total": total, "served": served, "status": status}

    def set_status(self, ticket_id: int, status: str) -> dict:
        """주문서를 내리거나(done) 다시 올린다(open). 지우지는 않는다."""
        if status not in (OPEN, READY, DONE, VOID):
            return {"ok": False, "reason": "알 수 없는 상태입니다"}
        with self._lock, self._conn:
            found = self._conn.execute(
                "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if found is None:
                return {"ok": False, "reason": "없는 주문서입니다"}
            self._conn.execute(
                "UPDATE tickets SET status = ?, done_at = ?, rev = ? WHERE id = ?",
                (status, time.time() if status == DONE else None, self._bump_rev(), ticket_id),
            )
            if status == OPEN:
                self._recount(ticket_id)
            return {"ok": True, "ticket_id": ticket_id, "status": status}

    # --- 읽기 ---
    def _load(self, rows: list) -> list:
        tickets = [self._to_ticket(row) for row in rows]
        if not tickets:
            return []
        ids = [t.id for t in tickets]
        marks = ",".join("?" * len(ids))
        items = self._conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_id IN (" + marks + ")"
            " ORDER BY ticket_id, line_no",
            ids,
        ).fetchall()
        by_ticket: dict = {}
        for row in items:
            by_ticket.setdefault(int(row["ticket_id"]), []).append(_to_item(row))
        for ticket in tickets:
            ticket.items = by_ticket.get(ticket.id, [])
        return tickets

    def live_tickets(self) -> list:
        """진행 중인 주문서. 오래된 것부터 - 홀에서는 오래된 게 급하다."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tickets WHERE status IN (?, ?) ORDER BY received_at, id",
                (OPEN, READY),
            ).fetchall()
            return self._load(rows)

    def get(self, ticket_id: int) -> Ticket | None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchall()
            loaded = self._load(rows)
        return loaded[0] if loaded else None

    def search_by_menu(self, menu: str, day: str | None = None, limit: int = 40) -> list:
        """그 메뉴가 든 주문서를 최신순으로. 기본은 오늘 것만."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.* FROM tickets t WHERE t.business_day = ? AND t.status != ?"
                " AND EXISTS (SELECT 1 FROM ticket_items i WHERE i.ticket_id = t.id"
                "             AND i.menu_name LIKE ?)"
                " ORDER BY t.received_at DESC, t.id DESC LIMIT ?",
                (day or business_day(), VOID, "%" + menu + "%", limit),
            ).fetchall()
            return self._load(rows)

    def menu_list(self, day: str | None = None) -> list:
        """오늘 나온 메뉴를 많이 나온 순으로."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT i.menu_name AS menu, COUNT(*) AS n FROM ticket_items i"
                " JOIN tickets t ON t.id = i.ticket_id"
                " WHERE t.business_day = ? AND t.status != ?"
                " GROUP BY i.menu_name ORDER BY n DESC, i.menu_name",
                (day or business_day(), VOID),
            ).fetchall()
        return [{"menu": row["menu"], "count": int(row["n"])} for row in rows]

    def counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT SUM(status = ?) AS open_n, SUM(status = ?) AS ready_n FROM tickets"
                " WHERE status IN (?, ?)",
                (OPEN, READY, OPEN, READY),
            ).fetchone()
        return {"open": int(row["open_n"] or 0), "ready": int(row["ready_n"] or 0)}

    def purge_old(self, now: float | None = None) -> int:
        """보관 기간이 지난 주문서를 항목까지 함께 지운다."""
        cutoff = (now or time.time()) - self.retention_days * 86400
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM ticket_items WHERE ticket_id IN"
                " (SELECT id FROM tickets WHERE received_at < ?)",
                (cutoff,),
            )
            cursor = self._conn.execute("DELETE FROM tickets WHERE received_at < ?", (cutoff,))
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _to_ticket(row: sqlite3.Row) -> Ticket:
        return Ticket(
            id=int(row["id"]), source=row["source"], source_key=row["source_key"],
            business_day=row["business_day"], order_no=row["order_no"],
            table_label=row["table_label"], table_key=row["table_key"],
            ticket_kind=row["ticket_kind"], pos_no=row["pos_no"], station=row["station"],
            received_at=float(row["received_at"]),
            ordered_at=float(row["ordered_at"]) if row["ordered_at"] is not None else None,
            status=row["status"],
            done_at=float(row["done_at"]) if row["done_at"] is not None else None,
            item_count=int(row["item_count"]), served_count=int(row["served_count"]),
            rev=int(row["rev"]),
        )


def _to_item(row: sqlite3.Row) -> Item:
    return Item(
        id=int(row["id"]), line_no=int(row["line_no"]), menu_name=row["menu_name"],
        option_text=row["option_text"], quantity=int(row["quantity"]),
        served=bool(row["served"]),
        served_at=float(row["served_at"]) if row["served_at"] is not None else None,
    )

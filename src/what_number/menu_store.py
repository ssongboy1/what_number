"""주방 주문서에서 읽은 메뉴를 담고, 메뉴 이름으로 주문한 테이블을 찾는다.

같은 주문서가 프린터마다 한 장씩 기록되므로 (영업일, 주문번호) 로 묶는다. 매장에서는
프린터마다 다른 메뉴가 갈 수 있어서, 이미 있는 주문서에 빠진 메뉴만 더한다.
취소 줄이 오면 같은 주문(번호 앞부분)·같은 테이블의 그 메뉴에서 수량을 뺀다.
"""

from __future__ import annotations

import collections
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# 영업일이 바뀌는 시각. 새벽 장사를 전날로 묶는다.
BUSINESS_DAY_START_HOUR = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT    NOT NULL,
    order_no    TEXT    NOT NULL,
    order_base  TEXT    NOT NULL,
    table_label TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT '',
    pos_no      TEXT    NOT NULL DEFAULT '',
    stations    TEXT    NOT NULL DEFAULT '',
    printed_at  REAL    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticket_key  ON tickets(day, order_no);
CREATE INDEX        IF NOT EXISTS idx_ticket_time ON tickets(day, printed_at);

CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER NOT NULL,
    menu       TEXT    NOT NULL,
    menu_key   TEXT    NOT NULL,
    code       TEXT    NOT NULL DEFAULT '',
    options    TEXT    NOT NULL DEFAULT '',
    option_key TEXT    NOT NULL DEFAULT '',
    menu_keys   TEXT   NOT NULL DEFAULT '',
    option_keys TEXT   NOT NULL DEFAULT '',
    quantity   INTEGER NOT NULL,
    cancelled  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_ticket ON items(ticket_id);
CREATE INDEX IF NOT EXISTS idx_items_menu   ON items(menu_key);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_SPACES = re.compile(r"\s+")


def business_day(when: float | None = None) -> str:
    """영업일. 새벽 5시 이전은 전날로 친다."""
    moment = datetime.fromtimestamp(when if when is not None else time.time())
    if moment.hour < BUSINESS_DAY_START_HOUR:
        moment -= timedelta(days=1)
    return moment.strftime("%Y-%m-%d")


def normalize(text: str) -> str:
    """검색용. '감바스 오일' 과 '감바스오일' 을 같게 본다."""
    return _SPACES.sub("", str(text or "")).lower()


# 두벌식 자판. 한글을 영문 상태에서 친 모양으로 바꿔 두면, 한글 입력을 켜지 않고도 찾을 수 있다.
_CHO = "r R s e E f a q Q t T d w W c z x v g".split()
_JUNG = "k o i O j p u P h hk ho hl y n nj np nl b m ml l".split()
_JONG = ["", "r", "R", "rt", "s", "sw", "sg", "e", "f", "fr", "fa", "fq", "ft", "fx", "fv",
         "fg", "a", "q", "qt", "t", "T", "d", "w", "c", "z", "x", "v", "g"]
_JAMO = {
    "ㄱ": "r", "ㄲ": "R", "ㄴ": "s", "ㄷ": "e", "ㄸ": "E", "ㄹ": "f", "ㅁ": "a", "ㅂ": "q",
    "ㅃ": "Q", "ㅅ": "t", "ㅆ": "T", "ㅇ": "d", "ㅈ": "w", "ㅉ": "W", "ㅊ": "c", "ㅋ": "z",
    "ㅌ": "x", "ㅍ": "v", "ㅎ": "g", "ㅏ": "k", "ㅐ": "o", "ㅑ": "i", "ㅒ": "O", "ㅓ": "j",
    "ㅔ": "p", "ㅕ": "u", "ㅖ": "P", "ㅗ": "h", "ㅛ": "y", "ㅜ": "n", "ㅠ": "b", "ㅡ": "m",
    "ㅣ": "l",
}


def keystrokes(text: str) -> str:
    """한글을 영문 자판으로 친 모양으로. '비프' -> 'qlvm'

    포스 PC 에서 한글 입력이 말썽일 때, 영문 상태로 쳐도 메뉴를 찾을 수 있게 한다.
    """
    out = []
    for char in str(text or ""):
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:  # 완성된 한글 글자
            index = code - 0xAC00
            out.append(_CHO[index // 588] + _JUNG[(index % 588) // 28] + _JONG[index % 28])
        elif char in _JAMO:  # ㄱ, ㅏ 같은 낱자
            out.append(_JAMO[char])
        elif not char.isspace():
            out.append(char)
    return "".join(out).lower()


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _split(quantity: int, cancelled: int, show_cancelled: bool) -> list:
    """한 줄을 화면에 어떻게 보여줄지. [(수량, 취소인지)]

    3개 중 1개만 취소했다면 남은 2개와 취소된 1개를 따로 보여준다. 그래야
    '시켰다가 하나 뺐구나' 를 알 수 있다. 취소를 감출 때는 남은 것만 보여준다.
    """
    left = quantity - cancelled
    if not show_cancelled:
        return [(left, False)] if left > 0 else []
    if cancelled <= 0:
        return [(quantity, False)]
    if left <= 0:
        return [(quantity, True)]
    return [(left, False), (cancelled, True)]


class MenuStore:
    def __init__(self, path, retention_hours: float = 48.0):
        self.path = str(path)
        self.retention_hours = retention_hours
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._add_missing_columns()
            self._conn.commit()

    def _add_missing_columns(self) -> None:
        """예전에 만든 파일에 새 칸을 더한다. 다시 켜도 오늘 주문이 그대로 남아 있도록."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(items)")}
        for column in ("option_key", "menu_keys", "option_keys"):
            if column not in have:
                self._conn.execute(
                    "ALTER TABLE items ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % column)

    # --- 주문서 넣기 ---
    def add(self, ticket) -> list:
        """주방 주문서 한 장을 넣는다. 새로 더해진 줄을 [(메뉴, 수량, 옵션)] 으로 돌려준다.

        같은 주문서를 여러 번 넣어도(프린터가 여러 대거나 다시 읽거나) 결과가 같다.
        """
        when = ticket.when
        day = business_day(when)
        order_no = ticket.order_no or "시각%d" % int(when)
        base = order_no.split("-")[0]
        added = []
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id, stations FROM tickets WHERE day = ? AND order_no = ?", (day, order_no)
            ).fetchone()
            if row is None:
                ticket_id = self._conn.execute(
                    "INSERT INTO tickets (day, order_no, order_base, table_label, kind, pos_no,"
                    " stations, printed_at) VALUES (?,?,?,?,?,?,?,?)",
                    (day, order_no, base, ticket.table, ticket.kind, ticket.pos_no,
                     ticket.station, when),
                ).lastrowid
            else:
                ticket_id = row["id"]
                stations = [s for s in row["stations"].split(",") if s]
                if ticket.station and ticket.station not in stations:
                    self._conn.execute(
                        "UPDATE tickets SET stations = ? WHERE id = ?",
                        (",".join(stations + [ticket.station]), ticket_id),
                    )

            have = collections.Counter()
            for item in self._conn.execute(
                "SELECT menu, options, quantity FROM items WHERE ticket_id = ?", (ticket_id,)
            ):
                have[(item["menu"], item["options"], item["quantity"] < 0)] += abs(item["quantity"])

            wanted = collections.Counter()
            codes = {}
            for line in ticket.lines:
                key = (line.menu, ", ".join(line.options), line.quantity < 0)
                wanted[key] += abs(line.quantity)
                codes[key] = line.code

            for key, count in wanted.items():
                missing = count - have[key]
                if missing <= 0:
                    continue
                menu, options, is_cancel = key
                quantity = -missing if is_cancel else missing
                self._conn.execute(
                    "INSERT INTO items (ticket_id, menu, menu_key, code, options, option_key,"
                    " menu_keys, option_keys, quantity) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ticket_id, menu, normalize(menu), codes[key], options, normalize(options),
                     keystrokes(menu), keystrokes(options), quantity),
                )
                if is_cancel:
                    self._cancel(day, base, ticket.table, menu, missing)
                added.append((menu, quantity, options))
        return added

    def _cancel(self, day: str, base: str, table: str, menu: str, count: int) -> None:
        """같은 주문의 같은 메뉴에서 수량을 뺀다. 나중에 들어온 것부터."""
        rows = self._conn.execute(
            "SELECT i.id, i.quantity - i.cancelled AS left FROM items i"
            " JOIN tickets t ON t.id = i.ticket_id"
            " WHERE t.day = ? AND t.order_base = ? AND t.table_label = ? AND i.menu = ?"
            "   AND i.quantity > 0 AND i.quantity > i.cancelled"
            " ORDER BY t.printed_at DESC, i.id DESC",
            (day, base, table, menu),
        ).fetchall()
        for row in rows:
            if count <= 0:
                break
            take = min(count, row["left"])
            self._conn.execute("UPDATE items SET cancelled = cancelled + ? WHERE id = ?", (take, row["id"]))
            count -= take

    # --- 찾기 ---
    def search(self, query: str, day: str | None = None, limit: int = 80,
               cancelled: bool = False) -> list:
        """메뉴 이름 일부로 찾는다. 최근 주문부터.

        cancelled 가 참이면 취소된 것도 함께 돌려준다(취소 표시를 붙여서).
        """
        key = normalize(query)
        if not key:
            return []
        # 메뉴 이름뿐 아니라 옵션에서도 찾는다. 세트 메뉴는 구성품이 옵션으로 들어가므로,
        # 스테이크가 나왔을 때 '스테이크' 로 찾으면 세트 주문도 나와야 한다.
        parts = ["i.menu_key LIKE ? ESCAPE '\\'", "i.option_key LIKE ? ESCAPE '\\'"]
        params = [_like(key), _like(key)]
        if key.isascii():
            # 한글 입력을 켜지 않고 영문 상태로 친 경우. 'qlvm' 도 '비프' 로 찾아준다.
            parts += ["i.menu_keys LIKE ? ESCAPE '\\'", "i.option_keys LIKE ? ESCAPE '\\'"]
            params += [_like(key), _like(key)]
        condition = "(" + " OR ".join(parts) + ")"
        if key.isdigit():
            condition = "(" + condition + " OR i.code = ?)"
            params.append(key)
        left = "" if cancelled else " AND i.quantity > i.cancelled"
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.table_label, t.order_no, t.kind, t.printed_at, t.stations,"
                "       i.menu, i.code, i.options, i.quantity, i.cancelled,"
                "       i.quantity - i.cancelled AS qty"
                " FROM items i JOIN tickets t ON t.id = i.ticket_id"
                " WHERE t.day = ? AND i.quantity > 0" + left + " AND " + condition +
                " ORDER BY t.printed_at DESC, t.id DESC, i.id LIMIT ?",
                [day or business_day()] + params + [limit],
            ).fetchall()
        found = []
        for row in rows:
            base = {
                "table": row["table_label"], "menu": row["menu"], "code": row["code"],
                "options": row["options"], "order_no": row["order_no"],
                "kind": row["kind"], "printed_at": row["printed_at"],
            }
            for quantity, is_cancelled in _split(row["quantity"], row["cancelled"], cancelled):
                line = dict(base)
                line["qty"] = quantity
                line["cancelled"] = is_cancelled
                found.append(line)
        return found

    def menus(self, day: str | None = None) -> list:
        """오늘 주문된 메뉴. 최근에 들어온 것부터."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT i.menu, SUM(i.quantity - i.cancelled) AS count, MAX(t.printed_at) AS last"
                " FROM items i JOIN tickets t ON t.id = i.ticket_id"
                " WHERE t.day = ? AND i.quantity > 0"
                " GROUP BY i.menu HAVING count > 0"
                " ORDER BY last DESC, MAX(t.id) DESC, MIN(i.id)",
                (day or business_day(),),
            ).fetchall()
        return [{"menu": row["menu"], "count": row["count"], "last": row["last"]} for row in rows]

    def recent(self, day: str | None = None, limit: int = 30, cancelled: bool = False) -> list:
        """최근 주문서. 취소된 메뉴는 취소 표시를 달아 함께 돌려준다.

        cancelled 가 거짓이면 통째로 취소된 주문서는 아예 빼고 돌려준다.
        """
        keep_all = " AND i.quantity > i.cancelled" if not cancelled else ""
        with self._lock:
            tickets = self._conn.execute(
                "SELECT t.* FROM tickets t WHERE t.day = ?"
                " AND EXISTS (SELECT 1 FROM items i WHERE i.ticket_id = t.id"
                "             AND i.quantity > 0" + keep_all + ")"
                " ORDER BY t.printed_at DESC, t.id DESC LIMIT ?",
                (day or business_day(), limit),
            ).fetchall()
            result = []
            for ticket in tickets:
                items = self._conn.execute(
                    "SELECT menu, options, quantity, cancelled FROM items"
                    " WHERE ticket_id = ? AND quantity > 0 ORDER BY id",
                    (ticket["id"],),
                ).fetchall()
                rows = []
                for item in items:
                    for quantity, is_cancelled in _split(item["quantity"], item["cancelled"], cancelled):
                        rows.append({
                            "menu": item["menu"], "options": item["options"],
                            "qty": quantity, "cancelled": is_cancelled,
                        })
                result.append({
                    "table": ticket["table_label"], "order_no": ticket["order_no"],
                    "kind": ticket["kind"], "printed_at": ticket["printed_at"], "items": rows,
                })
        return result

    def summary(self, day: str | None = None) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS tickets, MAX(printed_at) AS last FROM tickets WHERE day = ?",
                (day or business_day(),),
            ).fetchone()
        return {"tickets": row["tickets"] or 0, "last_ticket_at": row["last"]}

    # --- 어디까지 읽었는지 ---
    def get_offset(self, name: str):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", ("offset:" + name,)).fetchone()
        return int(row["value"]) if row else None

    def set_offset(self, name: str, offset: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("offset:" + name, str(int(offset))),
            )

    # --- 정리 ---
    def purge_old(self, now: float | None = None) -> int:
        """보관 기간이 지난 주문을 지운다."""
        limit = (now if now is not None else time.time()) - self.retention_hours * 3600
        with self._lock, self._conn:
            old = [row["id"] for row in self._conn.execute(
                "SELECT id FROM tickets WHERE printed_at < ?", (limit,)
            )]
            for ticket_id in old:
                self._conn.execute("DELETE FROM items WHERE ticket_id = ?", (ticket_id,))
                self._conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        return len(old)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

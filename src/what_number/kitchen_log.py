"""VD 포스(PaLiDa)의 주방 인쇄 기록을 따라 읽는다.

포스는 주방 주문서를 인쇄할 때마다 인쇄 내용을 통째로
kitchenPrinter_trace_YYYYMMDD.log 에 적는다. 포스 PC 에 있는 이 파일을 옆에서
읽기만 한다. 포스 설정을 바꾸지 않고, 이 프로그램이 꺼져도 인쇄에는 영향이 없다.

기록 한 건은 이렇게 생겼다(CP949, 줄 끝은 CRLF, 날짜마다 새 파일).

    [20260922 17:21:15.992] [   CTransData::SetPrintOrderData ] ....PrintContents[<ESC/POS 원본>
    ]

ESC/POS 원본은 escpos.parse 로 글자를 뽑은 뒤 아래 모양으로 해석한다.

    신규-주방주문서-홀          구분 - 주방주문서 - 프린터 이름
    [테이블] 홀-2
    [주문번호] 0002-0001
    43. 오븐 토마토 파스        메뉴명 칸이 좁아 글자 중간에서 줄이 바뀐다
    타             1 신규
    ▶순한맛       1 옵션
    POS-01                2026-09-22 17:21:15
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import escpos
from .changes import open_shared

ENCODING = "cp949"
LOG_GLOB = "kitchenPrinter_trace_*.log"

# 포스가 설치되는 곳. 사용자 매장은 C:\PaLiDa 였다.
SEARCH_ROOTS = ("C:\\PaLiDa", "D:\\PaLiDa")
SEARCH_DEPTH = 4

_FILE_NAME = re.compile(r"^kitchenPrinter_trace_(\d{8})\.log$", re.IGNORECASE)
_HEADER = re.compile(rb"^\[(\d{8} \d\d:\d\d:\d\d(?:\.\d+)?)\] \[\s*([^\]\r\n]*?)\s*\] ?", re.MULTILINE)
_CONTENTS = b"....PrintContents["

_TABLE = re.compile(r"\[\s*테이블\s*\]\s*(.+?)\s*$")
_ORDER_NO = re.compile(r"\[\s*주문번호\s*\]\s*([0-9A-Za-z]+(?:-[0-9]+)?)")
_FOOTER = re.compile(r"^(\S+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})$")
_ITEM = re.compile(r"^(?P<name>.*?)\s*(?P<qty>-?\d+)\s+(?P<kind>신규|취소|옵션|추가|서비스|변경|반품)$")
# 주문서 폭을 꽉 채운 선은 칸의 경계, 절반쯤 되는 선은 메뉴 사이의 구분이다.
_SEPARATOR = re.compile(r"^[-=]{30,}$")
_DIVIDER = re.compile(r"^[-=]{5,}$")
_CODE = re.compile(r"^(\d+)\.\s*")


@dataclass
class TicketLine:
    """주문서의 메뉴 한 줄. 취소 줄은 수량이 음수다."""

    menu: str
    quantity: int
    code: str = ""
    options: list = field(default_factory=list)

    @property
    def cancelled(self) -> bool:
        return self.quantity < 0


@dataclass
class KitchenTicket:
    """주방 주문서 한 장."""

    kind: str  # 신규 / 추가
    station: str  # 프린터 이름
    table: str
    order_no: str
    pos_no: str
    printed_at: float | None
    logged_at: float | None
    lines: list
    text: str

    @property
    def when(self) -> float:
        return self.printed_at or self.logged_at or time.time()


# ── 주문서 해석 ──────────────────────────────────────────────────────

def _parse_time(text: str, pattern: str):
    try:
        return datetime.strptime(text, pattern).timestamp()
    except ValueError:
        return None


NAME_COLUMN = 20  # 주문서의 메뉴명 칸 너비(인쇄 칸 수)


def _width(text: str) -> int:
    """인쇄 폭. 한글과 기호는 두 칸을 차지한다."""
    return sum(2 if ord(char) > 0x7F else 1 for char in text)


def join_name(head: str, tail: str, spaced: bool = False) -> str:
    """줄이 바뀌며 잘린 메뉴명을 잇는다.

    포스는 메뉴명 칸(20칸)을 글자 단위로 채우다가 다음 글자가 들어가지 않으면 줄을
    바꾼다(실측: '오븐 토마토 파스' + '타'). 그래서 그냥 이어 붙이면 되지만,
    잘린 자리가 띄어쓰기였다면 그 공백을 되살려야 한다.
    '갈릭 로스트' + '치킨' 을 '갈릭 로스트치킨' 으로 붙이면 안 된다.

    spaced 는 다음 줄이 공백으로 시작했다는 뜻이다(잘린 자리가 띄어쓰기였다).
    그렇지 않더라도 앞줄에 다음 글자가 들어갈 자리가 남아 있었다면 띄어쓰기였다고 본다.
    """
    if not head or not tail:
        return head or tail  # 끝 공백은 그대로 둔다. 다음 조각을 붙일 때 단서가 된다
    if head.endswith(" ") or tail.startswith(" "):
        return head + tail  # 띄어쓰기가 줄 끝이나 줄 앞에 그대로 남아 있다
    if spaced or _width(head) + _width(tail[0]) <= NAME_COLUMN:
        return head + " " + tail
    return head + tail


def _items(rows: list) -> list:
    """메뉴 칸의 줄들을 메뉴 단위로 묶는다."""
    start = next((i for i, row in enumerate(rows) if "수량" in row and "메" in row), None)
    if start is None:
        return []
    index = start + 1
    while index < len(rows) and (not rows[index].strip() or _SEPARATOR.match(rows[index].strip())):
        index += 1

    lines = []
    pending = ""  # 메뉴명 칸이 좁아 다음 줄로 넘어간 앞부분
    for row in rows[index:]:
        text = row.strip()
        if _SEPARATOR.match(text):
            break
        if not text or _DIVIDER.match(text):
            continue
        # 줄 앞뒤의 공백은 버리지 않는다. 메뉴명이 띄어쓰기 자리에서 잘렸다는 표시다.
        spaced = row[:1].isspace()
        found = _ITEM.match(text)
        if found is None:
            pending = join_name(pending, row.lstrip(), spaced)
            continue
        name = join_name(pending, found.group("name").strip(), spaced).strip()
        pending = ""
        quantity = int(found.group("qty"))
        if name.startswith("▶"):
            if lines:
                lines[-1].options.append(name.lstrip("▶").strip())
            continue
        code = _CODE.match(name)
        lines.append(TicketLine(
            menu=name[code.end():].strip() if code else name,
            quantity=quantity,
            code=code.group(1) if code else "",
        ))
    return lines


def parse_ticket(content: bytes, logged_at: float | None = None) -> KitchenTicket | None:
    """주문서 원본 바이트를 해석한다. 주문서가 아니면 None."""
    receipt = escpos.parse(content, ENCODING, keep_spaces=True)
    rows = list(receipt.lines)  # 줄 끝 공백도 메뉴명을 잇는 단서라 그대로 둔다
    texts = [row.strip() for row in rows if row.strip()]
    if not texts:
        return None

    parts = texts[0].split("-")
    kind = parts[0].strip() if len(parts) >= 2 else ""
    station = parts[-1].strip() if len(parts) >= 3 else ""

    table = order_no = pos_no = ""
    printed_at = None
    for text in texts:
        if not table:
            found = _TABLE.search(text)
            if found:
                table = found.group(1)
        if not order_no:
            found = _ORDER_NO.search(text)
            if found:
                order_no = found.group(1)
        found = _FOOTER.match(text)
        if found:
            pos_no = found.group(1)
            printed_at = _parse_time(found.group(2), "%Y-%m-%d %H:%M:%S")

    lines = _items(rows)
    if not lines and not table and not order_no:
        return None
    return KitchenTicket(
        kind=kind, station=station, table=table, order_no=order_no, pos_no=pos_no,
        printed_at=printed_at, logged_at=logged_at, lines=lines, text=receipt.text,
    )


# ── 기록 파일 읽기 ───────────────────────────────────────────────────

def read_entries(data: bytes, final: bool = False) -> tuple:
    """([(기록 시각, 함수 이름, 본문)], 다 읽은 위치).

    포스가 아직 쓰고 있는 부분은 남겨 두고 다음에 다시 읽는다. 줄이 끝나지 않은
    뒷부분, 그리고 닫는 ']' 줄이 아직 오지 않은 주문서 기록이 그렇다.
    final 이면(날짜가 바뀌어 더는 안 쓰이는 파일) 남은 것도 모두 읽는다.
    """
    usable = len(data) if final else data.rfind(b"\n") + 1
    heads = list(_HEADER.finditer(data, 0, usable))
    entries = []
    consumed = usable
    for index, head in enumerate(heads):
        stop = heads[index + 1].start() if index + 1 < len(heads) else usable
        body = data[head.end():stop]
        last = index + 1 == len(heads)
        if last and not final and body.startswith(_CONTENTS) and not _closed(body):
            consumed = head.start()
            break
        entries.append((head.group(1).decode("ascii"), head.group(2).decode(ENCODING, "replace"), body))
    return entries, consumed


def _closed(body: bytes) -> bool:
    """주문서 기록은 ']' 한 글자짜리 줄로 끝난다."""
    return body.rstrip(b"\r\n").endswith(b"\n]")


def ticket_from_entry(stamp: str, body: bytes) -> KitchenTicket | None:
    if not body.startswith(_CONTENTS):
        return None
    content = body[len(_CONTENTS):].rstrip(b"\r\n")
    if content.endswith(b"]"):
        content = content[:-1]
    return parse_ticket(content, _parse_time(stamp[:17], "%Y%m%d %H:%M:%S"))


def find_log_folder(roots=None, depth: int = SEARCH_DEPTH) -> Path | None:
    """주방 기록 파일이 있는 폴더. 여러 곳에 있으면 가장 최근에 쓰인 곳."""
    if roots is None:
        roots = list(SEARCH_ROOTS)
        if os.name == "nt":
            from . import diagnose

            roots += [str(folder) for folder in diagnose.pos_folders()]
    best = None
    for root in roots:
        base = str(root)
        if not os.path.isdir(base):
            continue
        base_depth = base.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(base):
            if dirpath.rstrip("\\/").count(os.sep) - base_depth >= depth:
                dirnames[:] = []
            for name in filenames:
                if not _FILE_NAME.match(name):
                    continue
                try:
                    modified = os.stat(os.path.join(dirpath, name)).st_mtime
                except OSError:
                    continue
                if best is None or modified > best[0]:
                    best = (modified, dirpath)
    return Path(best[1]) if best else None


def log_files(folder) -> list:
    """폴더의 주방 기록 파일. 날짜 순."""
    try:
        names = [entry.name for entry in os.scandir(str(folder)) if entry.is_file()]
    except OSError:
        return []
    return [Path(str(folder)) / name for name in sorted(names, key=str.lower) if _FILE_NAME.match(name)]


class LogFollower:
    """가장 최근 날짜의 주방 기록 파일을 따라 읽는다. 날짜가 바뀌면 새 파일로 넘어간다.

    어디까지 읽었는지를 offsets(get/set) 에 남겨, 다시 켜도 처음부터 읽지 않는다.
    """

    def __init__(self, folder, on_ticket, offsets=None, poll_seconds: float = 1.0):
        self.folder = Path(str(folder))
        self.on_ticket = on_ticket
        self.offsets = offsets
        self.poll_seconds = poll_seconds
        self.current = None
        self.offset = 0
        self.tickets = 0
        self.errors = []
        self.last_read_at = None
        self.last_ticket_at = None
        self._stop = threading.Event()
        self._thread = None

    def poll(self) -> int:
        """한 번 읽는다. 새로 읽은 주문서 수를 돌려준다."""
        files = log_files(self.folder)
        if not files:
            self._error(f"{self.folder} 에 주방 기록 파일이 없습니다")
            return 0
        newest = files[-1]
        count = 0
        if self.current is not None and newest != self.current:
            count += self._read(self.current, final=True)  # 어제 파일의 남은 부분
            self.current = None
        if self.current is None:
            self.current = newest
            saved = self.offsets.get_offset(newest.name) if self.offsets is not None else None
            self.offset = saved or 0
        count += self._read(self.current)
        return count

    def _read(self, path: Path, final: bool = False) -> int:
        try:
            size = os.stat(str(path)).st_size
        except OSError as exc:
            self._error(f"기록 파일을 볼 수 없습니다: {exc}")
            return 0
        if size < self.offset:
            self.offset = 0  # 같은 이름으로 새로 만들어졌다
        if size == self.offset:
            self.last_read_at = time.time()
            return 0
        try:
            with open_shared(path) as handle:
                handle.seek(self.offset)
                data = handle.read(size - self.offset)
        except OSError as exc:
            self._error(f"기록 파일을 읽지 못했습니다: {exc}")
            return 0

        entries, consumed = read_entries(data, final)
        count = 0
        for stamp, _function, body in entries:
            ticket = ticket_from_entry(stamp, body)
            if ticket is None:
                continue
            try:
                self.on_ticket(ticket)
            except Exception as exc:  # 한 장 때문에 따라 읽기가 멈추면 안 된다
                self._error(f"주문서를 저장하지 못했습니다: {exc}")
                continue
            count += 1
            self.last_ticket_at = ticket.when
        self.offset += consumed
        if self.offsets is not None:
            self.offsets.set_offset(path.name, self.offset)
        self.tickets += count
        self.last_read_at = time.time()
        if not any(message.startswith("주문서를") for message in self.errors):
            self.errors = []  # 지금 잘 읽히면 지난 문제는 더 보여주지 않는다
        return count

    def _error(self, message: str) -> None:
        if not self.errors or self.errors[-1] != message:
            self.errors = (self.errors + [message])[-5:]

    # --- 백그라운드 ---
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="kitchen-log", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as exc:
                self._error(f"기록을 읽다가 문제가 생겼습니다: {exc}")
            self._stop.wait(self.poll_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        fresh = self.last_read_at is not None and time.time() - self.last_read_at < 15
        return {
            "ok": fresh and not (self.errors and self.current is None),
            "folder": str(self.folder),
            "file": self.current.name if self.current else "",
            "tickets": self.tickets,
            "last_ticket_at": self.last_ticket_at,
            "errors": self.errors[-3:],
        }

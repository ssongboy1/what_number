"""포스가 없어도 화면이 제대로 뜨는지 확인할 수 있는 시연용 전표 생성기."""

from __future__ import annotations

import random

MENU = [
    ("김치찌개", 9000), ("된장찌개", 9000), ("제육볶음", 12000), ("계란말이", 8000),
    ("공기밥", 1000), ("소주", 5000), ("맥주", 5000), ("파전", 14000),
    ("돈까스", 11000), ("냉면", 10000), ("잡채", 13000), ("불고기", 16000),
]


def _cp949(text: str) -> bytes:
    return text.encode("cp949", errors="replace")


def make_receipt(table: int, order_no: str, items: list[tuple[str, int]]) -> bytes:
    """실제 포스가 보내는 것과 비슷한 형태의 ESC/POS 주문서 바이트."""
    out = bytearray()
    out += b"\x1b@"
    out += b"\x1ba\x01\x1b!\x38" + _cp949("주 방 주 문 서") + b"\n"
    out += b"\x1b!\x00\x1ba\x00"
    out += _cp949("=" * 32) + b"\n"
    out += b"\x1b!\x30" + _cp949(f"테이블 : {table}") + b"\x1b!\x00\n"
    out += _cp949(f"주문번호 : {order_no}") + b"\n"
    out += _cp949("-" * 32) + b"\n"
    for name, qty in items:
        out += _cp949(f"{name:<20}") + _cp949(f"x{qty}") + b"\n"
    out += _cp949("-" * 32) + b"\n"
    out += _cp949(f"합계{'':<18}{sum(q * 9000 for _, q in items):,}") + b"\n"
    out += b"\x1dk\x04" + order_no.encode() + b"\x00"
    out += b"\x1bd\x03\x1dV\x42\x00"
    return bytes(out)


def random_receipt(seq: int) -> bytes:
    table = random.randint(1, 24)
    items = [(name, random.randint(1, 3)) for name, _ in random.sample(MENU, random.randint(1, 4))]
    return make_receipt(table, f"D-{seq:04d}", items)


# ── VD 포스 주방 기록 (kitchenPrinter_trace_날짜.log) ─────────────────

def _width(text: str) -> int:
    """인쇄 폭. 한글과 기호는 두 칸이다."""
    return sum(2 if ord(char) > 0x7F else 1 for char in text)


def _kitchen_rows(name: str, quantity: int, mark: str) -> list:
    """메뉴 한 줄. 포스처럼 메뉴명 칸(20칸)을 넘으면 글자 중간에서 줄을 바꾼다."""
    amount = str(quantity)
    if _width(name) <= 15 - len(amount):
        return [name + " " * (16 - len(amount) - _width(name)) + amount + " " + mark]
    head, used, rest = "", 0, ""
    for index, char in enumerate(name):
        size = 2 if ord(char) > 0x7F else 1
        if used + size > 20:
            rest = name[index:]
            break
        head += char
        used += size
    return [head, rest + " " * max(1, 16 - len(amount) - _width(rest)) + amount + " " + mark]


def kitchen_ticket(table: str, order_no: str, items: list, kind: str = "신규",
                   station: str = "홀", when=None, pos: str = "POS-01") -> bytes:
    """VD 포스가 주방 기록에 남기는 주문서 원본과 같은 모양.

    items 는 [(메뉴, 수량, [옵션, ...])]. 수량이 음수면 취소 줄이 된다.
    """
    from datetime import datetime

    when = when or datetime.now()
    rows = []
    for index, (name, quantity, options) in enumerate(items):
        if index:
            rows.append("-" * 21)
        rows += _kitchen_rows(name, quantity, "취소" if quantity < 0 else "신규")
        for option in options:
            rows += _kitchen_rows("▶" + option, quantity, "취소" if quantity < 0 else "옵션")
    out = bytearray()
    out += b"\x1ba\x01\x1b!0" + _cp949(f"{kind}-주방주문서-{station}") + b"\x1b!\x1e\n"
    out += b"\x1ba\x1e\x1b!0" + _cp949(f"[테이블] {table}") + b"\x1b!\x1e\r\n"
    out += b"\x1b!0" + _cp949(f"[주문번호] {order_no}") + b"\x1b!\x1e\r\n"
    out += b"=" * 42 + b"\r\n"
    out += _cp949("    메  뉴  명                 수량   구분") + b"\r\n"
    out += b"-" * 42 + b"\r\n"
    out += b"\x1b!0" + b"\r\n".join(_cp949(row) for row in rows) + b"\x1b!\x1e\r\n"
    out += b"-" * 42 + b"\r\n"
    out += _cp949(f"{pos:<22}{when:%Y-%m-%d %H:%M:%S}") + b"\r\n\r\n"
    out += b"\x1b@\n\n\n\n\x1bi\r\n"
    return bytes(out)


def _log_head(when, function: str) -> bytes:
    stamp = when.strftime("%Y%m%d %H:%M:%S") + ".%03d" % (when.microsecond // 1000)
    return ("[%s] [%48s ] " % (stamp, function)).encode("ascii")


def kitchen_log_line(when, function: str, message: str) -> bytes:
    """주문서가 아닌 기록 한 줄."""
    return _log_head(when, function) + _cp949(message) + b"\r\n"


def kitchen_log_entry(ticket: bytes, when) -> bytes:
    """주문서 기록 한 건."""
    return _log_head(when, "CTransData::SetPrintOrderData") + b"....PrintContents[" + ticket + b"]\r\n"


def write_sample_kitchen_log(folder, now=None):
    """오늘 날짜의 주방 기록 파일을 만든다. 포스 없이 화면을 확인할 때 쓴다."""
    from datetime import datetime, timedelta
    from pathlib import Path

    now = now or datetime.now()
    orders = [
        (30, "홀-2", "0001-0001", "신규", [("43. 오븐 토마토 파스타", 1, [])]),
        (12, "홀-1", "0002-0001", "신규", [("34. 감바스 오일 파스타", 1, ["(약)"])]),
        (5, "홀-5", "0003-0001", "신규", [("49. 빠네 크림 파스타", 2, ["크림 소스 추가"]),
                                          ("20. 비프 찹 스테이크", 1, ["순한맛"])]),
        (2, "홀-5", "0003-0002", "추가", [("20. 비프 찹 스테이크", -1, ["순한맛"])]),
    ]
    out = bytearray()
    for minutes, table, order_no, kind, items in orders:
        when = now - timedelta(minutes=minutes)
        out += kitchen_log_line(when, "CTransData::SendDataLink", "SendDataLink start - id [1] message [printer]")
        for station in ("홀", "가니"):  # 실제처럼 프린터마다 한 장씩
            out += kitchen_log_entry(kitchen_ticket(table, order_no, items, kind, station, when), when)
            out += kitchen_log_line(when, "CTransData::SetUpdateFlag", "Print SUCCESS")
    path = Path(str(folder)) / f"kitchenPrinter_trace_{now:%Y%m%d}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path

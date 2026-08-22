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

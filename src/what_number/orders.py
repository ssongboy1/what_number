"""전표 텍스트에서 테이블 번호·주문 항목을 뽑아낸다.

포스마다 전표 서식이 다르므로 인식 규칙은 설정에서 바꿀 수 있게 해 둔다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# 앞에 있는 규칙일수록 우선. 각 규칙은 숫자 한 개를 잡아야 한다.
DEFAULT_TABLE_PATTERNS = [
    r"(?:테이블|테이블번호|테이블\s*번호)\s*[:：\-]?\s*(\d{1,3})",
    r"(\d{1,3})\s*번\s*(?:테이블|테이블번호)",
    r"(?:좌석|자리|룸|룸번호|홀)\s*[:：\-]?\s*(\d{1,3})",
    r"\bTABLE\s*[:：#\-]?\s*(\d{1,3})",
    r"\bT\s*[:：#\-]?\s*(\d{1,3})\b",
]

DEFAULT_ORDER_NO_PATTERNS = [
    r"(?:주문번호|주문\s*번호|전표번호|접수번호)\s*[:：\-]?\s*([A-Za-z0-9\-]{1,20})",
    r"(?:ORDER|NO)\s*[:：#\-]\s*([A-Za-z0-9\-]{1,20})",
]

TAKEOUT_MARKERS = ("포장", "테이크아웃", "TAKE OUT", "TAKEOUT")
DELIVERY_MARKERS = ("배달", "배송", "딜리버리", "DELIVERY", "배민", "쿠팡이츠", "요기요")

# 항목 줄에서 "이름 ... 수량" 을 뽑는다. 수량은 줄 끝의 숫자나 x2 형태.
_ITEM_PATTERNS = [
    re.compile(r"^\s*(?P<name>.+?)\s+[xX*]\s?(?P<qty>\d{1,3})\s*$"),
    re.compile(r"^\s*(?P<name>.+?)\s+(?P<qty>\d{1,3})\s*(?:개|EA|ea)\s*$"),
    re.compile(r"^\s*(?P<name>.+?)\s{2,}(?P<qty>\d{1,3})\s*$"),
]

# 항목으로 오해하기 쉬운 줄들
_SKIP_LINE_HINTS = (
    "합계", "총액", "소계", "부가세", "봉사료", "할인", "결제", "카드", "현금",
    "받을금액", "거스름", "영수증", "사업자", "대표", "전화", "주소", "TEL",
    "감사합니다", "주문번호", "테이블", "일시", "시간", "담당",
)


@dataclass
class Order:
    """화면에 보여줄 주문 한 건."""

    table: str | None = None
    order_no: str | None = None
    order_type: str = "홀"  # 홀 / 포장 / 배달
    items: list[tuple[str, int]] = field(default_factory=list)
    raw_text: str = ""
    printed_at: str | None = None  # 전표에 찍힌 시각 문자열

    @property
    def item_summary(self) -> str:
        parts = [name if qty == 1 else f"{name} x{qty}" for name, qty in self.items]
        return ", ".join(parts)


def content_hash(text: str) -> str:
    """같은 전표가 여러 프린터로 나갈 때 하나로 묶기 위한 지문."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def find_table(text: str, patterns: list[str] | None = None) -> str | None:
    for pattern in patterns or DEFAULT_TABLE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).lstrip("0") or "0"
    return None


def find_order_no(text: str, patterns: list[str] | None = None) -> str | None:
    for pattern in patterns or DEFAULT_ORDER_NO_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def find_order_type(text: str) -> str:
    upper = text.upper()
    for marker in DELIVERY_MARKERS:
        if marker.upper() in upper:
            return "배달"
    for marker in TAKEOUT_MARKERS:
        if marker.upper() in upper:
            return "포장"
    return "홀"


def find_printed_at(text: str) -> str | None:
    match = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", text)
    return match.group(1) if match else None


def find_items(text: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 2 or set(stripped) <= set("-=*_~ "):
            continue
        if any(hint in stripped for hint in _SKIP_LINE_HINTS):
            continue
        for pattern in _ITEM_PATTERNS:
            match = pattern.match(stripped)
            if not match:
                continue
            name = match.group("name").strip(" .-·")
            # 금액줄(예: "12,000")을 항목으로 잡지 않도록 걸러낸다.
            if not name or re.fullmatch(r"[\d,.\s원]+", name):
                break
            items.append((name, int(match.group("qty"))))
            break
    return items


def build_order(
    text: str,
    table_patterns: list[str] | None = None,
    order_no_patterns: list[str] | None = None,
) -> Order:
    return Order(
        table=find_table(text, table_patterns),
        order_no=find_order_no(text, order_no_patterns),
        order_type=find_order_type(text),
        items=find_items(text),
        raw_text=text,
        printed_at=find_printed_at(text),
    )

"""주문서가 들어오는 유일한 입구.

프린터든 API든 샘플이든 전부 여기를 지난다. 나중에 실제 주문서를 가져올 때
이 문에 어댑터만 붙이면 되고, 저장소와 화면은 손대지 않는다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .ticket_store import VOID, TicketStore, business_day


@dataclass
class FeedItem:
    """주문서의 메뉴 한 줄."""

    menu_name: str
    quantity: int = 1
    option_text: str = ""

    def as_row(self) -> dict:
        return {"menu": self.menu_name, "qty": self.quantity, "option": self.option_text}


@dataclass
class FeedTicket:
    """주문서 한 장."""

    source: str
    source_key: str
    items: list = field(default_factory=list)
    table_label: str = ""
    order_no: str = ""
    ticket_kind: str = "신규"
    pos_no: str = ""
    station: str = ""
    received_at: float | None = None
    ordered_at: float | None = None


def make_source_key(day: str, order_no: str, fallback: str = "") -> str:
    """같은 주문서가 두 번 들어와도 한 건이 되게 하는 열쇠.

    영수증번호는 날마다 다시 쓰이므로 영업일을 반드시 함께 넣는다.
    """
    tail = order_no.strip() or fallback.strip() or str(time.time())
    return day + ":" + tail


class TicketFeed:
    def __init__(self, store: TicketStore, on_change: Callable[[], None] = lambda: None):
        self.store = store
        self.on_change = on_change
        self.accepted = 0

    def submit(self, ticket: FeedTicket) -> int:
        """주문서를 받아 저장한다. 이미 있으면 기존 id 를 돌려준다."""
        now = ticket.received_at if ticket.received_at is not None else time.time()
        ticket_id = self.store.add_ticket(
            source=ticket.source,
            source_key=ticket.source_key,
            items=[item.as_row() for item in ticket.items],
            table_label=ticket.table_label,
            order_no=ticket.order_no,
            ticket_kind=ticket.ticket_kind,
            pos_no=ticket.pos_no,
            station=ticket.station,
            received_at=now,
            ordered_at=ticket.ordered_at,
            day=business_day(now),
        )
        self.accepted += 1
        self.on_change()
        return ticket_id

    def void(self, source: str, source_key: str) -> bool:
        """취소된 주문서를 화면에서 내린다."""
        found = None
        for ticket in self.store.live_tickets():
            if ticket.source == source and ticket.source_key == source_key:
                found = ticket
                break
        if found is None:
            return False
        self.store.set_status(found.id, VOID)
        self.on_change()
        return True

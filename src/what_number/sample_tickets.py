"""실제 주문서와 비슷한 샘플을 만들어 화면을 시험한다.

포스에서 주문서를 가져오는 기능이 붙기 전까지, 이걸로 태블릿에서 미리 써 본다.
표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import random
import threading
import time

from .sample_menu import MENUS, OPTIONS_BY_MENU, TABLE_FLOORS, TABLE_NUMBERS, TICKET_SIZES, TAKEOUT_LABELS
from .ticket_feed import FeedItem, FeedTicket, make_source_key
from .ticket_store import business_day

_MENU_NAMES = [name for name, _ in MENUS]
_MENU_WEIGHTS = [count for _, count in MENUS]
_SIZES = list(TICKET_SIZES)
_SIZE_WEIGHTS = [TICKET_SIZES[s] for s in _SIZES]


def _pick_option(menu: str) -> str:
    choices = OPTIONS_BY_MENU.get(menu) or [("", 1)]
    names = [name for name, _ in choices]
    weights = [count for _, count in choices]
    return random.choices(names, weights=weights, k=1)[0]


def random_table() -> str:
    """실제 주문서와 같은 표기. 열에 하나쯤은 포장."""
    if random.random() < 0.08:
        return random.choice(TAKEOUT_LABELS)
    return "%s-%d" % (random.choice(TABLE_FLOORS), random.choice(TABLE_NUMBERS))


def random_items(count: int | None = None) -> list:
    size = count or random.choices(_SIZES, weights=_SIZE_WEIGHTS, k=1)[0]
    picked = random.choices(_MENU_NAMES, weights=_MENU_WEIGHTS, k=size)
    items = []
    for menu in picked:
        # 실제 자료에서 수량 1 이 97% 였다
        quantity = 1 if random.random() < 0.97 else random.randint(2, 3)
        items.append(FeedItem(menu_name=menu, quantity=quantity, option_text=_pick_option(menu)))
    return items


def random_ticket(seq: int, table: str | None = None, kind: str = "신규") -> FeedTicket:
    now = time.time()
    table_label = table or random_table()
    order_no = "%04d-%04d" % (seq, 1 if kind == "신규" else 2)
    return FeedTicket(
        source="sample",
        source_key=make_source_key(business_day(now), order_no),
        items=random_items(),
        table_label=table_label,
        order_no=order_no,
        ticket_kind=kind,
        pos_no=random.choice(["POS-01", "POS-04", "POS-05"]),
        station="3가니",
        received_at=now,
        ordered_at=now,
    )


def seed(feed, count: int = 12) -> list:
    """화면을 열자마자 볼 수 있게 몇 장을 미리 넣는다.

    일부는 시간이 지난 것처럼, 일부는 이미 일부 나간 것처럼 만들어
    경과 시간 표시와 체크 상태가 어떻게 보이는지 함께 확인할 수 있게 한다.
    """
    now = time.time()
    created = []
    tables = []
    for i in range(count):
        table = random_table()
        tables.append(table)
        ticket = random_ticket(i + 1, table=table)
        ticket.received_at = now - (count - i) * random.randint(60, 210)
        created.append(feed.submit(ticket))

    # 같은 테이블의 추가주문도 한 건 넣어 둔다. 테이블별 보기를 확인하려면 필요하다.
    if tables:
        extra = random_ticket(count + 1, table=tables[0], kind="추가")
        extra.received_at = now - 90
        created.append(feed.submit(extra))
    return created


class SampleDripper:
    """몇 초에 한 장씩 새 주문서를 흘려보낸다. 실제 영업 흐름을 흉내낸다."""

    def __init__(self, feed, every_seconds: float = 25.0, start_seq: int = 100):
        self.feed = feed
        self.every_seconds = every_seconds
        self.seq = start_seq
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.every_seconds):
            self.seq += 1
            try:
                self.feed.submit(random_ticket(self.seq))
            except Exception:
                pass  # 샘플 생성 실패로 화면이 멈추면 안 된다

    def stop(self) -> None:
        self._stop.set()

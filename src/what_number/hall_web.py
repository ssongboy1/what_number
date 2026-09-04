"""홀 화면의 요청 처리.

소켓 없이도 시험할 수 있게, 모두 (store, ...) 를 받는 평범한 함수로 둔다.
"""

from __future__ import annotations

import json
import re

from .ticket_store import DONE, OPEN, TicketStore, business_day

MAX_BODY = 64 * 1024

# 초성 표
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"

_ITEM_PATH = re.compile(r"^/api/hall/items/(\d+)/served$")
_TICKET_PATH = re.compile(r"^/api/hall/tickets/(\d+)/status$")

_PRIVATE_HOST = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
    r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|\[::1\])(:\d+)?$",
    re.IGNORECASE,
)


def chosung(text: str) -> str:
    """'고르곤졸라 피자' -> 'ㄱㄹㄱㅈㄹ ㅍㅈ'. 태블릿에서 한글 입력 없이 찾기 위한 것."""
    out = []
    for char in text:
        code = ord(char) - 0xAC00
        out.append(_CHO[code // 588] if 0 <= code <= 11171 else char)
    return "".join(out)


def host_allowed(host: str) -> bool:
    """DNS 리바인딩 차단. 사설망 주소로 들어온 요청만 받는다."""
    return bool(host) and bool(_PRIVATE_HOST.match(host.strip()))


def state(store: TicketStore) -> dict:
    tickets = store.live_tickets()
    return {
        "rev": store.rev,
        "tickets": [t.to_dict() for t in tickets],
        "counts": store.counts(),
        "server_time": __import__("time").time(),
    }


def menus(store: TicketStore, day: str | None = None) -> dict:
    rows = store.menu_list(day or business_day())
    for row in rows:
        row["cho"] = chosung(row["menu"])
    return {"menus": rows}


def search(store: TicketStore, menu: str, day: str | None = None) -> dict:
    if not menu.strip():
        return {"tickets": []}
    found = store.search_by_menu(menu.strip(), day or business_day())
    return {"menu": menu, "tickets": [t.to_dict() for t in found]}


def set_served(store: TicketStore, item_id: int, payload: dict) -> tuple:
    if not isinstance(payload.get("served"), bool):
        return 400, {"ok": False, "reason": "served 는 true 또는 false 여야 합니다"}
    result = store.set_served(item_id, payload["served"])
    return (200 if result.get("ok") else 404), result


def set_status(store: TicketStore, ticket_id: int, payload: dict) -> tuple:
    status = payload.get("status")
    if status not in (OPEN, DONE):
        return 400, {"ok": False, "reason": "status 는 open 또는 done 이어야 합니다"}
    result = store.set_status(ticket_id, status)
    return (200 if result.get("ok") else 404), result


def route_post(store: TicketStore, path: str, payload: dict) -> tuple:
    """POST 경로를 명시적인 표로만 받는다."""
    match = _ITEM_PATH.match(path)
    if match:
        return set_served(store, int(match.group(1)), payload)
    match = _TICKET_PATH.match(path)
    if match:
        return set_status(store, int(match.group(1)), payload)
    return 404, {"ok": False, "reason": "없는 주소입니다"}


def parse_body(raw: bytes) -> tuple:
    """요청 몸통을 읽는다. (성공여부, 결과)."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False, {"ok": False, "reason": "JSON 을 읽을 수 없습니다"}
    if not isinstance(payload, dict):
        return False, {"ok": False, "reason": "JSON 객체여야 합니다"}
    return True, payload

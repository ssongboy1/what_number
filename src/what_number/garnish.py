"""메뉴 이름으로 가니쉬를 찾는다.

주문서의 메뉴명에는 (리뷰), [말카드] 같은 접두어나 (행사) 표시가 붙어 있어서
가니쉬 표의 이름과 그대로 맞지 않는다. 이름을 다듬어 맞춘다.
"""

from __future__ import annotations

import re

from .garnish_data import GARNISH

# 메뉴명 앞뒤에 붙는 행사·경로 표시
_TAGS = re.compile(r"[\(\[][^\)\]]*[\)\]]")


def normalize(name: str) -> str:
    """'(리뷰)마늘빵' 과 '마늘빵' 을 같게 본다."""
    cleaned = _TAGS.sub(" ", str(name or ""))
    return "".join(cleaned.split()).lower()


_INDEX = {normalize(menu): menu for menu in GARNISH}


def lookup(name: str) -> dict | None:
    """메뉴에 맞는 가니쉬 안내. 없으면 None."""
    key = normalize(name)
    if not key:
        return None

    found = _INDEX.get(key)
    if found is None:
        # 표의 이름이 주문서 이름에 들어 있거나 그 반대인 경우까지 본다
        best = ""
        for indexed, menu in _INDEX.items():
            if len(indexed) < 4:
                continue
            if (indexed in key or key in indexed) and len(indexed) > len(best):
                best, found = indexed, menu
    if found is None:
        return None

    sprinkle, separate, tools, section = GARNISH[found]
    if not sprinkle and not separate and not tools:
        return None
    return {
        "menu": found,
        "sprinkle": list(sprinkle),
        "separate": list(separate),
        "tools": tools,
        "section": section,
    }


def has_garnish(name: str) -> bool:
    return lookup(name) is not None

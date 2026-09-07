"""가니쉬 표를 읽어 src/what_number/garnish_data.py 를 만든다.

개발용 도구다. openpyxl 을 쓰므로 src/ 밖에 두고 exe 에는 넣지 않는다.

원본은 좌우 두 덩어리가 나란히 있는 표다.
  왼쪽:  번호 | 메뉴명 | (빈칸) | 가니쉬 | (빈칸) | 집기
  오른쪽: 번호 | 메뉴명 | (빈칸) | 가니쉬 | (빈칸) | 집기
가니쉬 표기에서 ▶ 는 뿌리는 순서, ● 는 별도 제공을 뜻한다.

    py tools/build_garnish.py "2026년 라라코스트 가니쉬(260319).xlsx"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl 이 필요합니다:  py -m pip install openpyxl")

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "what_number" / "garnish_data.py"

SHEET = "일반"  # 파베이크 시트는 쓰지 않는다
BLOCKS = ((0, 1, 2, 3), (5, 6, 7, 8))  # (번호, 메뉴명, 가니쉬, 집기) 열 위치

HEADER = '''"""메뉴별 가니쉬 안내.

tools/build_garnish.py 가 자동으로 만든 파일이다. 직접 고치지 말 것.
sprinkle: 뿌리는 순서대로 / separate: 별도로 내는 것 / tools: 집기
"""

'''


def split_garnish(text: str) -> tuple:
    """'파슬리 ▶ 어린잎 / ● 콘샐러드' 를 뿌리는 순서와 별도 제공으로 나눈다.

    칸 값 앞에 세로줄 표시가 붙어 있어 먼저 떼어낸다.
    '불고기 : 버거피자소스' 처럼 종류에 따라 달라지는 표기는 쪼개지 않고
    문장 그대로 남긴다. 잘못 쪼개면 엉뚱한 안내가 되기 때문이다.
    """
    raw = str(text or "").replace("\n", " ").strip()
    raw = raw.lstrip("|").strip()
    cleaned = " ".join(raw.split())
    if not cleaned:
        return [], []

    sprinkle: list = []
    separate: list = []
    for chunk in re.split(r"\s*/\s*", cleaned):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "●" in chunk:  # 별도 제공
            for part in chunk.split("●"):
                part = part.strip(" ,")
                if not part:
                    continue
                if ":" in part:
                    separate.append(part)
                else:
                    separate.extend(p.strip() for p in part.split(",") if p.strip())
        elif ":" in chunk:  # 종류별로 다른 안내는 통째로 둔다
            sprinkle.append(chunk)
        else:
            sprinkle.extend(p.strip() for p in chunk.split("▶") if p.strip())
    return sprinkle, separate


def build(path: str) -> str:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[SHEET]

    entries: dict = {}
    section_of: dict = {}
    sections = ["", ""]

    for row in sheet.iter_rows(values_only=True):
        cells = list(row) + [None] * 13
        for block_index, (c_no, c_name, c_garnish, c_tools) in enumerate(BLOCKS):
            no = cells[c_no]
            name = cells[c_name]
            # 번호 없이 이름만 있으면 구역 제목(SALAD, PASTA 등)이다
            if name is None and isinstance(no, str) and no.strip() and not no.strip().isdigit():
                if "가니쉬" in no or "집기" in no:
                    continue
                sections[block_index] = no.strip()
                continue
            numbered = isinstance(no, (int, float)) or (
                isinstance(no, str) and no.strip().isdigit())
            if not numbered or not isinstance(name, str):
                continue
            menu = " ".join(name.split())
            if not menu:
                continue
            sprinkle, separate = split_garnish(cells[c_garnish])
            tools = " ".join(str(cells[c_tools] or "").lstrip("| ").split())
            entries[menu] = (sprinkle, separate, tools)
            section_of[menu] = sections[block_index]

    out = [HEADER]
    out.append("# 메뉴 이름 -> (뿌리는 순서, 별도 제공, 집기, 구역)\n")
    out.append("GARNISH = {\n")
    for menu in sorted(entries):
        sprinkle, separate, tools = entries[menu]
        out.append("    %r: (%r, %r, %r, %r),\n" % (
            menu, sprinkle, separate, tools, section_of.get(menu, "")))
    out.append("}\n")

    with_garnish = sum(1 for v in entries.values() if v[0] or v[1])
    print("메뉴 %d개 중 가니쉬가 적힌 것 %d개" % (len(entries), with_garnish))
    for menu in list(sorted(entries))[:5]:
        print("   ", menu, "->", entries[menu])
    return "".join(out)


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else None
    if not source:
        found = sorted(ROOT.glob("*가니쉬*.xlsx"))
        if not found:
            print(__doc__)
            return 1
        source = str(found[-1])
        print("자료:", source)
    OUTPUT.write_text(build(source), encoding="utf-8")
    print("만들었습니다:", OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

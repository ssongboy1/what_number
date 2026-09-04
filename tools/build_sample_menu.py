"""엑셀에서 메뉴 카탈로그를 뽑아 src/what_number/sample_menu.py 를 만든다.

개발용 도구다. openpyxl 을 쓰므로 src/ 밖에 두고 exe 에는 절대 넣지 않는다.
만들어지는 카탈로그에는 메뉴 이름과 옵션, 주문서 크기 분포만 담는다.
금액, 날짜, 매장명, 영수증번호는 담지 않는다. 매출 자료가 아니라 메뉴 목록이다.

    py tools/build_sample_menu.py 칠곡VD조회_20260716_20260831.xlsx
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl 이 필요합니다:  py -m pip install openpyxl")

TOP_MENUS = 129
TOP_OPTIONS_PER_MENU = 6
OUTPUT = Path(__file__).resolve().parents[1] / "src" / "what_number" / "sample_menu.py"

HEADER = '''"""샘플 주문서를 만들기 위한 메뉴 카탈로그.

tools/build_sample_menu.py 가 자동으로 만든 파일이다. 직접 고치지 말 것.
메뉴 이름과 옵션, 주문서 크기 분포만 들어 있다. 금액과 매출 정보는 없다.
"""

'''


def build(xlsx_path: str) -> str:
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook["주문내역"]
    rows = sheet.iter_rows(values_only=True)
    column = {name: i for i, name in enumerate(next(rows))}

    menu_counts: collections.Counter = collections.Counter()
    options: dict = {}
    tickets: dict = {}
    tables: collections.Counter = collections.Counter()

    for row in rows:
        if row[column["매출구분"]] != "SALE":
            continue
        menu = str(row[column["메뉴명"]] or "").strip()
        if not menu:
            continue
        option = str(row[column["옵션조합"]] or "").strip()
        if option in ("(옵션 없음)", "(옵션없음)"):
            option = ""

        menu_counts[menu] += 1
        options.setdefault(menu, collections.Counter())[option] += 1
        tables[str(row[column["테이블"]] or "").strip()] += 1

        key = (str(row[column["날짜"]])[:10], row[column["영수증번호"]])
        tickets.setdefault(key, 0)
        tickets[key] += 1

    top_menus = menu_counts.most_common(TOP_MENUS)
    sizes = collections.Counter(tickets.values())

    out = [HEADER]
    out.append("# (메뉴 이름, 나온 횟수)\n")
    out.append("MENUS = [\n")
    for name, count in top_menus:
        out.append("    (%r, %d),\n" % (name, count))
    out.append("]\n\n")

    out.append("# 메뉴별로 실제로 붙었던 옵션 (옵션, 횟수). 빈 문자열은 옵션 없음\n")
    out.append("OPTIONS_BY_MENU = {\n")
    for name, _ in top_menus:
        picks = options[name].most_common(TOP_OPTIONS_PER_MENU)
        out.append("    %r: [%s],\n" % (name, ", ".join("(%r, %d)" % p for p in picks)))
    out.append("}\n\n")

    out.append("# 주문서 한 장에 메뉴가 몇 줄이었는지의 분포 {줄수: 주문서수}\n")
    out.append("TICKET_SIZES = {%s}\n\n" % ", ".join(
        "%d: %d" % (size, n) for size, n in sorted(sizes.items()) if size <= 12
    ))

    out.append("# 테이블 이름은 실제 주문서 형식(2층-21)을 따른다\n")
    out.append("TABLE_FLOORS = ['1층', '2층']\n")
    out.append("TABLE_NUMBERS = list(range(1, 31))\n")
    out.append("TAKEOUT_LABELS = ['포장1', '포장2', '포장3']\n")

    print("메뉴 %d종, 주문서 %d장에서 뽑았습니다." % (len(top_menus), len(tickets)))
    print("테이블 표기 예시:", ", ".join(list(tables)[:4]))
    return "".join(out)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build(sys.argv[1]), encoding="utf-8")
    print("만들었습니다:", OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

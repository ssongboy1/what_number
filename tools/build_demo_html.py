"""서버 없이 파일 하나로 열어 보는 예시 화면을 만든다.

실제 화면(hall_page.PAGE)을 그대로 쓰고, 서버와 주고받는 부분만 브라우저 안에서
흉내내도록 바꿔 끼운다. 그래서 예시와 실제 화면이 어긋나지 않는다.

    py tools/build_demo_html.py
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from what_number.hall_page import PAGE  # noqa: E402
from what_number.hall_web import chosung  # noqa: E402
from what_number.sample_tickets import random_items, random_table  # noqa: E402

OUTPUT = ROOT / "홀주문서_예시.html"
TICKET_COUNT = 14


def make_tickets() -> list:
    """예시로 보여줄 주문서. 시간이 제각각이라 경과 표시가 어떻게 보이는지 알 수 있다."""
    random.seed(20260904)
    now = time.time()
    tickets = []
    item_id = 1
    tables = []

    for index in range(TICKET_COUNT):
        table = random_table()
        tables.append(table)
        # 오래된 것부터 최근 것까지 골고루
        received = now - (TICKET_COUNT - index) * random.randint(70, 200)
        rows = []
        for line_no, item in enumerate(random_items(), start=1):
            rows.append({
                "id": item_id,
                "line_no": line_no,
                "menu": item.menu_name,
                "option": item.option_text,
                "qty": item.quantity,
                "served": False,
                "served_at": None,
            })
            item_id += 1
        tickets.append({
            "id": index + 1,
            "order_no": "%04d-0001" % (index + 1),
            "table": table,
            "table_key": "".join(table.split()).replace("-", "").lower(),
            "kind": "신규",
            "pos": "POS-01",
            "station": "3가니",
            "received_at": received,
            "ordered_at": received,
            "status": "open",
            "item_count": len(rows),
            "served_count": 0,
            "rev": index + 1,
            "items": rows,
        })

    # 같은 테이블의 추가주문 한 장. 테이블별 보기를 확인하려면 필요하다.
    if tables:
        rows = []
        for line_no, item in enumerate(random_items(2), start=1):
            rows.append({
                "id": item_id, "line_no": line_no, "menu": item.menu_name,
                "option": item.option_text, "qty": item.quantity,
                "served": False, "served_at": None,
            })
            item_id += 1
        tickets.append({
            "id": TICKET_COUNT + 1, "order_no": "0001-0002", "table": tables[0],
            "table_key": "".join(tables[0].split()).replace("-", "").lower(),
            "kind": "추가", "pos": "POS-05", "station": "3가니",
            "received_at": now - 120, "ordered_at": now - 120, "status": "open",
            "item_count": len(rows), "served_count": 0, "rev": TICKET_COUNT + 1,
            "items": rows,
        })

    tickets.sort(key=lambda t: t["received_at"])
    return tickets


def menu_rows(tickets: list) -> list:
    counts: dict = {}
    for ticket in tickets:
        for item in ticket["items"]:
            counts[item["menu"]] = counts.get(item["menu"], 0) + 1
    rows = [{"menu": m, "count": n, "cho": chosung(m)} for m, n in counts.items()]
    rows.sort(key=lambda r: (-r["count"], r["menu"]))
    return rows


# 브라우저 안에서 서버 노릇을 하는 조각. 실제 서버와 같은 규칙으로 답한다.
SHIM = """
<script>
// ---- 예시 파일용. 서버 대신 브라우저 안에서 답한다 ----
try { localStorage.setItem("_t", "1"); localStorage.removeItem("_t"); }
catch (e) {
  const mem = {};
  Object.defineProperty(window, "localStorage", {value: {
    getItem: k => (k in mem ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: k => { delete mem[k]; },
  }});
}

const DEMO = {tickets: __TICKETS__, menus: __MENUS__, rev: 100};
const DEMO_START = JSON.parse(JSON.stringify(DEMO.tickets));

function demoFindItem(id) {
  for (const t of DEMO.tickets) {
    for (const i of t.items) if (i.id === id) return [t, i];
  }
  return [null, null];
}
function demoRecount(t) {
  t.item_count = t.items.length;
  t.served_count = t.items.filter(i => i.served).length;
  if (t.status === "open" || t.status === "ready") {
    t.status = (t.item_count && t.served_count >= t.item_count) ? "ready" : "open";
  }
  DEMO.rev += 1;
  t.rev = DEMO.rev;
}
function demoReset() {
  DEMO.tickets = JSON.parse(JSON.stringify(DEMO_START));
  DEMO.rev += 1;
  if (typeof refresh === "function") refresh();
}

window.fetch = async function (url, options) {
  const path = String(url);
  const body = options && options.body ? JSON.parse(options.body) : null;
  let payload = {};

  if (path.indexOf("/api/hall/state") === 0) {
    const live = DEMO.tickets.filter(t => t.status === "open" || t.status === "ready");
    live.sort((a, b) => a.received_at - b.received_at);
    payload = {rev: DEMO.rev, tickets: live, counts: {open: live.length, ready: 0}};
  } else if (path.indexOf("/api/hall/menus") === 0) {
    payload = {menus: DEMO.menus};
  } else if (path.indexOf("/api/hall/search") === 0) {
    const wanted = decodeURIComponent(path.split("menu=")[1] || "");
    const hits = DEMO.tickets
      .filter(t => t.status !== "void" && t.items.some(i => i.menu.indexOf(wanted) >= 0))
      .slice()
      .sort((a, b) => b.received_at - a.received_at);
    payload = {menu: wanted, tickets: hits};
  } else if (/\\/api\\/hall\\/items\\/(\\d+)\\/served/.test(path)) {
    const id = Number(path.match(/items\\/(\\d+)/)[1]);
    const [ticket, item] = demoFindItem(id);
    if (!ticket) return new Response("{}", {status: 404});
    const wasReady = ticket.status === "ready";
    item.served = !!body.served;
    item.served_at = item.served ? Date.now() / 1000 : null;
    demoRecount(ticket);
    payload = {ok: true, ticket_id: ticket.id, status: ticket.status,
               served_count: ticket.served_count, item_count: ticket.item_count,
               became_ready: ticket.status === "ready" && !wasReady};
  } else if (/\\/api\\/hall\\/tickets\\/(\\d+)\\/status/.test(path)) {
    const id = Number(path.match(/tickets\\/(\\d+)/)[1]);
    const ticket = DEMO.tickets.find(t => t.id === id);
    if (!ticket) return new Response("{}", {status: 404});
    ticket.status = body.status;
    DEMO.rev += 1;
    ticket.rev = DEMO.rev;
    if (body.status === "open") demoRecount(ticket);
    payload = {ok: true, ticket_id: id, status: ticket.status};
  } else {
    return new Response("{}", {status: 404});
  }
  return new Response(JSON.stringify(payload),
    {status: 200, headers: {"Content-Type": "application/json"}});
};
</script>
"""

BADGE = """
<script>
window.addEventListener("load", function () {
  const h = document.querySelector("header h1");
  h.insertAdjacentHTML("afterend",
    '<span style="font-size:13px;font-weight:800;padding:4px 9px;border-radius:7px;' +
    'background:rgba(255,201,92,.18);color:#ffc95c">예시</span>');
  const find = document.querySelector(".find");
  find.insertAdjacentHTML("beforebegin",
    '<button onclick="demoReset()" style="min-height:52px;padding:0 16px;border-radius:11px;' +
    'background:#2b3446;font-size:15px;font-weight:700">처음으로</button>');
});
</script>
"""


def main() -> int:
    tickets = make_tickets()
    shim = (
        SHIM.replace("__TICKETS__", json.dumps(tickets, ensure_ascii=False))
        .replace("__MENUS__", json.dumps(menu_rows(tickets), ensure_ascii=False))
    )
    page = PAGE.replace("<script>", shim + BADGE + "<script>", 1)
    OUTPUT.write_text(page, encoding="utf-8")
    total = sum(len(t["items"]) for t in tickets)
    print("만들었습니다:", OUTPUT)
    print("  주문서 %d장 / 메뉴 %d줄 / %.0f KB" % (
        len(tickets), total, OUTPUT.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())

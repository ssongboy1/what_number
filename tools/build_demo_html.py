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
from what_number.hall_web import chosung, garnish_table  # noqa: E402
from what_number.sample_tickets import random_items, random_table  # noqa: E402

OUTPUT = ROOT / "홀주문서_예시.html"
# 처음 화면은 주문서 3장으로 시작한다.
# 색이 바뀌는 지점(빨강 20분 초과 / 노랑 10분 초과 / 그 아래 기본)에 하나씩 둔다.
START_MINUTES = (21, 11, 1)


def make_tickets() -> list:
    """예시로 보여줄 주문서 3장. 경과 시간 색을 하나씩 볼 수 있게 배치한다."""
    random.seed(20260904)
    now = time.time()
    tickets = []
    item_id = 1
    tables = []

    for index, minutes in enumerate(sorted(START_MINUTES, reverse=True)):
        table = random_table()
        tables.append(table)
        received = now - minutes * 60
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
            "age_min": minutes,
            "status": "open",
            "item_count": len(rows),
            "served_count": 0,
            "rev": index + 1,
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

const DEMO = {tickets: __TICKETS__, menus: __MENUS__, garnish: __GARNISH__, rev: 100,
              pool: __POOL__, tables: __TABLES__, nextId: 9000, nextItem: 90000, auto: null};
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
function demoPick(list) { return list[Math.floor(Math.random() * list.length)]; }

function demoAddTicket(table) {
  // 가끔은 이미 있는 테이블의 추가주문으로 만든다. 테이블별 보기를 보려면 필요하다.
  if (!table && Math.random() < 0.25) {
    const live = DEMO.tickets.filter(t => t.status === "open" || t.status === "ready");
    if (live.length) table = demoPick(live).table;
  }
  const size = 1 + Math.floor(Math.random() * 4);
  const items = [];
  for (let i = 0; i < size; i++) {
    const entry = demoPick(DEMO.pool);
    items.push({
      id: DEMO.nextItem++, line_no: i + 1, menu: entry[0],
      option: entry[1].length ? demoPick(entry[1]) : "",
      qty: Math.random() < 0.97 ? 1 : 2, served: false, served_at: null,
    });
  }
  const label = table || demoPick(DEMO.tables);
  const id = DEMO.nextId++;
  DEMO.rev += 1;
  DEMO.tickets.push({
    id: id, order_no: String(id) + "-0001", table: label,
    table_key: label.replace(/[\\s-]/g, "").toLowerCase(),
    kind: table ? "추가" : "신규", pos: "POS-01", station: "3가니",
    received_at: Date.now() / 1000, ordered_at: Date.now() / 1000,
    status: "open", item_count: items.length, served_count: 0,
    rev: DEMO.rev, items: items,
  });
  return id;
}

// 한 장씩 시간차를 두고 들어오게 한다. 실제로는 이렇게 몰려 들어온다.
function demoBurst(count, gapMs) {
  let n = 0;
  const step = () => {
    demoAddTicket();
    if (typeof refresh === "function") refresh();
    if (++n < count) setTimeout(step, gapMs || 1200);
  };
  step();
}

function demoAuto() {
  const btn = document.getElementById("demoAuto");
  if (DEMO.auto) {
    clearInterval(DEMO.auto); DEMO.auto = null;
    btn.textContent = "자동";
    btn.style.background = "#2b3446";
    btn.style.color = "#f4f7fb";
  } else {
    DEMO.auto = setInterval(() => { demoAddTicket(); refresh(); }, 6000);
    btn.textContent = "자동 켜짐";
    btn.style.background = "#4ade80";
    btn.style.color = "#0d2417";
  }
}

function demoReset() {
  if (DEMO.auto) { clearInterval(DEMO.auto); DEMO.auto = null; }
  DEMO.tickets = JSON.parse(JSON.stringify(DEMO_START));
  // 시각도 처음 상태로 되돌린다. 안 그러면 계속 오래된 주문서로 남는다.
  const now = Date.now() / 1000;
  for (const t of DEMO.tickets) {
    if (typeof t.age_min === "number") {
      t.received_at = now - t.age_min * 60;
      t.ordered_at = t.received_at;
    }
  }
  DEMO.rev += 1;
  const btn = document.getElementById("demoAuto");
  if (btn) { btn.textContent = "자동"; btn.style.background = "#2b3446"; btn.style.color = "#f4f7fb"; }
  if (typeof refresh === "function") { seenIds = null; refresh(); }
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
  } else if (path.indexOf("/api/hall/garnish") === 0) {
    payload = {garnish: DEMO.garnish};
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
  // 시험용 단추는 남은 메뉴 표시와 보기 토글 사이에 둔다
  const style = "height:34px;padding:0 11px;border-radius:9px;background:#2b3446;" +
                "font-size:13px;font-weight:700;white-space:nowrap;color:#f4f7fb;" +
                "border:1px solid #3a465e";
  document.querySelector("header .spacer").insertAdjacentHTML("afterend",
    '<span style="font-size:11px;color:#93a1b8;font-weight:700">시험용</span>' +
    '<button onclick="demoAddTicket()" style="' + style + '">주문 1건</button>' +
    '<button onclick="demoBurst(5)" style="' + style + '">연속 5건</button>' +
    '<button id="demoAuto" onclick="demoAuto()" style="' + style + '">자동</button>' +
    '<button onclick="demoReset()" style="' + style + '">처음으로</button>' +
    '<span style="flex:1"></span>');
});
</script>
"""


def main() -> int:
    tickets = make_tickets()
    from what_number.sample_menu import MENUS, OPTIONS_BY_MENU

    pool = [
        [name, [opt for opt, _ in OPTIONS_BY_MENU.get(name, []) if opt]]
        for name, _ in MENUS[:60]
    ]
    tables = ["%s-%d" % (floor, n) for floor in ("1층", "2층") for n in range(1, 31)]
    tables += ["포장1", "포장2", "포장3"]

    shim = (
        SHIM.replace("__TICKETS__", json.dumps(tickets, ensure_ascii=False))
        .replace("__MENUS__", json.dumps(menu_rows(tickets), ensure_ascii=False))
        .replace("__POOL__", json.dumps(pool, ensure_ascii=False))
        .replace("__TABLES__", json.dumps(tables, ensure_ascii=False))
        .replace("__GARNISH__",
                 json.dumps(garnish_table()["garnish"], ensure_ascii=False))
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

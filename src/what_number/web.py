"""최근 주문을 보여주는 로컬 웹 화면. 파이썬 기본 웹서버만 사용한다."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .store import OrderStore

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>최근 주문</title>
<style>
  :root {
    --bg: #10131a; --card: #1a1f2b; --card-alt: #202636; --line: #2c3446;
    --text: #f2f5fa; --muted: #8e9bb3; --accent: #ffc95c; --accent-ink: #2a1f00;
    --hall: #5ec5ff; --takeout: #7ee6a8; --delivery: #ff9d7a;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif;
    padding-bottom: 40px;
  }
  header {
    position: sticky; top: 0; z-index: 5; background: rgba(16,19,26,.96);
    border-bottom: 1px solid var(--line); padding: 12px 16px 10px;
    backdrop-filter: blur(8px);
  }
  .bar { display: flex; align-items: center; gap: 10px; }
  h1 { font-size: 19px; margin: 0; font-weight: 700; letter-spacing: -.02em; }
  .status { margin-left: auto; font-size: 12.5px; color: var(--muted); display: flex;
            align-items: center; gap: 6px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #4ade80; }
  .dot.off { background: #f87171; }
  .filters { display: flex; gap: 6px; overflow-x: auto; margin-top: 10px;
             padding-bottom: 2px; scrollbar-width: none; }
  .filters::-webkit-scrollbar { display: none; }
  .chip {
    flex: 0 0 auto; padding: 7px 13px; border-radius: 999px; border: 1px solid var(--line);
    background: var(--card); color: var(--muted); font-size: 14px; font-weight: 600;
    cursor: pointer;
  }
  .chip.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  main { padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; }
  .order {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 13px 15px; cursor: pointer;
  }
  .order.fresh { border-color: var(--accent); }
  .row { display: flex; align-items: baseline; gap: 12px; }
  .table-no {
    font-size: 40px; font-weight: 800; line-height: 1; letter-spacing: -.03em;
    min-width: 76px;
  }
  .table-no small { font-size: 15px; font-weight: 600; color: var(--muted); margin-left: 3px; }
  .table-no.unknown { font-size: 20px; color: var(--muted); font-weight: 700; padding-top: 8px; }
  .meta { margin-left: auto; text-align: right; font-size: 13px; color: var(--muted);
          line-height: 1.5; white-space: nowrap; }
  .ago { font-size: 16px; font-weight: 700; color: var(--text); }
  .tag { display: inline-block; font-size: 11.5px; font-weight: 700; padding: 2px 7px;
         border-radius: 5px; vertical-align: 2px; }
  .tag.홀 { background: rgba(94,197,255,.16); color: var(--hall); }
  .tag.포장 { background: rgba(126,230,168,.16); color: var(--takeout); }
  .tag.배달 { background: rgba(255,157,122,.16); color: var(--delivery); }
  .items { margin-top: 8px; font-size: 15.5px; line-height: 1.5; color: #dbe3f0;
           word-break: break-all; }
  .items.none { color: var(--muted); font-size: 14px; }
  .raw { display: none; margin-top: 10px; padding: 11px 12px; background: var(--card-alt);
         border-radius: 9px; font-family: Consolas, "D2Coding", monospace; font-size: 12.5px;
         white-space: pre-wrap; word-break: break-all; color: #c8d2e3; line-height: 1.5; }
  .order.open .raw { display: block; }
  .warn { margin-top: 8px; font-size: 13px; color: var(--accent); }
  .empty { text-align: center; color: var(--muted); padding: 60px 24px; line-height: 1.9;
           font-size: 14.5px; }
  .empty b { color: var(--text); display: block; font-size: 16px; margin-bottom: 10px; }
</style>
</head>
<body>
<header>
  <div class="bar">
    <h1>최근 주문</h1>
    <div class="status"><span class="dot" id="dot"></span><span id="statusText">연결 중</span></div>
  </div>
  <div class="filters" id="filters"></div>
</header>
<main id="list"></main>
<script>
let currentTable = "";
let openIds = new Set();

function ago(ts) {
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (sec < 60) return sec + "초 전";
  const min = Math.floor(sec / 60);
  if (min < 60) return min + "분 전";
  const hour = Math.floor(min / 60);
  return hour + "시간 " + (min % 60) + "분 전";
}

function clockOf(ts) {
  const d = new Date(ts * 1000);
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function render(orders) {
  const list = document.getElementById("list");
  if (!orders.length) {
    list.innerHTML = '<div class="empty"><b>아직 들어온 주문이 없습니다</b>' +
      '포스에서 주문을 하나 넣어보세요.<br>주문서가 프린터로 나가는 순간 여기에 뜹니다.</div>';
    return;
  }
  const now = Date.now() / 1000;
  list.innerHTML = orders.map(o => {
    const fresh = now - o.first_seen < 180;
    const open = openIds.has(o.id);
    const tableHtml = o.table
      ? '<div class="table-no">' + escapeHtml(o.table) + '<small>번</small></div>'
      : '<div class="table-no unknown">번호 미확인</div>';
    const items = o.items
      ? '<div class="items">' + escapeHtml(o.items) + '</div>'
      : '<div class="items none">항목 인식 안 됨 · 눌러서 원본 보기</div>';
    const warn = o.has_raster
      ? '<div class="warn">이 주문서는 이미지로 전송되어 글자를 읽을 수 없습니다</div>' : '';
    const copies = o.copies > 1 ? o.copies + '장' : '';
    return '<div class="order ' + (fresh ? 'fresh ' : '') + (open ? 'open' : '') +
      '" onclick="toggle(' + o.id + ')">' +
      '<div class="row">' + tableHtml +
      '<div class="meta"><div class="ago">' + ago(o.first_seen) + '</div>' +
      clockOf(o.first_seen) + ' <span class="tag ' + escapeHtml(o.order_type) + '">' +
      escapeHtml(o.order_type) + '</span> ' + copies + '</div></div>' +
      items + warn +
      '<div class="raw">' + escapeHtml(o.raw_text) + '</div></div>';
  }).join("");
}

function renderFilters(tables) {
  const box = document.getElementById("filters");
  const chips = ['<div class="chip ' + (currentTable === "" ? "on" : "") +
    '" onclick="pick(\\'\\')">전체</div>'];
  for (const t of tables) {
    chips.push('<div class="chip ' + (currentTable === t ? "on" : "") +
      '" onclick="pick(\\'' + t + '\\')">' + escapeHtml(t) + '번</div>');
  }
  box.innerHTML = chips.join("");
}

function pick(t) { currentTable = t; refresh(); }
function toggle(id) {
  if (openIds.has(id)) openIds.delete(id); else openIds.add(id);
  refresh();
}

async function refresh() {
  try {
    const query = currentTable ? "?table=" + encodeURIComponent(currentTable) : "";
    const res = await fetch("/api/orders" + query, { cache: "no-store" });
    const data = await res.json();
    render(data.orders);
    renderFilters(data.tables);
    const dot = document.getElementById("dot");
    dot.className = "dot" + (data.capturing ? "" : " off");
    document.getElementById("statusText").textContent =
      data.capturing ? ("감시 중 · 주문 " + data.total + "건") : "감시 중지됨";
  } catch (e) {
    document.getElementById("dot").className = "dot off";
    document.getElementById("statusText").textContent = "프로그램 연결 끊김";
  }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "WhatNumber"
    store: OrderStore
    status_provider = staticmethod(lambda: {})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        elif parsed.path == "/api/orders":
            self._send_json(self._orders(parse_qs(parsed.query)))
        elif parsed.path == "/api/status":
            self._send_json(self.status_provider())
        elif parsed.path == "/favicon.ico":
            self._send(204, "text/plain", b"")
        else:
            self._send(404, "text/plain; charset=utf-8", "없는 주소입니다".encode("utf-8"))

    def _orders(self, query: dict) -> dict:
        table = (query.get("table") or [""])[0] or None
        limit = min(int((query.get("limit") or ["60"])[0] or 60), 300)
        orders = self.store.recent(limit=limit, table=table)
        payload = self.status_provider()
        payload.update(
            {
                "orders": [o.to_dict() for o in orders],
                "tables": self.store.tables(),
                "total": self.store.count(),
            }
        )
        return payload

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(200, "application/json; charset=utf-8", body)

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt: str, *args) -> None:
        """접속할 때마다 콘솔이 지저분해지지 않도록 끈다."""


def serve(store: OrderStore, port: int, status_provider) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = type("Handler", (_Handler,), {"store": store, "status_provider": staticmethod(status_provider)})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread

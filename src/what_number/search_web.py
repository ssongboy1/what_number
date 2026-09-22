"""메뉴로 테이블을 찾는 화면. 파이썬 기본 웹서버만 사용한다.

음식이 나왔을 때 메뉴 이름 일부를 치거나 오늘 주문된 메뉴 버튼을 누르면,
그 메뉴를 주문한 테이블이 최근 주문부터 나온다.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .menu_store import MenuStore
from .web import _ExclusiveHTTPServer

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>몇번인가요</title>
<style>
  :root {
    --bg: #10131a; --card: #1a1f2b; --line: #2c3446; --text: #f2f5fa; --muted: #8e9bb3;
    --accent: #ffc95c; --accent-ink: #2a1f00; --fresh: #4ade80; --bad: #f87171;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; background: var(--bg); color: var(--text); padding-bottom: 40px;
    font-family: "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif;
  }
  header {
    position: sticky; top: 0; z-index: 5; background: rgba(16,19,26,.97);
    border-bottom: 1px solid var(--line); padding: 12px 16px 10px;
  }
  .bar { display: flex; align-items: center; gap: 10px; }
  h1 { font-size: 19px; margin: 0; font-weight: 800; letter-spacing: -.02em; }
  .status { margin-left: auto; font-size: 12.5px; color: var(--muted); display: flex;
            align-items: center; gap: 6px; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--fresh); }
  .dot.off { background: var(--bad); }
  .search { display: flex; gap: 8px; margin-top: 10px; }
  .search input {
    flex: 1; min-width: 0; font-size: 20px; padding: 11px 14px; border-radius: 12px;
    border: 1px solid var(--line); background: var(--card); color: var(--text); outline: none;
  }
  .search input:focus { border-color: var(--accent); }
  .search button {
    flex: 0 0 auto; font-size: 15px; font-weight: 700; padding: 0 14px; border-radius: 12px;
    border: 1px solid var(--line); background: var(--card); color: var(--muted); cursor: pointer;
  }
  .chips { display: flex; gap: 6px; overflow-x: auto; margin-top: 10px; padding-bottom: 2px;
           scrollbar-width: none; }
  .chips::-webkit-scrollbar { display: none; }
  .chip {
    flex: 0 0 auto; padding: 7px 12px; border-radius: 999px; border: 1px solid var(--line);
    background: var(--card); color: #cfd8e8; font-size: 14.5px; font-weight: 600; cursor: pointer;
  }
  .chip.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .chip small { color: var(--muted); font-weight: 600; margin-left: 3px; }
  .chip.on small { color: var(--accent-ink); }
  main { padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; }
  .title { font-size: 14px; color: var(--muted); margin: 2px 2px 0; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px;
          padding: 12px 15px; display: flex; gap: 14px; align-items: center; }
  .card.fresh { border-color: var(--accent); }
  .card.old { opacity: .55; }
  .table { font-size: 34px; font-weight: 800; letter-spacing: -.03em; line-height: 1.05;
           min-width: 92px; }
  .body { flex: 1; min-width: 0; }
  .menu { font-size: 17px; font-weight: 700; word-break: keep-all; }
  .menu .qty { color: var(--accent); margin-left: 4px; }
  .opt { font-size: 14px; color: var(--muted); margin-top: 2px; }
  .when { text-align: right; white-space: nowrap; font-size: 13px; color: var(--muted); }
  .when b { display: block; font-size: 17px; color: var(--text); }
  .lines { font-size: 15.5px; line-height: 1.55; color: #dbe3f0; }
  .lines s { color: var(--muted); }
  .lines .sub { color: var(--muted); font-size: 13.5px; }
  .empty { color: var(--muted); text-align: center; padding: 40px 10px; font-size: 15px;
           line-height: 1.6; white-space: pre-line; }
  .warn { color: var(--accent); font-size: 13px; margin-top: 6px; }
</style>
</head>
<body>
<header>
  <div class="bar">
    <h1>몇번인가요</h1>
    <div class="status"><span class="dot" id="dot"></span><span id="status">연결 중</span></div>
  </div>
  <div class="search">
    <input id="q" type="search" placeholder="메뉴 이름 (예: 감바스)" autocomplete="off">
    <button id="clear" type="button">지우기</button>
  </div>
  <div class="chips" id="chips"></div>
  <div class="warn" id="warn"></div>
</header>
<main id="list"></main>
<script>
const $ = (id) => document.getElementById(id);
let query = "";
let skew = 0;  // 이 기기와 포스 PC 의 시계 차이
let overview = { menus: [], recent: [] };
let results = null;

function now() { return Date.now() / 1000 + skew; }
function clock(ts) {
  const d = new Date(ts * 1000);
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}
function ago(ts) {
  const min = Math.max(0, Math.floor((now() - ts) / 60));
  return min < 1 ? "방금" : min + "분 전";
}
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function age(ts) {
  const min = (now() - ts) / 60;
  return min <= 20 ? " fresh" : (min > 120 ? " old" : "");
}

function renderChips() {
  const box = $("chips");
  box.textContent = "";
  for (const m of overview.menus) {
    const chip = el("button", "chip" + (normalize(m.menu) === normalize(query) ? " on" : ""), m.menu);
    chip.type = "button";
    chip.appendChild(el("small", "", String(m.count)));
    chip.onclick = () => { setQuery(normalize(m.menu) === normalize(query) ? "" : m.menu); };
    box.appendChild(chip);
  }
}

function renderResults() {
  const list = $("list");
  list.textContent = "";
  if (query && results) {
    list.appendChild(el("div", "title", "'" + query + "' 주문한 테이블 " + results.length + "곳"));
    if (!results.length) {
      list.appendChild(el("div", "empty", "오늘 이 메뉴를 주문한 테이블이 없습니다.\\n취소된 주문은 보이지 않습니다."));
    }
    for (const r of results) {
      const card = el("div", "card" + age(r.printed_at));
      card.appendChild(el("div", "table", r.table || "?"));
      const body = el("div", "body");
      const menu = el("div", "menu", r.menu);
      if (r.qty > 1) menu.appendChild(el("span", "qty", "x" + r.qty));
      body.appendChild(menu);
      const note = [r.options, r.kind === "추가" ? "추가 주문" : ""].filter(Boolean).join(" · ");
      if (note) body.appendChild(el("div", "opt", note));
      card.appendChild(body);
      const when = el("div", "when");
      when.appendChild(el("b", "", ago(r.printed_at)));
      when.appendChild(document.createTextNode(clock(r.printed_at)));
      card.appendChild(when);
      list.appendChild(card);
    }
    return;
  }
  list.appendChild(el("div", "title", "최근 주문"));
  if (!overview.recent.length) {
    list.appendChild(el("div", "empty", "오늘 들어온 주방 주문서가 아직 없습니다."));
  }
  for (const t of overview.recent) {
    const card = el("div", "card" + age(t.printed_at));
    card.appendChild(el("div", "table", t.table || "?"));
    const body = el("div", "body lines");
    for (const i of t.items) {
      const line = el("div");
      const text = i.menu + (i.qty > 1 ? " x" + i.qty : "");
      if (i.cancelled) { line.appendChild(el("s", "", text + " 취소")); } else { line.textContent = text; }
      if (i.options) line.appendChild(el("span", "sub", " " + i.options));
      body.appendChild(line);
    }
    card.appendChild(body);
    const when = el("div", "when");
    when.appendChild(el("b", "", ago(t.printed_at)));
    when.appendChild(document.createTextNode(clock(t.printed_at)));
    card.appendChild(when);
    list.appendChild(card);
  }
}

function normalize(text) { return String(text || "").replace(/\\s+/g, "").toLowerCase(); }

function setQuery(text) {
  $("q").value = text;
  query = text.trim();
  results = null;
  renderChips();
  search();
}

async function search() {
  const asked = query;
  if (!asked) { renderResults(); return; }
  try {
    const res = await fetch("/api/search?q=" + encodeURIComponent(asked));
    const data = await res.json();
    if (asked !== query) return;  // 그 사이 다른 글자를 쳤다
    results = data.results;
    renderResults();
  } catch (e) { /* 다음 새로고침에서 다시 한다 */ }
}

async function refresh() {
  try {
    const res = await fetch("/api/overview");
    const data = await res.json();
    skew = data.now - Date.now() / 1000;
    overview = data;
    const s = data.status || {};
    $("dot").className = "dot" + (s.ok ? "" : " off");
    $("status").textContent = s.ok
      ? (data.summary.last_ticket_at ? "마지막 주문 " + clock(data.summary.last_ticket_at) : "주문 기다리는 중")
      : "주방 기록을 못 읽는 중";
    $("warn").textContent = (s.errors || []).join(" / ");
    renderChips();
    if (query) { await search(); } else { renderResults(); }
  } catch (e) {
    $("dot").className = "dot off";
    $("status").textContent = "포스 PC 와 연결 끊김";
  }
}

$("q").addEventListener("input", () => { query = $("q").value.trim(); renderChips(); search(); });
$("clear").onclick = () => { setQuery(""); $("q").focus(); };
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "WhatNumber"
    store: MenuStore
    status_provider = staticmethod(lambda: {})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        elif parsed.path == "/api/search":
            text = (parse_qs(parsed.query).get("q") or [""])[0][:40]
            self._send_json({"query": text, "results": self.store.search(text), "now": time.time()})
        elif parsed.path == "/api/overview":
            self._send_json({
                "now": time.time(),
                "status": self.status_provider(),
                "summary": self.store.summary(),
                "menus": self.store.menus(),
                "recent": self.store.recent(limit=30),
            })
        elif parsed.path == "/favicon.ico":
            self._send(204, "text/plain", b"")
        else:
            self._send(404, "text/plain; charset=utf-8", "없는 주소입니다".encode("utf-8"))

    def _send_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, "application/json; charset=utf-8", body)

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


def serve_search(store: MenuStore, port: int, status_provider) -> tuple:
    """검색 화면을 연다. 포트를 이미 쓰고 있으면 OSError."""
    handler = type("Handler", (_Handler,), {
        "store": store,
        "status_provider": staticmethod(status_provider),
    })
    httpd = _ExclusiveHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread

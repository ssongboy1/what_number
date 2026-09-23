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
/* 포스 PC 의 옛날 브라우저(인터넷 익스플로러 등)에서도 돌아가도록 예전 문법만 쓴다.
   화살표 함수, fetch, async 같은 요즘 문법을 쓰면 화면이 통째로 멈춘다. */
window.onerror = function (message) {
  var box = document.getElementById("warn");
  if (box) { box.style.color = "#f87171"; box.textContent = "화면 오류: " + message; }
  var dot = document.getElementById("dot");
  if (dot) { dot.className = "dot off"; }
  return false;
};

var query = "";
var skew = 0;  /* 이 기기와 포스 PC 의 시계 차이 */
var overview = { menus: [], recent: [] };
var results = null;

function $(id) { return document.getElementById(id); }
function now() { return new Date().getTime() / 1000 + skew; }
function two(value) { return (value < 10 ? "0" : "") + value; }
function clock(ts) {
  var d = new Date(ts * 1000);
  return two(d.getHours()) + ":" + two(d.getMinutes());
}
function ago(ts) {
  var min = Math.max(0, Math.floor((now() - ts) / 60));
  return min < 1 ? "방금" : min + "분 전";
}
function age(ts) {
  var min = (now() - ts) / 60;
  return min <= 20 ? " fresh" : (min > 120 ? " old" : "");
}
function el(tag, cls, text) {
  var node = document.createElement(tag);
  if (cls) { node.className = cls; }
  if (text !== undefined) { node.appendChild(document.createTextNode(text)); }
  return node;
}
function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
function normalize(text) { return String(text || "").replace(/\s+/g, "").toLowerCase(); }

function ask(path, done) {
  var xhr = new XMLHttpRequest();
  xhr.open("GET", path, true);
  xhr.onreadystatechange = function () {
    if (xhr.readyState !== 4) { return; }
    if (xhr.status === 200) {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
      if (data) { done(data); return; }
    }
    done(null);
  };
  xhr.send();
}

function chipClicked(menu) {
  return function () { setQuery(normalize(menu) === normalize(query) ? "" : menu); };
}

function renderChips() {
  var box = $("chips");
  clear(box);
  for (var i = 0; i < overview.menus.length; i++) {
    var item = overview.menus[i];
    var chip = el("button", "chip" + (normalize(item.menu) === normalize(query) ? " on" : ""), item.menu);
    chip.type = "button";
    chip.appendChild(el("small", "", String(item.count)));
    chip.onclick = chipClicked(item.menu);
    box.appendChild(chip);
  }
}

function whenBox(ts) {
  var box = el("div", "when");
  box.appendChild(el("b", "", ago(ts)));
  box.appendChild(document.createTextNode(clock(ts)));
  return box;
}

function renderResults() {
  var list = $("list");
  clear(list);
  var i, card, body;
  if (query && results) {
    list.appendChild(el("div", "title", "'" + query + "' 주문한 테이블 " + results.length + "곳"));
    if (!results.length) {
      list.appendChild(el("div", "empty", "오늘 이 메뉴를 주문한 테이블이 없습니다.
취소된 주문은 보이지 않습니다."));
    }
    for (i = 0; i < results.length; i++) {
      var found = results[i];
      card = el("div", "card" + age(found.printed_at));
      card.appendChild(el("div", "table", found.table || "?"));
      body = el("div", "body");
      var menu = el("div", "menu", found.menu);
      if (found.qty > 1) { menu.appendChild(el("span", "qty", "x" + found.qty)); }
      body.appendChild(menu);
      var note = found.options || "";
      if (found.kind === "추가") { note = note ? note + " · 추가 주문" : "추가 주문"; }
      if (note) { body.appendChild(el("div", "opt", note)); }
      card.appendChild(body);
      card.appendChild(whenBox(found.printed_at));
      list.appendChild(card);
    }
    return;
  }
  list.appendChild(el("div", "title", "최근 주문"));
  if (!overview.recent.length) {
    list.appendChild(el("div", "empty", "오늘 들어온 주방 주문서가 아직 없습니다."));
  }
  for (i = 0; i < overview.recent.length; i++) {
    var ticket = overview.recent[i];
    card = el("div", "card" + age(ticket.printed_at));
    card.appendChild(el("div", "table", ticket.table || "?"));
    body = el("div", "body lines");
    for (var k = 0; k < ticket.items.length; k++) {
      var item = ticket.items[k];
      var line = el("div");
      var text = item.menu + (item.qty > 1 ? " x" + item.qty : "");
      if (item.cancelled) { line.appendChild(el("s", "", text + " 취소")); }
      else { line.appendChild(document.createTextNode(text)); }
      if (item.options) { line.appendChild(el("span", "sub", " " + item.options)); }
      body.appendChild(line);
    }
    card.appendChild(body);
    card.appendChild(whenBox(ticket.printed_at));
    list.appendChild(card);
  }
}

function setQuery(text) {
  $("q").value = text;
  query = text.replace(/^\s+|\s+$/g, "");
  results = null;
  renderChips();
  search();
}

function search() {
  var asked = query;
  if (!asked) { renderResults(); return; }
  ask("/api/search?q=" + encodeURIComponent(asked), function (data) {
    if (!data || asked !== query) { return; }  /* 그 사이 다른 글자를 쳤다 */
    results = data.results;
    renderResults();
  });
}

function refresh() {
  ask("/api/overview", function (data) {
    if (!data) {
      $("dot").className = "dot off";
      $("status").textContent = "포스 PC 와 연결 끊김";
      return;
    }
    skew = data.now - new Date().getTime() / 1000;
    overview = data;
    var state = data.status || {};
    $("dot").className = "dot" + (state.ok ? "" : " off");
    if (state.ok) {
      $("status").textContent = data.summary.last_ticket_at
        ? "마지막 주문 " + clock(data.summary.last_ticket_at)
        : "주문 기다리는 중";
    } else {
      $("status").textContent = "주방 기록을 못 읽는 중";
    }
    $("warn").textContent = (state.errors || []).join(" / ");
    renderChips();
    if (query) { search(); } else { renderResults(); }
  });
}

$("q").onkeyup = function () { setQuery($("q").value); };
$("q").onchange = function () { setQuery($("q").value); };
$("clear").onclick = function () { setQuery(""); $("q").focus(); };
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

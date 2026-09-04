"""홀 태블릿 화면.

web.py 의 PAGE 와 같은 방식으로 HTML 을 문자열 하나에 담는다.
.py 안에 두면 CP949 검사 테스트가 이 화면까지 자동으로 지켜준다.
"""

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<title>홀 주문서</title>
<style>
  :root {
    --bg:#11141b; --card:#1b2130; --line:#2b3446; --line2:#3a465e;
    --text:#f4f7fb; --muted:#93a1b8; --accent:#ffc95c; --accent-ink:#2b2000;
    --done:#4ade80; --warn:#fbbf24; --late:#f87171; --takeout:#7ee6a8;
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html, body {
    margin:0; height:100%; background:var(--bg); color:var(--text);
    font-family:"Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif;
    touch-action:manipulation; user-select:none; -webkit-user-select:none;
    overscroll-behavior:contain;
  }
  button { font:inherit; color:inherit; background:none; border:none; cursor:pointer; }

  header {
    position:sticky; top:0; z-index:20; height:64px; display:flex; align-items:center;
    gap:14px; padding:0 14px; background:rgba(17,20,27,.97);
    border-bottom:1px solid var(--line);
  }
  h1 { font-size:20px; margin:0; font-weight:800; letter-spacing:-.02em; white-space:nowrap; }
  .count { font-size:15px; color:var(--muted); font-weight:700; }
  .spacer { flex:1; }

  .toggle { display:flex; border:1px solid var(--line2); border-radius:10px; overflow:hidden; }
  .toggle button { padding:10px 16px; font-size:15px; font-weight:700; color:var(--muted); }
  .toggle button.on { background:var(--accent); color:var(--accent-ink); }

  .find {
    width:72px; height:72px; border-radius:50%; background:var(--accent);
    color:var(--accent-ink); font-size:15px; font-weight:800; line-height:1.2;
    box-shadow:0 3px 10px rgba(0,0,0,.4); flex:0 0 auto;
  }

  main { padding:12px; display:grid; gap:12px;
         grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); }

  .card { background:var(--card); border:1px solid var(--line); border-left:5px solid var(--line);
          border-radius:14px; overflow:hidden; }
  .card.warn { border-left-color:var(--warn); }
  .card.late { border-left-color:var(--late); }
  .card.ready { border-color:var(--done); border-left-color:var(--done); }

  .card-top { display:flex; align-items:baseline; gap:10px; padding:12px 14px 8px; }
  .table { font-size:34px; font-weight:800; letter-spacing:-.03em; line-height:1; }
  .badge { font-size:13px; font-weight:800; padding:3px 8px; border-radius:6px;
           background:rgba(255,201,92,.18); color:var(--accent); }
  .badge.takeout { background:rgba(126,230,168,.16); color:var(--takeout); }
  .ago { margin-left:auto; font-size:19px; font-weight:800; white-space:nowrap; }
  .ago.warn { color:var(--warn); } .ago.late { color:var(--late); }

  .group { padding:6px 14px 2px; font-size:13px; font-weight:700; color:var(--muted);
           border-top:1px dashed var(--line2); margin-top:4px; }

  .item { display:flex; align-items:center; gap:12px; min-height:64px;
          padding:8px 14px; border-top:1px solid var(--line); }
  .item:first-of-type { border-top:none; }
  .box { width:28px; height:28px; flex:0 0 auto; border:2.5px solid var(--line2);
         border-radius:7px; position:relative; }
  .item.on .box { background:var(--done); border-color:var(--done); }
  .item.on .box::after { content:""; position:absolute; left:8px; top:2px; width:8px; height:15px;
         border:solid #0d2417; border-width:0 3px 3px 0; transform:rotate(45deg); }
  .name { font-size:21px; font-weight:700; line-height:1.25; }
  .opt { font-size:15px; color:var(--muted); margin-top:2px; }
  .item.on .name, .item.on .opt { text-decoration:line-through; opacity:.45; }
  .qty { margin-left:auto; font-size:17px; font-weight:800; color:var(--accent);
         background:rgba(255,201,92,.14); padding:3px 9px; border-radius:7px; }

  .empty { grid-column:1/-1; text-align:center; color:var(--muted); padding:80px 20px;
           font-size:16px; line-height:2; }
  .empty b { display:block; color:var(--text); font-size:20px; margin-bottom:8px; }

  .sheet { position:fixed; left:0; right:0; bottom:0; z-index:40; background:var(--card);
           border-top:2px solid var(--done); padding:18px 20px 22px;
           box-shadow:0 -6px 24px rgba(0,0,0,.5); display:none; }
  .sheet.show { display:block; }
  .sheet p { margin:0 0 14px; font-size:20px; font-weight:700; }
  .sheet .row { display:flex; gap:12px; }
  .sheet button { flex:1; min-height:60px; border-radius:12px; font-size:18px; font-weight:800; }
  .ok { background:var(--done); color:#0d2417; }
  .later { background:var(--line); color:var(--text); }

  .undo { position:fixed; left:50%; transform:translateX(-50%); bottom:18px; z-index:41;
          background:var(--card); border:1px solid var(--line2); border-radius:12px;
          padding:12px 16px; display:none; align-items:center; gap:14px; font-size:16px; }
  .undo.show { display:flex; }
  .undo button { background:var(--accent); color:var(--accent-ink); font-weight:800;
                 padding:10px 16px; border-radius:9px; }

  .overlay { position:fixed; inset:0; z-index:50; background:var(--bg); display:none;
             flex-direction:column; }
  .overlay.show { display:flex; }
  .ov-top { display:flex; gap:12px; padding:14px; border-bottom:1px solid var(--line); }
  .ov-top input { flex:1; min-height:56px; background:var(--card); border:1px solid var(--line2);
                  border-radius:12px; padding:0 16px; font-size:20px; color:var(--text); }
  .ov-top button { min-width:96px; background:var(--line); border-radius:12px; font-size:17px;
                   font-weight:700; }
  .ov-body { flex:1; overflow-y:auto; padding:14px; }
  .chips { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }
  .chip { background:var(--card); border:1px solid var(--line2); border-radius:11px;
          padding:14px 16px; font-size:17px; font-weight:700; }
  .hit { background:var(--card); border:1px solid var(--line); border-radius:12px;
         padding:14px 16px; margin-bottom:10px; }
  .hit .h { display:flex; align-items:baseline; gap:12px; }
  .hit .t { font-size:26px; font-weight:800; }
  .hit .s { margin-left:auto; font-size:16px; color:var(--muted); }
  .hit .m { margin-top:8px; font-size:16px; line-height:1.6; color:#cfd9e8; }
  .hit .m .mine { color:var(--accent); font-weight:800; }
  .hit .m .gone { text-decoration:line-through; opacity:.45; }

  .offline { position:fixed; inset:0; z-index:90; background:rgba(10,12,17,.93); display:none;
             align-items:center; justify-content:center; text-align:center; font-size:22px;
             font-weight:800; line-height:1.8; }
  .offline.show { display:flex; }
</style>
</head>
<body>
<header>
  <h1>홀 주문서</h1>
  <span class="count" id="count"></span>
  <div class="spacer"></div>
  <div class="toggle">
    <button id="byTicket" onclick="setView('ticket')">주문서별</button>
    <button id="byTable" onclick="setView('table')">테이블별</button>
  </div>
  <button class="find" onclick="openFind()">메뉴<br>찾기</button>
</header>

<main id="board"></main>

<div class="sheet" id="sheet">
  <p id="sheetText"></p>
  <div class="row">
    <button class="ok" onclick="confirmDone()">내리기</button>
    <button class="later" onclick="closeSheet()">조금 더 두기</button>
  </div>
</div>

<div class="undo" id="undo">
  <span>주문서를 내렸습니다</span>
  <button onclick="undoDone()">되돌리기</button>
</div>

<div class="overlay" id="find">
  <div class="ov-top">
    <input id="q" placeholder="메뉴 이름 또는 초성" oninput="renderFind()" autocomplete="off">
    <button onclick="closeFind()">닫기</button>
  </div>
  <div class="ov-body" id="findBody"></div>
</div>

<div class="offline" id="offline">연결이 끊겼습니다<br>화면이 최신이 아닙니다</div>

<script>
let state = {rev: -1, tickets: []};
let view = localStorage.getItem("hallView") || "ticket";
let pending = {};          // 서버 응답 전에도 즉시 반응하도록
let sheetTicket = null, lastDone = null, lastOk = Date.now();
let menus = [], findResults = null;

const CHO = ["ㄱ","ㄲ","ㄴ","ㄷ","ㄸ","ㄹ","ㅁ","ㅂ","ㅃ","ㅅ","ㅆ","ㅇ","ㅈ","ㅉ","ㅊ","ㅋ","ㅌ","ㅍ","ㅎ"];
function chosung(text) {
  let out = "";
  for (const ch of text) {
    const code = ch.charCodeAt(0) - 0xAC00;
    out += (code >= 0 && code <= 11171) ? CHO[Math.floor(code / 588)] : ch;
  }
  return out;
}
function esc(s) {
  return (s || "").replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function agoText(ts) {
  const m = Math.floor((Date.now() / 1000 - ts) / 60);
  if (m < 1) return "방금";
  if (m < 60) return m + "분";
  return Math.floor(m / 60) + "시간 " + (m % 60) + "분";
}
function agoClass(ts) {
  const m = (Date.now() / 1000 - ts) / 60;
  return m > 20 ? "late" : (m > 10 ? "warn" : "");
}
function isServed(item) {
  return (item.id in pending) ? pending[item.id] : item.served;
}

function setView(v) {
  view = v; localStorage.setItem("hallView", v); render();
}

function itemHtml(item) {
  const on = isServed(item);
  const opt = item.option ? '<div class="opt">' + esc(item.option) + "</div>" : "";
  const qty = item.qty > 1 ? '<span class="qty">x' + item.qty + "</span>" : "";
  return '<div class="item ' + (on ? "on" : "") + '" data-id="' + item.id + '">' +
    '<div class="box"></div><div><div class="name">' + esc(item.menu) + "</div>" + opt +
    "</div>" + qty + "</div>";
}

function cardHtml(head, tickets) {
  const first = tickets[0];
  const cls = first.status === "ready" ? "ready" : agoClass(first.received_at);
  let body = "";
  tickets.forEach((t, i) => {
    if (i > 0) {
      const when = new Date(t.received_at * 1000);
      body += '<div class="group">추가 ' +
        String(when.getHours()).padStart(2, "0") + ":" +
        String(when.getMinutes()).padStart(2, "0") + "</div>";
    }
    body += t.items.map(itemHtml).join("");
  });
  return '<div class="card ' + cls + '">' + head + body + "</div>";
}

function headHtml(t, extra) {
  const takeout = /포장/.test(t.table);
  const badge = extra ? '<span class="badge">' + extra + "</span>" :
    (t.kind === "추가" ? '<span class="badge">추가</span>' : "");
  const to = takeout ? '<span class="badge takeout">포장</span>' : "";
  return '<div class="card-top"><span class="table">' + esc(t.table) + "</span>" + to + badge +
    '<span class="ago ' + agoClass(t.received_at) + '">' + agoText(t.received_at) + "</span></div>";
}

function render() {
  document.getElementById("byTicket").className = view === "ticket" ? "on" : "";
  document.getElementById("byTable").className = view === "table" ? "on" : "";

  const board = document.getElementById("board");
  const list = state.tickets;
  if (!list.length) {
    board.innerHTML = '<div class="empty"><b>진행 중인 주문서가 없습니다</b>' +
      "새 주문서가 들어오면 여기에 나타납니다</div>";
    document.getElementById("count").textContent = "";
    return;
  }

  let html = "";
  if (view === "ticket") {
    html = list.map(t => cardHtml(headHtml(t), [t])).join("");
  } else {
    const groups = new Map();
    for (const t of list) {
      if (!groups.has(t.table_key)) groups.set(t.table_key, []);
      groups.get(t.table_key).push(t);
    }
    for (const [, ts] of groups) {
      const extra = ts.length > 1 ? "주문서 " + ts.length + "장" : "";
      html += cardHtml(headHtml(ts[0], extra), ts);
    }
  }
  board.innerHTML = html;

  const left = list.reduce((n, t) => n + t.item_count - t.served_count, 0);
  document.getElementById("count").textContent =
    "주문서 " + list.length + "장 · 남은 메뉴 " + left + "개";
}

// --- 체크 ---
let pressTimer = null, pressedId = null;
document.getElementById("board").addEventListener("pointerdown", e => {
  const row = e.target.closest(".item");
  if (!row) return;
  pressedId = Number(row.dataset.id);
  const on = row.classList.contains("on");
  if (!on) return;                       // 체크는 탭
  pressTimer = setTimeout(() => {        // 해제는 길게 눌러야 한다
    pressTimer = null;
    setServed(pressedId, false);
    pressedId = null;
  }, 500);
});
document.getElementById("board").addEventListener("pointerup", e => {
  const row = e.target.closest(".item");
  if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
  if (!row || pressedId === null) { pressedId = null; return; }
  if (!row.classList.contains("on")) setServed(pressedId, true);
  pressedId = null;
});
document.getElementById("board").addEventListener("pointercancel", () => {
  if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
  pressedId = null;
});

async function setServed(itemId, served) {
  pending[itemId] = served;
  render();
  try {
    const res = await post("/api/hall/items/" + itemId + "/served", {served: served});
    delete pending[itemId];
    if (res.became_ready) openSheet(res.ticket_id);
    await refresh();
  } catch (e) {
    delete pending[itemId];
    render();
  }
}

function openSheet(ticketId) {
  const t = state.tickets.find(x => x.id === ticketId);
  sheetTicket = ticketId;
  document.getElementById("sheetText").textContent =
    (t ? t.table + " " : "") + "메뉴가 모두 나왔습니다. 주문서를 내릴까요?";
  document.getElementById("sheet").classList.add("show");
}
function closeSheet() {
  document.getElementById("sheet").classList.remove("show"); sheetTicket = null;
}
async function confirmDone() {
  const id = sheetTicket;
  closeSheet();
  if (!id) return;
  await post("/api/hall/tickets/" + id + "/status", {status: "done"});
  lastDone = id;
  const bar = document.getElementById("undo");
  bar.classList.add("show");
  setTimeout(() => bar.classList.remove("show"), 20000);
  await refresh();
}
async function undoDone() {
  document.getElementById("undo").classList.remove("show");
  if (lastDone) { await post("/api/hall/tickets/" + lastDone + "/status", {status: "open"}); }
  lastDone = null;
  await refresh();
}

// --- 메뉴 찾기 ---
async function openFind() {
  document.getElementById("find").classList.add("show");
  document.getElementById("q").value = "";
  findResults = null;
  if (!menus.length) {
    try { menus = (await getJson("/api/hall/menus")).menus || []; } catch (e) { menus = []; }
  }
  renderFind();
}
function closeFind() { document.getElementById("find").classList.remove("show"); }

function renderFind() {
  const q = document.getElementById("q").value.trim();
  const body = document.getElementById("findBody");
  if (findResults && findResults.q === q) { body.innerHTML = hitsHtml(findResults); return; }

  let list = menus;
  if (q) {
    const qc = chosung(q);
    list = menus.filter(m => m.menu.includes(q) || chosung(m.menu).includes(qc));
  } else {
    list = menus.slice(0, 24);
  }
  body.innerHTML = '<div class="chips">' +
    list.slice(0, 40).map(m =>
      '<button class="chip" onclick="searchMenu(' + JSON.stringify(m.menu).replace(/"/g, "&quot;") +
      ')">' + esc(m.menu) + "</button>").join("") +
    "</div>" + (q && !list.length ? '<div class="empty">찾는 메뉴가 없습니다</div>' : "");
}

async function searchMenu(menu) {
  const data = await getJson("/api/hall/search?menu=" + encodeURIComponent(menu));
  findResults = {q: document.getElementById("q").value.trim(), menu: menu, tickets: data.tickets};
  document.getElementById("findBody").innerHTML = hitsHtml(findResults);
}

function hitsHtml(r) {
  if (!r.tickets.length) {
    return '<div class="empty"><b>' + esc(r.menu) + "</b>오늘 이 메뉴 주문이 없습니다</div>";
  }
  return '<div class="chips"><button class="chip" onclick="findResults=null;renderFind()">' +
    "&#8592; 메뉴 다시 고르기</button></div>" +
    r.tickets.map(t => {
      const when = new Date(t.received_at * 1000);
      const time = String(when.getHours()).padStart(2, "0") + ":" +
                   String(when.getMinutes()).padStart(2, "0");
      const lines = t.items.map(i => {
        const mine = i.menu.includes(r.menu);
        return '<span class="' + (mine ? "mine " : "") + (i.served ? "gone" : "") + '">' +
          esc(i.menu) + (i.option ? " (" + esc(i.option) + ")" : "") + "</span>";
      }).join(" &#183; ");
      return '<div class="hit"><div class="h"><span class="t">' + esc(t.table) + "</span>" +
        '<span class="s">' + time + " &#183; " + agoText(t.received_at) + " 전 &#183; " +
        t.served_count + "/" + t.item_count + " 나감</span></div>" +
        '<div class="m">' + lines + "</div></div>";
    }).join("");
}

// --- 서버와 맞추기 ---
async function getJson(url) {
  const res = await fetch(url, {cache: "no-store"});
  if (!res.ok) throw new Error(res.status);
  return res.json();
}
async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(res.status);
  lastOk = Date.now();
  return res.json();
}
async function refresh() {
  try {
    const data = await getJson("/api/hall/state");
    lastOk = Date.now();
    document.getElementById("offline").classList.remove("show");
    if (data.rev !== state.rev) { state = data; render(); }
  } catch (e) {
    if (Date.now() - lastOk > 10000) {
      document.getElementById("offline").classList.add("show");
    }
  }
}

setView(view);
refresh();
setInterval(refresh, 1500);
setInterval(render, 20000);   // 경과 시간 표시를 계속 맞춘다
</script>
</body>
</html>
"""

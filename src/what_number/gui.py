"""메뉴로 테이블을 찾는 창.

포스 PC 의 기본 브라우저가 오래되어 화면이 안 뜨는 일이 있어서, 브라우저 없이
이 창 하나로 쓸 수 있게 한다. 파이썬에 기본으로 들어 있는 tkinter 만 쓴다.

창과 웹 화면은 같은 저장소를 본다. 창은 포스 PC 에서, 웹 화면은 폰·태블릿에서 쓴다.
"""

from __future__ import annotations

import time
from datetime import datetime

BACKGROUND = "#10131a"
CARD = "#1a1f2b"
LINE = "#2c3446"
TEXT = "#f2f5fa"
MUTED = "#8e9bb3"
ACCENT = "#ffc95c"
FRESH = "#4ade80"
BAD = "#f87171"

FAMILY = "맑은 고딕"
REFRESH_MS = 2000
MAX_CHIPS = 12


def elapsed(printed_at: float, now: float | None = None) -> str:
    """'5분 전' 처럼. 한 시간이 넘으면 시간까지 보여준다."""
    minutes = int(max(0.0, (now if now is not None else time.time()) - printed_at) // 60)
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    return f"{minutes // 60}시간 {minutes % 60}분 전"


def clock(printed_at: float) -> str:
    return datetime.fromtimestamp(printed_at).strftime("%H:%M")


def result_line(found: dict, now: float | None = None) -> tuple:
    """검색 결과 한 줄. (테이블, 메뉴, 설명, 시각)"""
    menu = found.get("menu", "")
    if found.get("qty", 1) > 1:
        menu += " x%d" % found["qty"]
    notes = [found.get("options") or ""]
    if found.get("kind") == "추가":
        notes.append("추가 주문")
    note = " · ".join(part for part in notes if part)
    when = f"{elapsed(found.get('printed_at', 0), now)}   {clock(found.get('printed_at', 0))}"
    return found.get("table") or "?", menu, note, when


def ticket_lines(ticket: dict) -> list:
    """최근 주문서 한 장의 메뉴 줄들."""
    lines = []
    for item in ticket.get("items", []):
        text = item.get("menu", "")
        if item.get("qty", 1) > 1:
            text += " x%d" % item["qty"]
        if item.get("options"):
            text += " · " + item["options"]
        if item.get("cancelled"):
            text += " (취소됨)"
        lines.append(text)
    return lines


def short_path(path, limit: int = 44) -> str:
    """긴 폴더 경로는 뒷부분만 보여준다. 창 아래 한 줄에 들어가도록."""
    text = str(path)
    if len(text) <= limit:
        return text
    parts = text.replace("/", "\\").split("\\")
    shown = parts[-1]
    for part in reversed(parts[:-1]):
        longer = part + "\\" + shown
        if len("...\\" + longer) > limit:
            break
        shown = longer
    return "...\\" + shown


def available() -> bool:
    """이 PC 에서 창을 띄울 수 있는지."""
    try:
        import tkinter
    except Exception:
        return False
    try:
        root = tkinter.Tk()
    except Exception:
        return False
    root.destroy()
    return True


class SearchWindow:
    """메뉴를 치면 그 메뉴를 주문한 테이블을 보여주는 창."""

    def __init__(self, store, status_provider=lambda: {}, note: str = ""):
        import tkinter as tk

        self.store = store
        self.status_provider = status_provider
        self.note = note
        self.query = ""
        self._chip_buttons = []

        self.root = tk.Tk()
        self.root.title("몇번인가요 - 메뉴로 테이블 찾기")
        self.root.configure(bg=BACKGROUND)
        self.root.geometry("560x780")
        self.root.minsize(420, 480)
        self._center()

        self._build_head(tk)
        self._build_search(tk)
        self._build_chips(tk)
        self._build_foot(tk)   # 아래쪽 자리를 먼저 잡아야 목록에 밀려 잘리지 않는다
        self._build_list(tk)

        self.root.bind("<Escape>", lambda event: self.set_query(""))
        self.entry.focus_set()
        self.refresh()

    def _center(self) -> None:
        """처음 열릴 때 화면 가운데에. 포스 화면이 커도 구석에 숨지 않도록."""
        self.root.update_idletasks()
        width, height = 560, 780
        left = max(0, (self.root.winfo_screenwidth() - width) // 2)
        top = max(0, (self.root.winfo_screenheight() - height) // 3)
        self.root.geometry(f"{width}x{height}+{left}+{top}")

    # --- 화면 만들기 ---
    def _build_head(self, tk) -> None:
        head = tk.Frame(self.root, bg=BACKGROUND)
        head.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(head, text="몇번인가요", bg=BACKGROUND, fg=TEXT,
                 font=(FAMILY, 16, "bold")).pack(side="left")
        self.dot = tk.Frame(head, bg=FRESH, width=10, height=10)
        self.dot.pack(side="right", padx=(8, 0), pady=6)
        self.state_label = tk.Label(head, text="연결 중", bg=BACKGROUND, fg=MUTED,
                                    font=(FAMILY, 10))
        self.state_label.pack(side="right")

    def _build_search(self, tk) -> None:
        box = tk.Frame(self.root, bg=BACKGROUND)
        box.pack(fill="x", padx=16, pady=(10, 0))
        self.text_var = tk.StringVar()
        self.entry = tk.Entry(
            box, textvariable=self.text_var, font=(FAMILY, 18), bg=CARD, fg=TEXT,
            insertbackground=ACCENT, relief="flat", highlightthickness=1,
            highlightbackground=LINE, highlightcolor=ACCENT,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, ipadx=8)
        self.text_var.trace_add("write", lambda *args: self._typed())
        tk.Button(box, text="지우기", command=lambda: self.set_query(""), bg=CARD, fg=MUTED,
                  font=(FAMILY, 11, "bold"), relief="flat", activebackground=LINE,
                  activeforeground=TEXT, cursor="hand2").pack(side="left", padx=(8, 0), ipadx=10, ipady=8)
        tk.Label(self.root, text="메뉴 이름 일부를 치거나, 아래 버튼을 누르세요",
                 bg=BACKGROUND, fg=MUTED, font=(FAMILY, 9)).pack(anchor="w", padx=18, pady=(4, 0))

    def _build_chips(self, tk) -> None:
        self.chips = tk.Frame(self.root, bg=BACKGROUND)
        self.chips.pack(fill="x", padx=12, pady=(6, 0))

    def _build_list(self, tk) -> None:
        wrap = tk.Frame(self.root, bg=BACKGROUND)
        wrap.pack(fill="both", expand=True, padx=16, pady=(10, 0))
        self.view = tk.Text(
            wrap, bg=BACKGROUND, fg=TEXT, relief="flat", wrap="word", cursor="arrow",
            highlightthickness=0, spacing1=2, spacing3=6, padx=4, pady=4,
        )
        bar = tk.Scrollbar(wrap, command=self.view.yview, bg=CARD, troughcolor=BACKGROUND,
                           relief="flat", borderwidth=0)
        self.view.configure(yscrollcommand=bar.set, state="disabled")
        bar.pack(side="right", fill="y")
        self.view.pack(side="left", fill="both", expand=True)

        self.view.tag_configure("title", foreground=MUTED, font=(FAMILY, 10), spacing3=8)
        self.view.tag_configure("table", foreground=TEXT, font=(FAMILY, 22, "bold"))
        self.view.tag_configure("table_fresh", foreground=ACCENT, font=(FAMILY, 22, "bold"))
        self.view.tag_configure("menu", foreground=TEXT, font=(FAMILY, 13, "bold"))
        self.view.tag_configure("note", foreground=MUTED, font=(FAMILY, 10))
        self.view.tag_configure("when", foreground=MUTED, font=(FAMILY, 10), spacing3=10)
        self.view.tag_configure("empty", foreground=MUTED, font=(FAMILY, 11), spacing1=20)

    def _build_foot(self, tk) -> None:
        foot = tk.Frame(self.root, bg=BACKGROUND)
        foot.pack(side="bottom", fill="x", padx=16, pady=(6, 12))
        tk.Label(foot, text=self.note, bg=BACKGROUND, fg=MUTED, font=(FAMILY, 9)).pack(side="left")
        self.warn_label = tk.Label(foot, text="", bg=BACKGROUND, fg=ACCENT, font=(FAMILY, 9))
        self.warn_label.pack(side="right")

    # --- 동작 ---
    def _typed(self) -> None:
        self.query = self.text_var.get().strip()
        self.render()

    def set_query(self, text: str) -> None:
        self.text_var.set(text)
        self.entry.icursor("end")
        self.entry.focus_set()

    def _chip_clicked(self, menu: str):
        def clicked() -> None:
            self.set_query("" if self._same(menu) else menu)
        return clicked

    def _same(self, menu: str) -> bool:
        from .menu_store import normalize

        return bool(self.query) and normalize(menu) == normalize(self.query)

    def render_chips(self) -> None:
        import tkinter as tk

        menus = self.store.menus()[:MAX_CHIPS]
        for button in self._chip_buttons:
            button.destroy()
        self._chip_buttons = []
        for index, item in enumerate(menus):
            chosen = self._same(item["menu"])
            button = tk.Button(
                self.chips, text=f"{item['menu']}  {item['count']}",
                command=self._chip_clicked(item["menu"]),
                bg=ACCENT if chosen else CARD, fg="#2a1f00" if chosen else TEXT,
                activebackground=ACCENT if chosen else LINE, activeforeground="#2a1f00" if chosen else TEXT,
                font=(FAMILY, 10, "bold"), relief="flat", cursor="hand2", padx=10, pady=5,
            )
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=3)
            self._chip_buttons.append(button)
        self.chips.grid_columnconfigure(0, weight=1)
        self.chips.grid_columnconfigure(1, weight=1)

    def render(self) -> None:
        now = time.time()
        self.view.configure(state="normal")
        self.view.delete("1.0", "end")
        if self.query:
            found = self.store.search(self.query)
            self.view.insert("end", f"'{self.query}' 주문한 테이블 {len(found)}곳\n", "title")
            if not found:
                self.view.insert(
                    "end", "오늘 이 메뉴를 주문한 테이블이 없습니다.\n취소된 주문은 보이지 않습니다.\n", "empty")
            for row in found:
                table, menu, note, when = result_line(row, now)
                fresh = now - row.get("printed_at", 0) <= 20 * 60
                self.view.insert("end", table + "  ", "table_fresh" if fresh else "table")
                self.view.insert("end", menu + "\n", "menu")
                if note:
                    self.view.insert("end", "      " + note + "\n", "note")
                self.view.insert("end", "      " + when + "\n", "when")
        else:
            recent = self.store.recent(limit=30)
            self.view.insert("end", "최근 주문\n", "title")
            if not recent:
                self.view.insert("end", "오늘 들어온 주방 주문서가 아직 없습니다.\n", "empty")
            for ticket in recent:
                fresh = now - ticket.get("printed_at", 0) <= 20 * 60
                self.view.insert("end", (ticket.get("table") or "?") + "  ",
                                 "table_fresh" if fresh else "table")
                lines = ticket_lines(ticket)
                self.view.insert("end", (lines[0] if lines else "") + "\n", "menu")
                for extra in lines[1:]:
                    self.view.insert("end", "      " + extra + "\n", "menu")
                self.view.insert(
                    "end",
                    "      " + elapsed(ticket.get("printed_at", 0), now)
                    + "   " + clock(ticket.get("printed_at", 0)) + "\n",
                    "when",
                )
        self.view.configure(state="disabled")

    def refresh(self) -> None:
        state = self.status_provider() or {}
        summary = self.store.summary()
        self.dot.configure(bg=FRESH if state.get("ok") else BAD)
        if state.get("ok"):
            last = summary.get("last_ticket_at")
            self.state_label.configure(text="마지막 주문 " + clock(last) if last else "주문 기다리는 중")
        else:
            self.state_label.configure(text="주방 기록을 못 읽는 중")
        self.warn_label.configure(text=" / ".join(state.get("errors") or []))
        self.render_chips()
        self.render()
        self.root.after(REFRESH_MS, self.refresh)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass

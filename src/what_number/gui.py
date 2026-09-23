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

# 글꼴은 영문 이름으로 적는다. 한글 이름으로 주면 윈도우 입력기가 다른 글꼴로 조합 글자를
# 그려서 네모로 보이는 일이 있다.
FAMILY = "Malgun Gothic"
# 입력칸은 밝게 둔다. 한글을 조합하는 동안 입력기가 흰 상자를 겹쳐 그리기 때문에,
# 어두운 칸에서는 글자가 밀린 것처럼 보인다.
FIELD = "#f4f6fa"
FIELD_TEXT = "#10131a"
REFRESH_MS = 2000
TABLE_COLUMN = "124p"  # 테이블 번호 칸의 너비
LONG_TABLE = 8  # 이보다 넓은 이름('배달 배민원1')은 작은 글씨로 줄여 칸 안에 넣는다
TYPING_MS = 120  # 글자를 친 뒤 목록을 다시 그리기까지 기다리는 시간


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
    """최근 주문서 한 장의 메뉴 줄들. [(메뉴, 옵션, 취소인지)]

    메뉴와 옵션을 나눠 돌려준다. 옵션이 여러 개면 옅은 글씨로 뒤에 붙이기 위해서다.
    """
    lines = []
    for item in ticket.get("items", []):
        text = item.get("menu", "")
        if item.get("qty", 1) > 1:
            text += " x%d" % item["qty"]
        lines.append((text, item.get("options") or "", bool(item.get("cancelled"))))
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

    def __init__(self, store, status_provider=lambda: {}, note: str = "", title: str = ""):
        import tkinter as tk

        self.store = store
        self.status_provider = status_provider
        self.note = note
        self.query = ""
        self._view_key = None
        self._typing_job = None

        self.title = title or "몇번인가요"
        self.root = tk.Tk()
        self.root.title(self.title + " - 메뉴로 테이블 찾기")
        self.root.configure(bg=BACKGROUND)
        self.root.geometry("560x780")
        self.root.minsize(420, 480)
        self._center()

        self._build_head(tk)
        self._build_search(tk)
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
        tk.Label(head, text=self.title, bg=BACKGROUND, fg=TEXT,
                 font=(FAMILY, 16, "bold")).pack(side="left")
        self.dot = tk.Frame(head, bg=FRESH, width=10, height=10)
        self.dot.pack(side="right", padx=(8, 0), pady=6)
        self.state_label = tk.Label(head, text="연결 중", bg=BACKGROUND, fg=MUTED,
                                    font=(FAMILY, 10))
        self.state_label.pack(side="right")

    def _build_search(self, tk) -> None:
        box = tk.Frame(self.root, bg=BACKGROUND)
        box.pack(fill="x", padx=16, pady=(10, 0))
        # 입력칸을 변수(textvariable)와 묶지 않는다. 묶어 두면 글자를 칠 때마다 Tk 가 칸을
        # 다시 맞추는데, 그 사이에 한글 조합이 끊겨 글자가 밀린다.
        self.entry = tk.Entry(
            box, font=(FAMILY, 18), bg=FIELD, fg=FIELD_TEXT,
            insertbackground=FIELD_TEXT, relief="flat", highlightthickness=2,
            highlightbackground=LINE, highlightcolor=ACCENT,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, ipadx=8)
        self.entry.bind("<KeyRelease>", lambda event: self._typed())
        tk.Button(box, text="지우기", command=lambda: self.set_query(""), bg=CARD, fg=MUTED,
                  font=(FAMILY, 11, "bold"), relief="flat", activebackground=LINE,
                  activeforeground=TEXT, cursor="hand2").pack(side="left", padx=(8, 0), ipadx=10, ipady=8)

        under = tk.Frame(self.root, bg=BACKGROUND)
        under.pack(fill="x", padx=18, pady=(4, 0))
        tk.Label(under, text="메뉴 이름 일부를 치면 그 메뉴를 주문한 테이블이 나옵니다",
                 bg=BACKGROUND, fg=MUTED, font=(FAMILY, 9)).pack(side="left")
        self.show_cancelled = tk.BooleanVar(value=True)
        tk.Checkbutton(
            under, text="취소 포함", variable=self.show_cancelled, command=self._redraw,
            bg=BACKGROUND, fg=MUTED, font=(FAMILY, 9), selectcolor=CARD, activebackground=BACKGROUND,
            activeforeground=TEXT, highlightthickness=0, borderwidth=0, cursor="hand2",
        ).pack(side="right")

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

        # 테이블 번호 자리를 고정해 둔다. 메뉴가 여러 개여도 줄 앞이 가지런하도록.
        self.view.tag_configure("row", tabs=(TABLE_COLUMN, "180p", "240p"), lmargin2=TABLE_COLUMN)
        self.view.tag_configure("title", foreground=MUTED, font=(FAMILY, 10), spacing3=8)
        self.view.tag_configure("table", foreground=TEXT, font=(FAMILY, 20, "bold"))
        self.view.tag_configure("table_fresh", foreground=ACCENT, font=(FAMILY, 20, "bold"))
        self.view.tag_configure("table_long", foreground=TEXT, font=(FAMILY, 12, "bold"))
        self.view.tag_configure("table_long_fresh", foreground=ACCENT, font=(FAMILY, 12, "bold"))
        self.view.tag_configure("menu", foreground=TEXT, font=(FAMILY, 13, "bold"))
        self.view.tag_configure("cancelled", foreground=MUTED, font=(FAMILY, 13, "bold", "overstrike"))
        self.view.tag_configure("note", foreground=MUTED, font=(FAMILY, 10))
        self.view.tag_configure("note_gone", foreground=MUTED, font=(FAMILY, 10, "overstrike"))
        self.view.tag_configure("when", foreground=MUTED, font=(FAMILY, 10), spacing3=12)
        self.view.tag_configure("empty", foreground=MUTED, font=(FAMILY, 11), spacing1=20)

    def _build_foot(self, tk) -> None:
        foot = tk.Frame(self.root, bg=BACKGROUND)
        foot.pack(side="bottom", fill="x", padx=16, pady=(6, 12))
        tk.Label(foot, text=self.note, bg=BACKGROUND, fg=MUTED, font=(FAMILY, 9)).pack(side="left")
        self.warn_label = tk.Label(foot, text="", bg=BACKGROUND, fg=ACCENT, font=(FAMILY, 9))
        self.warn_label.pack(side="right")

    # --- 동작 ---
    def _typed(self) -> None:
        """글자를 칠 때마다 부른다.

        바로 다시 그리면 한글을 조합하는 중에 화면이 흔들려 글자가 밀린다.
        잠깐 기다렸다가, 더 치지 않으면 그때 그린다.
        """
        self.query = self.entry.get().strip()
        if self._typing_job is not None:
            self.root.after_cancel(self._typing_job)
        self._typing_job = self.root.after(TYPING_MS, self._redraw)

    def _redraw(self) -> None:
        self._typing_job = None
        self.render(force=True)

    def set_query(self, text: str) -> None:
        """지우기 같은 곳에서 검색어를 정한다. 직접 친 것이 아니므로 곧바로 그린다."""
        self.entry.delete(0, "end")
        if text:
            self.entry.insert(0, text)
        self.query = text.strip()
        if self.root.focus_get() is not self.entry:
            self.entry.focus_set()  # 이미 입력칸에 있으면 건드리지 않는다(조합 중인 한글이 끊긴다)
        if self._typing_job is not None:
            self.root.after_cancel(self._typing_job)
        self._redraw()

    def _write(self, text: str, *tags) -> None:
        self.view.insert("end", text, ("row",) + tags)

    def _write_head(self, table: str, fresh: bool) -> None:
        """줄 맨 앞의 테이블 번호. 뒤 내용은 늘 같은 자리에서 시작한다.

        '배달 배민원1' 처럼 긴 이름은 작은 글씨로 줄여 칸을 넘지 않게 한다.
        """
        long = sum(2 if ord(char) > 0x7F else 1 for char in table) > LONG_TABLE
        tag = ("table_long" if long else "table") + ("_fresh" if fresh else "")
        self._write(table + "\t", tag)

    def render(self, force: bool = False) -> None:
        now = time.time()
        summary = self.store.summary()
        cancelled = bool(self.show_cancelled.get())
        key = (self.query, cancelled, summary.get("tickets"), summary.get("last_ticket_at"),
               int(now // 30))
        if not force and key == self._view_key:
            return  # 바뀐 것이 없으면 다시 그리지 않는다
        self._view_key = key

        self.view.configure(state="normal")
        self.view.delete("1.0", "end")
        if self.query:
            found = self.store.search(self.query, cancelled=cancelled)
            self._write(f"'{self.query}' 주문한 테이블 {len(found)}곳\n", "title")
            if not found:
                self._write("오늘 이 메뉴를 주문한 테이블이 없습니다.\n", "empty")
            for row in found:
                table, menu, note, when = result_line(row, now)
                self._write_head(table, now - row.get("printed_at", 0) <= 20 * 60)
                self._write(menu + "\n", "cancelled" if row.get("cancelled") else "menu")
                if note:
                    self._write("\t" + note + "\n", "note")
                self._write("\t" + when + "\n", "when")
        else:
            recent = self.store.recent(limit=30, cancelled=cancelled)
            self._write("최근 주문\n", "title")
            if not recent:
                self._write("오늘 들어온 주방 주문서가 아직 없습니다.\n", "empty")
            for ticket in recent:
                self._write_head(ticket.get("table") or "?",
                                 now - ticket.get("printed_at", 0) <= 20 * 60)
                for index, (text, options, gone) in enumerate(ticket_lines(ticket) or [("", "", False)]):
                    self._write(("" if index == 0 else "\t") + text, "cancelled" if gone else "menu")
                    if options:
                        self._write("  " + options, "note_gone" if gone else "note")
                    if gone:
                        self._write("  (취소됨)", "note_gone")
                    self._write("\n", "menu")
                self._write(
                    "\t" + elapsed(ticket.get("printed_at", 0), now)
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
        self.render()
        self.root.after(REFRESH_MS, self.refresh)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass

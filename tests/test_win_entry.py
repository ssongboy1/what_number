"""윈도우 기본 입력칸(win_entry). Tk 입력칸의 한글 밀림을 피하기 위한 것."""

import unittest
from datetime import datetime

from what_number import gui, kitchen_log, win_entry
from what_number.demo import kitchen_ticket
from what_number.menu_store import MenuStore


def ticket(table, order_no, items):
    return kitchen_log.parse_ticket(
        kitchen_ticket(table, order_no, items, "신규", "홀", datetime.now()))


@unittest.skipUnless(win_entry.available() and gui.available(), "윈도우 창을 띄울 수 없는 환경")
class NativeEntryTest(unittest.TestCase):
    """실제로 창을 만들어 본다. 보이지 않게 띄운 뒤 곧바로 닫는다."""

    def setUp(self):
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self.frame = tk.Frame(self.root, width=300, height=52)
        self.frame.pack()
        self.changes = []
        self.escapes = []
        self.entry = win_entry.NativeEntry(
            self.frame, on_change=lambda: self.changes.append(True),
            on_escape=lambda: self.escapes.append(True))

    def tearDown(self):
        self.entry.destroy()
        self.root.destroy()

    def test_text_goes_in_and_comes_out(self):
        self.entry.insert(0, "감바스")
        self.assertEqual(self.entry.get(), "감바스")
        self.entry.insert("end", " 오일")
        self.assertEqual(self.entry.get(), "감바스 오일")
        self.entry.delete(0, "end")
        self.assertEqual(self.entry.get(), "")

    def test_the_window_hears_about_changes(self):
        """글자가 바뀌면 바로 알려줘야 한다(EN_CHANGE). 이것으로 검색이 돌아간다."""
        self.entry.insert(0, "빠네")
        self.assertTrue(self.changes)

    def test_korean_characters_arrive_as_they_are(self):
        """입력기가 조합을 끝내고 보내는 글자(WM_CHAR)가 그대로 들어가야 한다.

        실제 한글 조합이 매끄러운지는 사람이 쳐 봐야 알 수 있지만, 글자가 들어오는
        길과 '바뀌었다' 는 알림이 이어져 있는지는 여기서 확인한다.
        """
        for char in "감바스":
            win_entry._user32.SendMessageW(self.entry._edit, 0x0102, ord(char), 1)  # WM_CHAR
        self.assertEqual(self.entry.get(), "감바스")
        self.assertTrue(self.changes)

    def test_escape_clears(self):
        self.entry.insert(0, "감바스")
        win_entry._user32.SendMessageW(
            self.entry._edit, win_entry.WM_KEYDOWN, win_entry.VK_ESCAPE, 0)
        self.assertTrue(self.escapes)

    def test_focus_does_not_raise(self):
        self.entry.focus_set()
        self.entry.focus_set()  # 이미 초점이 있으면 건드리지 않는다

    def test_resizing_follows_the_frame(self):
        self.frame.configure(width=420, height=60)
        self.root.update()
        self.assertEqual(self.entry.get(), "")  # 크기가 바뀌어도 살아 있다

    def test_destroying_twice_is_harmless(self):
        self.entry.destroy()
        self.entry.destroy()
        self.assertEqual(self.entry.get(), "")  # 없어진 뒤에는 빈 글자


@unittest.skipUnless(win_entry.available() and gui.available(), "윈도우 창을 띄울 수 없는 환경")
class ManyWindowsTest(unittest.TestCase):
    def test_opening_and_closing_several_times(self):
        """창을 여러 번 열고 닫아도 멈추지 않아야 한다.

        창 종류(class)에 입력칸마다 다른 처리 함수를 등록했더니, 먼저 만든 입력칸이
        없어진 뒤 세 번째 창에서 사라진 함수를 가리켜 그대로 멈췄다. 모듈 하나짜리
        처리 함수로 바꿔 고쳤고, 다시 밟지 않도록 여기서 네 번 열고 닫는다.
        """
        import tkinter as tk

        for turn in range(4):
            root = tk.Tk()
            root.withdraw()
            frame = tk.Frame(root, width=300, height=52)
            frame.pack()
            entry = win_entry.NativeEntry(frame)
            entry.insert(0, "시험%d" % turn)
            root.update()
            self.assertEqual(entry.get(), "시험%d" % turn)
            entry.destroy()
            root.destroy()


@unittest.skipUnless(gui.available(), "창을 띄울 수 없는 환경")
class FallbackTest(unittest.TestCase):
    """윈도우 입력칸을 못 만들면 예전 Tk 입력칸으로 열려야 한다."""

    def test_the_window_still_opens_with_the_tk_box(self):
        def refuse(*args, **kwargs):
            raise win_entry.Unsupported("시험용으로 일부러 막았다")

        store = MenuStore(":memory:")
        store.add(ticket("홀-1", "0006-0001", [("34. 감바스 오일 파스타", 1, [])]))
        original = win_entry.NativeEntry
        win_entry.NativeEntry = refuse
        try:
            window = gui.SearchWindow(store, lambda: {"ok": True, "errors": []})
        finally:
            win_entry.NativeEntry = original
        window.root.withdraw()
        try:
            self.assertIsNone(window.native)
            window.set_query("감바스")
            window.root.update()
            self.assertEqual(window.entry.get(), "감바스")
            self.assertIn("감바스 오일 파스타", window.view.get("1.0", "end"))
        finally:
            window.close()
            store.close()


if __name__ == "__main__":
    unittest.main()

import time
import unittest
from datetime import datetime, timedelta

from what_number import gui, kitchen_log, win_entry
from what_number.demo import kitchen_ticket
from what_number.menu_store import MenuStore

NOW = datetime.now().replace(microsecond=0)


def ticket(table, order_no, items, kind="신규", station="홀", minutes_ago=0):
    when = NOW - timedelta(minutes=minutes_ago)
    return kitchen_log.parse_ticket(kitchen_ticket(table, order_no, items, kind, station, when))


class ElapsedTest(unittest.TestCase):
    def test_wording(self):
        now = 10_000.0
        self.assertEqual(gui.elapsed(now - 20, now), "방금")
        self.assertEqual(gui.elapsed(now - 60, now), "1분 전")
        self.assertEqual(gui.elapsed(now - 59 * 60, now), "59분 전")
        self.assertEqual(gui.elapsed(now - 3700, now), "1시간 1분 전")

    def test_future_times_do_not_go_negative(self):
        self.assertEqual(gui.elapsed(10_100.0, 10_000.0), "방금")


class LineTest(unittest.TestCase):
    def test_result_line(self):
        row = {"table": "홀-1", "menu": "감바스 오일 파스타", "qty": 1, "options": "(약)",
               "kind": "신규", "printed_at": 10_000.0}
        self.assertEqual(gui.result_line(row, 10_300.0),
                         ("홀-1", "감바스 오일 파스타", "(약)", "5분 전   " + gui.clock(10_000.0)))

    def test_result_line_shows_quantity_and_extra_order(self):
        row = {"table": "홀-5", "menu": "빠네 크림 파스타", "qty": 2, "options": "크림 소스 추가",
               "kind": "추가", "printed_at": 10_000.0}
        table, menu, note, _when = gui.result_line(row, 10_000.0)
        self.assertEqual((table, menu), ("홀-5", "빠네 크림 파스타 x2"))
        self.assertEqual(note, "크림 소스 추가 · 추가 주문")

    def test_ticket_lines_keep_menu_and_options_apart(self):
        lines = gui.ticket_lines({"items": [
            {"menu": "감바스 오일 파스타", "qty": 1, "options": "(약)", "cancelled": False},
            {"menu": "비프 찹 스테이크", "qty": 2, "options": "순한맛, 소스 따로", "cancelled": True},
        ]})
        self.assertEqual(lines, [
            ("감바스 오일 파스타", "(약)", False),
            ("비프 찹 스테이크 x2", "순한맛, 소스 따로", True),
        ])

    def test_missing_table_is_a_question_mark(self):
        self.assertEqual(gui.result_line({"printed_at": 0})[0], "?")


class ShortPathTest(unittest.TestCase):
    def test_short_paths_are_left_alone(self):
        self.assertEqual(gui.short_path(r"C:\PaLiDa\bin\log"), r"C:\PaLiDa\bin\log")

    def test_long_paths_keep_the_end(self):
        short = gui.short_path(r"C:\Users\pos\AppData\Local\Temp\아주긴폴더이름\PaLiDa\bin\log")
        self.assertTrue(short.startswith("..."))
        self.assertTrue(short.endswith(r"bin\log"))
        self.assertLessEqual(len(short), 44)


@unittest.skipUnless(gui.available(), "창을 띄울 수 없는 환경")
class WindowTest(unittest.TestCase):
    """창을 실제로 만들어 본다. 보이지 않게 띄운 뒤 곧바로 닫는다."""

    def setUp(self):
        self.store = MenuStore(":memory:")
        self.store.add(ticket("홀-1", "0006-0001", [("34. 감바스 오일 파스타", 1, ["(약)"])]))
        self.store.add(ticket("홀-5", "0007-0001", [("49. 빠네 크림 파스타", 2, [])], minutes_ago=40))
        self.window = gui.SearchWindow(self.store, lambda: {"ok": True, "errors": []}, note="기록: X")
        self.window.root.withdraw()

    def tearDown(self):
        self.window.close()
        self.store.close()

    def shown(self):
        return self.window.view.get("1.0", "end")

    def test_recent_orders_are_listed(self):
        self.assertIn("최근 주문", self.shown())
        self.assertIn("홀-1", self.shown())
        self.assertIn("감바스 오일 파스타", self.shown())
        self.assertIn("(약)", self.shown())

    def test_many_options_are_all_shown(self):
        self.store.add(ticket("홀-7", "0030-0001",
                              [("20. 비프 찹 스테이크", 1, ["순한맛", "(약)", "소스 따로"])]))
        self.window._redraw()
        self.window.root.update()
        self.assertIn("비프 찹 스테이크", self.shown())
        self.assertIn("순한맛, (약), 소스 따로", self.shown())

    def test_typing_searches(self):
        self.window.set_query("감바스")
        self.window.root.update()
        text = self.shown()
        self.assertIn("'감바스' 주문한 테이블 1곳", text)
        self.assertIn("홀-1", text)
        self.assertNotIn("빠네", text)

    def test_setting_the_box_searches_at_once(self):
        self.window.set_query("빠네 크림 파스타")
        self.window.root.update()
        self.assertEqual(self.window.entry.get(), "빠네 크림 파스타")
        self.assertIn("홀-5", self.shown())

    def test_clearing_goes_back_to_recent_orders(self):
        self.window.set_query("감바스")
        self.window.root.update()
        self.window.set_query("")
        self.window.root.update()
        self.assertIn("최근 주문", self.shown())

    def test_english_typing_finds_korean_menus(self):
        """한글 입력을 켜지 않고 영문 상태로 쳐도 찾아진다. 'qlvm' -> 비프..."""
        self.store.add(ticket("홀-3", "0020-0001", [("20. 비프 찹 스테이크", 1, [])]))
        self.window.set_query("qlvm")
        self.window.root.update()
        self.assertIn("비프 찹 스테이크", self.shown())
        self.assertIn("홀-3", self.shown())

    def test_nothing_found(self):
        self.window.set_query("없는메뉴")
        self.window.root.update()
        self.assertIn("주문한 테이블이 없습니다", self.shown())

    def test_table_number_has_its_own_column(self):
        """메뉴가 여러 개여도 줄 앞이 가지런해야 한다. 테이블 번호 뒤에 탭을 둔다."""
        self.store.add(ticket("홀-9", "0009-0001", [
            ("34. 감바스 오일 파스타", 1, []), ("49. 빠네 크림 파스타", 1, [])]))
        self.window._redraw()
        self.window.root.update()
        lines = [line for line in self.shown().splitlines() if line.strip()]
        row = next(line for line in lines if line.startswith("홀-9"))
        self.assertTrue(row.startswith("홀-9\t"), row)
        following = lines[lines.index(row) + 1]
        self.assertTrue(following.startswith("\t"), following)

    def test_cancelled_menu_is_shown_with_a_line_through_it(self):
        self.store.add(ticket("홀-7", "0010-0001", [("20. 비프 찹 스테이크", 1, [])], minutes_ago=5))
        self.store.add(ticket("홀-7", "0010-0002", [("20. 비프 찹 스테이크", -1, [])], "추가"))
        self.window._redraw()
        self.window.root.update()
        self.assertIn("비프 찹 스테이크", self.shown())
        marked = self.window.view.tag_ranges("cancelled")
        self.assertTrue(marked, "취소된 메뉴에 취소선 표시가 없습니다")
        self.assertTrue(self.window.view.tag_cget("cancelled", "font").endswith("overstrike"))

    def test_cancelled_menu_can_be_hidden(self):
        self.store.add(ticket("홀-7", "0010-0001", [("20. 비프 찹 스테이크", 1, [])], minutes_ago=5))
        self.store.add(ticket("홀-7", "0010-0002", [("20. 비프 찹 스테이크", -1, [])], "추가"))
        self.window.show_cancelled.set(False)
        self.window._redraw()
        self.window.root.update()
        self.assertNotIn("비프 찹 스테이크", self.shown())

    def test_typing_waits_a_moment_before_redrawing(self):
        """한글을 조합하는 중에 화면이 흔들리지 않도록 잠깐 기다린다."""
        self.window.set_query("")
        self.window.entry.insert(0, "감바")
        self.window._typed()
        self.assertIsNotNone(self.window._typing_job)
        self.assertIn("최근 주문", self.shown())  # 아직 다시 그리지 않았다
        self.window._redraw()
        self.assertIn("'감바' 주문한 테이블", self.shown())

    def test_nothing_is_redrawn_when_nothing_changed(self):
        self.window.render(force=True)
        first = self.window._view_key
        self.window.render()
        self.assertEqual(self.window._view_key, first)

    def test_the_window_keeps_running_when_the_store_fails(self):
        """저장소를 읽다 오류가 나도 2초마다 다시 보는 것을 멈추면 안 된다.

        한 번 끊기면 창이 그대로 굳어서, 주문이 들어와도 화면이 멈춘 것처럼 보인다.
        """
        broken = self.window.store

        class Broken:
            def summary(self, *args, **kwargs):
                return {"tickets": 0, "last_ticket_at": None}

            def search(self, *args, **kwargs):
                raise RuntimeError("일부러 낸 오류")

            def recent(self, *args, **kwargs):
                raise RuntimeError("일부러 낸 오류")

        self.window.store = Broken()
        self.window.refresh()  # 예외가 밖으로 나오지 않는다
        self.window.root.update()
        self.assertIn("새로 그리지 못했습니다", self.window.warn_label.cget("text"))
        self.window.store = broken
        self.window.render(force=True)
        self.assertIn("감바스 오일 파스타", self.shown())

    def test_it_says_so_while_the_old_orders_are_being_read(self):
        self.window.status_provider = lambda: {"ok": True, "errors": [], "caught_up": False}
        self.window.refresh()
        self.window.root.update()
        self.assertEqual(self.window.state_label.cget("text"), "지난 주문을 읽는 중...")

    def test_the_refresh_loop_schedules_itself_once(self):
        """한 번 돌 때 다음 번을 한 번만 예약해야 한다.

        두 번 예약하면 2초마다 두 배로 늘어난다. 30~40초면 창이 마비되고, 새 주문이
        들어올 때 멈춘 것처럼 보였다(시험 모드에서 실제로 그랬다).
        """
        for loop, name in ((self.window.refresh, "refresh"),
                           (self.window._watch_entry, "_watch_entry")):
            if name == "_watch_entry" and self.window.native is None:
                continue
            booked = []
            original = self.window.root.after

            def counting(delay, func=None, *args, _name=name, _original=original):
                # 같은 함수라도 부를 때마다 다른 꾸러미가 되므로 이름으로 본다
                if getattr(func, "__name__", "") == _name:
                    booked.append(delay)
                    return "시험용"
                return _original(delay, func, *args)

            self.window.root.after = counting
            try:
                loop()
            finally:
                self.window.root.after = original
            self.assertEqual(len(booked), 1, "%s 가 %d번 예약했습니다" % (name, len(booked)))

    def test_the_windows_hooks_never_touch_tk(self):
        """입력칸에서 오는 소식은 표시만 남겨야 한다.

        그 소식은 윈도우가 메시지를 처리하는 중에 온다. 그 자리에서 Tk 를 건드리면
        Tk 가 안에 담아 둔 상태가 엉켜 프로그램이 통째로 죽는다. 실제로 시험 모드에서
        새 주문이 들어올 때 죽었다. 여기서는 Tk 를 다른 것으로 바꿔 두고 불러 본다.
        """
        guard = object()  # Tk 를 건드리는 순간 AttributeError 가 난다
        root, self.window.root = self.window.root, guard
        try:
            self.window._note_typing()
            self.window._note_clear()
        finally:
            self.window.root = root
        self.assertTrue(self.window._typed_at)
        self.assertTrue(self.window._clear_wanted)
        self.window._clear_wanted = False

    @unittest.skipUnless(win_entry.available(), "윈도우에서만")
    def test_typing_through_windows_reaches_the_search(self):
        """실제로 윈도우가 글자를 넣어 주는 길(WM_CHAR)로 쳐 본다."""
        if self.window.native is None:
            self.skipTest("윈도우 입력칸이 아닙니다")
        for char in "감바스":
            win_entry._user32.SendMessageW(self.window.native._edit, 0x0102, ord(char), 1)
        deadline = time.time() + 3
        while time.time() < deadline and "'감바스'" not in self.shown():
            self.window.root.update()
            time.sleep(0.03)
        self.assertIn("'감바스' 주문한 테이블", self.shown())

    def test_status_shows_a_problem(self):
        self.window.status_provider = lambda: {"ok": False, "errors": ["기록 파일을 읽지 못했습니다"]}
        self.window.refresh()
        self.window.root.update()
        self.assertEqual(self.window.state_label.cget("text"), "주방 기록을 못 읽는 중")
        self.assertEqual(self.window.warn_label.cget("text"), "기록 파일을 읽지 못했습니다")


if __name__ == "__main__":
    unittest.main()

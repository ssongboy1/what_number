import unittest
from datetime import datetime, timedelta

from what_number import gui, kitchen_log
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

    def test_ticket_lines_do_not_double_the_brackets(self):
        lines = gui.ticket_lines({"items": [
            {"menu": "감바스 오일 파스타", "qty": 1, "options": "(약)", "cancelled": False},
            {"menu": "비프 찹 스테이크", "qty": 2, "options": "", "cancelled": True},
        ]})
        self.assertEqual(lines, ["감바스 오일 파스타 · (약)", "비프 찹 스테이크 x2 (취소됨)"])

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
        self.assertIn("감바스 오일 파스타 · (약)", self.shown())

    def test_menu_buttons_are_made(self):
        labels = [button.cget("text") for button in self.window._chip_buttons]
        self.assertIn("감바스 오일 파스타  1", labels)
        self.assertIn("빠네 크림 파스타  2", labels)

    def test_typing_searches(self):
        self.window.set_query("감바스")
        self.window.root.update()
        text = self.shown()
        self.assertIn("'감바스' 주문한 테이블 1곳", text)
        self.assertIn("홀-1", text)
        self.assertNotIn("빠네", text)

    def test_menu_button_fills_the_box(self):
        self.window._chip_clicked("빠네 크림 파스타")()
        self.window.root.update()
        self.assertEqual(self.window.text_var.get(), "빠네 크림 파스타")
        self.assertIn("홀-5", self.shown())

    def test_clearing_goes_back_to_recent_orders(self):
        self.window.set_query("감바스")
        self.window.root.update()
        self.window.set_query("")
        self.window.root.update()
        self.assertIn("최근 주문", self.shown())

    def test_nothing_found(self):
        self.window.set_query("없는메뉴")
        self.window.root.update()
        self.assertIn("주문한 테이블이 없습니다", self.shown())

    def test_status_shows_a_problem(self):
        self.window.status_provider = lambda: {"ok": False, "errors": ["기록 파일을 읽지 못했습니다"]}
        self.window.refresh()
        self.window.root.update()
        self.assertEqual(self.window.state_label.cget("text"), "주방 기록을 못 읽는 중")
        self.assertEqual(self.window.warn_label.cget("text"), "기록 파일을 읽지 못했습니다")


if __name__ == "__main__":
    unittest.main()

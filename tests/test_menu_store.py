import json
import time
import unittest
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from what_number import kitchen_log
from what_number.demo import kitchen_ticket
from what_number.menu_store import MenuStore, business_day, normalize

NOW = datetime.now().replace(microsecond=0)
DAY = business_day(NOW.timestamp())


def ticket(table, order_no, items, kind="신규", station="홀", minutes_ago=0):
    when = NOW - timedelta(minutes=minutes_ago)
    return kitchen_log.parse_ticket(kitchen_ticket(table, order_no, items, kind, station, when))


class MenuStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = MenuStore(":memory:")

    def tearDown(self):
        self.store.close()

    def tables(self, query):
        return [(r["table"], r["menu"], r["qty"]) for r in self.store.search(query, DAY)]

    def test_search_finds_the_table(self):
        self.store.add(ticket("홀-1", "0006-0001", [("34. 감바스 오일 파스타", 1, ["(약)"])]))
        self.store.add(ticket("홀-2", "0007-0001", [("43. 오븐 토마토 파스타", 2, [])]))
        self.assertEqual(self.tables("감바스"), [("홀-1", "감바스 오일 파스타", 1)])
        self.assertEqual(self.tables("감바스오일"), [("홀-1", "감바스 오일 파스타", 1)])
        self.assertEqual(self.tables("파스타"), [("홀-2", "오븐 토마토 파스타", 2), ("홀-1", "감바스 오일 파스타", 1)])
        self.assertEqual(self.store.search("감바스", DAY)[0]["options"], "(약)")

    def test_search_finds_set_menus_by_their_parts(self):
        """세트 메뉴는 구성품이 옵션으로 들어간다. 스테이크가 나왔을 때도 찾아져야 한다."""
        self.store.add(ticket("홀-17", "0100-0001", [
            ("★2. 커플 SET [2인]", 1, ["★블랙 스톤 스테이크", "★고르곤졸라 피자(L)", "★딸기 에이드"])]))
        self.store.add(ticket("홀-3", "0101-0001", [("20. 비프 찹 스테이크", 1, ["순한맛"])]))
        self.assertEqual(
            [(r["table"], r["menu"]) for r in self.store.search("스테이크", DAY)],
            [("홀-3", "비프 찹 스테이크"), ("홀-17", "★2. 커플 SET [2인]")],
        )
        self.assertEqual([r["table"] for r in self.store.search("에이드", DAY)], ["홀-17"])
        self.assertEqual([r["table"] for r in self.store.search("고르곤졸라", DAY)], ["홀-17"])

    def test_long_option_names_survive(self):
        options = ["머스타드 크리미 드레싱(추천)", "(ICE) 카페라떼 [스페셜 티 원두]"]
        self.store.add(ticket("홀-9", "0102-0001", [("★8. 스테이크 쉐어링 플래터", 2, options)]))
        found = self.store.search("쉐어링", DAY)[0]
        self.assertEqual(found["options"], ", ".join(options))
        self.assertEqual(found["qty"], 2)
        self.assertEqual([r["table"] for r in self.store.search("카페라떼", DAY)], ["홀-9"])

    def test_search_by_menu_number(self):
        self.store.add(ticket("홀-1", "0006-0001", [("34. 감바스 오일 파스타", 1, [])]))
        self.assertEqual(self.tables("34"), [("홀-1", "감바스 오일 파스타", 1)])

    def test_same_ticket_on_two_printers_counts_once(self):
        for station in ("홀", "가니"):
            self.store.add(ticket("홀-2", "0002-0001", [("43. 오븐 토마토 파스타", 1, [])], station=station))
        self.assertEqual(self.tables("토마토"), [("홀-2", "오븐 토마토 파스타", 1)])
        self.assertEqual(self.store.summary(DAY)["tickets"], 1)

    def test_printers_with_different_menus_are_merged(self):
        self.store.add(ticket("홀-3", "0005-0001", [("43. 오븐 토마토 파스타", 1, [])], station="파스타"))
        self.store.add(ticket("홀-3", "0005-0001", [("12. 마르게리따 피자", 1, [])], station="피자"))
        self.assertEqual(self.tables("피자"), [("홀-3", "마르게리따 피자", 1)])
        self.assertEqual(self.tables("토마토"), [("홀-3", "오븐 토마토 파스타", 1)])

    def test_cancel_removes_the_menu(self):
        self.store.add(ticket("홀-4", "0004-0001", [("49. 빠네 크림 파스타", 1, ["크림 소스 추가"])], minutes_ago=5))
        self.store.add(ticket("홀-4", "0004-0002", [("20. 비프 찹 스테이크", 1, ["순한맛"])], "추가", minutes_ago=3))
        cancel = [("49. 빠네 크림 파스타", -1, ["크림 소스 추가"]), ("20. 비프 찹 스테이크", -1, ["순한맛"])]
        for station in ("홀", "가니"):  # 취소 주문서도 프린터마다 온다. 한 번만 빼야 한다
            self.store.add(ticket("홀-4", "0004-0003", cancel, "추가", station=station))
        self.assertEqual(self.tables("파스타"), [])
        self.assertEqual(self.tables("스테이크"), [])
        self.assertEqual(self.store.recent(DAY), [])  # 통째로 취소된 주문은 최근 주문에서도 빠진다

    def test_recent_marks_partly_cancelled_tickets(self):
        self.store.add(ticket("홀-6", "0008-0001", [("34. 감바스 오일 파스타", 1, []), ("20. 비프 찹 스테이크", 1, [])], minutes_ago=5))
        self.store.add(ticket("홀-6", "0008-0002", [("20. 비프 찹 스테이크", -1, [])], "추가"))

        marked = self.store.recent(DAY, cancelled=True)
        self.assertEqual([t["order_no"] for t in marked], ["0008-0001"])
        self.assertEqual([(i["menu"], i["cancelled"], i["qty"]) for i in marked[0]["items"]],
                         [("감바스 오일 파스타", False, 1), ("비프 찹 스테이크", True, 1)])

        hidden = self.store.recent(DAY)  # 취소를 빼고 보면 남은 메뉴만
        self.assertEqual([(i["menu"], i["cancelled"]) for i in hidden[0]["items"]],
                         [("감바스 오일 파스타", False)])

    def test_cancelled_orders_come_back_when_asked(self):
        self.store.add(ticket("홀-4", "0004-0001", [("49. 빠네 크림 파스타", 2, [])], minutes_ago=5))
        self.store.add(ticket("홀-4", "0004-0002", [("49. 빠네 크림 파스타", -2, [])], "추가"))
        self.assertEqual(self.tables("빠네"), [])
        found = self.store.search("빠네", DAY, cancelled=True)
        self.assertEqual([(r["table"], r["qty"], r["cancelled"]) for r in found], [("홀-4", 2, True)])
        self.assertEqual([t["order_no"] for t in self.store.recent(DAY)], [])
        # 취소 전표 자체(0004-0002)는 주문이 아니라 취소 기록이라 목록에 나오지 않는다
        back = self.store.recent(DAY, cancelled=True)
        self.assertEqual([t["order_no"] for t in back], ["0004-0001"])
        self.assertEqual([(i["menu"], i["qty"], i["cancelled"]) for i in back[0]["items"]],
                         [("빠네 크림 파스타", 2, True)])

    def test_partial_cancel(self):
        self.store.add(ticket("홀-5", "0003-0001", [("49. 빠네 크림 파스타", 3, [])], minutes_ago=5))
        self.store.add(ticket("홀-5", "0003-0002", [("49. 빠네 크림 파스타", -1, [])], "추가"))
        self.assertEqual(self.tables("빠네"), [("홀-5", "빠네 크림 파스타", 2)])

    def test_partial_cancel_shows_the_cancelled_one_separately(self):
        """3개 중 1개만 취소하면 남은 2개와 취소된 1개를 따로 보여준다."""
        self.store.add(ticket("홀-5", "0003-0001", [("49. 빠네 크림 파스타", 3, [])], minutes_ago=5))
        self.store.add(ticket("홀-5", "0003-0002", [("49. 빠네 크림 파스타", -1, [])], "추가"))

        found = self.store.search("빠네", DAY, cancelled=True)
        self.assertEqual([(r["qty"], r["cancelled"]) for r in found], [(2, False), (1, True)])

        items = self.store.recent(DAY, cancelled=True)[0]["items"]
        self.assertEqual([(i["qty"], i["cancelled"]) for i in items], [(2, False), (1, True)])

        hidden = self.store.recent(DAY)[0]["items"]  # 취소를 감추면 남은 것만
        self.assertEqual([(i["qty"], i["cancelled"]) for i in hidden], [(2, False)])

    def test_cancel_does_not_touch_other_tables(self):
        self.store.add(ticket("홀-1", "0001-0001", [("34. 감바스 오일 파스타", 1, [])], minutes_ago=5))
        self.store.add(ticket("홀-2", "0002-0001", [("34. 감바스 오일 파스타", 1, [])], minutes_ago=4))
        self.store.add(ticket("홀-2", "0002-0002", [("34. 감바스 오일 파스타", -1, [])], "추가"))
        self.assertEqual(self.tables("감바스"), [("홀-1", "감바스 오일 파스타", 1)])

    def test_menus_for_the_buttons(self):
        self.store.add(ticket("홀-1", "0001-0001", [("34. 감바스 오일 파스타", 1, [])], minutes_ago=5))
        self.store.add(ticket("홀-2", "0002-0001", [("34. 감바스 오일 파스타", 2, []), ("20. 비프 찹 스테이크", 1, [])]))
        self.assertEqual(
            [(m["menu"], m["count"]) for m in self.store.menus(DAY)],
            [("감바스 오일 파스타", 3), ("비프 찹 스테이크", 1)],
        )

    def test_other_days_are_not_searched(self):
        self.store.add(ticket("홀-1", "0001-0001", [("34. 감바스 오일 파스타", 1, [])], minutes_ago=60 * 30))
        self.assertEqual(self.tables("감바스"), [])

    def test_offsets(self):
        self.assertIsNone(self.store.get_offset("a.log"))
        self.store.set_offset("a.log", 10)
        self.store.set_offset("a.log", 25)
        self.assertEqual(self.store.get_offset("a.log"), 25)

    def test_purge_old(self):
        self.store.add(ticket("홀-1", "0001-0001", [("34. 감바스 오일 파스타", 1, [])], minutes_ago=60 * 50))
        self.store.add(ticket("홀-2", "0002-0001", [("34. 감바스 오일 파스타", 1, [])]))
        self.assertEqual(self.store.purge_old(), 1)

    def test_search_text_is_not_a_pattern(self):
        self.store.add(ticket("홀-1", "0001-0001", [("34. 감바스 오일 파스타", 1, [])]))
        self.assertEqual(self.tables("%"), [])
        self.assertEqual(self.tables("_"), [])
        self.assertEqual(self.tables("   "), [])


class NormalizeTest(unittest.TestCase):
    def test_spaces_and_case(self):
        self.assertEqual(normalize(" 감바스  오일 Pasta "), "감바스오일pasta")


class BusinessDayTest(unittest.TestCase):
    def test_early_morning_is_the_previous_day(self):
        self.assertEqual(business_day(datetime(2026, 9, 23, 2, 0).timestamp()), "2026-09-22")
        self.assertEqual(business_day(datetime(2026, 9, 23, 6, 0).timestamp()), "2026-09-23")


class SearchWebTest(unittest.TestCase):
    def setUp(self):
        from what_number.search_web import serve_search

        self.store = MenuStore(":memory:")
        self.store.add(ticket("홀-1", "0006-0001", [("34. 감바스 오일 파스타", 1, ["(약)"])]))
        self.httpd, _ = serve_search(self.store, 0, lambda: {"ok": True, "errors": []})
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.read().decode("utf-8")

    def test_page(self):
        self.assertIn("몇번인가요", self.get("/"))

    def test_search_api(self):
        data = json.loads(self.get("/api/search?q=" + urllib.parse.quote("감바스")))
        self.assertEqual(data["results"][0]["table"], "홀-1")
        self.assertLess(abs(data["now"] - time.time()), 5)

    def test_overview_api(self):
        data = json.loads(self.get("/api/overview"))
        self.assertEqual(data["menus"][0]["menu"], "감바스 오일 파스타")
        self.assertEqual(data["recent"][0]["table"], "홀-1")
        self.assertEqual(data["summary"]["tickets"], 1)
        self.assertTrue(data["status"]["ok"])

    def test_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/nothing")


if __name__ == "__main__":
    unittest.main()

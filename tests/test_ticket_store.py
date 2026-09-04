import threading
import time
import unittest

from what_number.ticket_store import (
    DONE, OPEN, READY, TicketStore, business_day, clean_option, table_key_of,
)


def items(*names):
    return [{"menu": n} for n in names]


class HelperTest(unittest.TestCase):
    def test_option_none_becomes_empty(self):
        self.assertEqual(clean_option("(옵션 없음)"), "")

    def test_option_trailing_space_is_trimmed(self):
        """실제 자료에 '순한맛 ' 처럼 뒤에 공백이 붙은 것이 섞여 있다."""
        self.assertEqual(clean_option("순한맛 "), "순한맛")

    def test_table_key_ignores_spaces_and_dash(self):
        self.assertEqual(table_key_of("2층-21"), table_key_of("2층 - 21"))

    def test_different_tables_have_different_keys(self):
        self.assertNotEqual(table_key_of("2층-21"), table_key_of("2층-12"))

    def test_early_morning_belongs_to_previous_day(self):
        late = time.mktime(time.strptime("2026-09-05 02:30", "%Y-%m-%d %H:%M"))
        self.assertEqual(business_day(late), "2026-09-04")

    def test_evening_belongs_to_same_day(self):
        evening = time.mktime(time.strptime("2026-09-04 21:00", "%Y-%m-%d %H:%M"))
        self.assertEqual(business_day(evening), "2026-09-04")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = TicketStore(":memory:")

    def tearDown(self):
        self.store.close()

    def add(self, key="d1:0001-0001", table="2층-21", menus=("김치찌개", "공기밥"), **kw):
        return self.store.add_ticket(
            source="test", source_key=key, table_label=table, items=items(*menus), **kw
        )

    def test_items_keep_printed_order(self):
        tid = self.add(menus=("피자", "파스타", "샐러드"))
        got = [i.menu_name for i in self.store.get(tid).items]
        self.assertEqual(got, ["피자", "파스타", "샐러드"])

    def test_same_key_twice_is_one_ticket(self):
        first = self.add()
        second = self.add()
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.live_tickets()), 1)

    def test_serving_updates_count_and_rev(self):
        tid = self.add()
        before = self.store.rev
        item = self.store.get(tid).items[0]
        result = self.store.set_served(item.id, True)
        self.assertEqual(result["served_count"], 1)
        self.assertGreater(self.store.rev, before)

    def test_serving_twice_is_the_same(self):
        tid = self.add()
        item = self.store.get(tid).items[0]
        self.store.set_served(item.id, True)
        again = self.store.set_served(item.id, True)
        self.assertEqual(again["served_count"], 1)
        self.assertFalse(again["became_ready"])

    def test_last_item_makes_it_ready_once(self):
        tid = self.add(menus=("피자", "파스타"))
        first, second = self.store.get(tid).items
        self.assertFalse(self.store.set_served(first.id, True)["became_ready"])
        result = self.store.set_served(second.id, True)
        self.assertTrue(result["became_ready"])
        self.assertEqual(result["status"], READY)
        self.assertFalse(self.store.set_served(second.id, True)["became_ready"])

    def test_unchecking_returns_to_open(self):
        tid = self.add(menus=("피자",))
        item = self.store.get(tid).items[0]
        self.store.set_served(item.id, True)
        self.assertEqual(self.store.set_served(item.id, False)["status"], OPEN)

    def test_done_ticket_leaves_the_board_but_stays_searchable(self):
        tid = self.add(menus=("고르곤졸라 피자",))
        self.store.set_status(tid, DONE)
        self.assertEqual(self.store.live_tickets(), [])
        self.assertEqual(len(self.store.search_by_menu("고르곤졸라")), 1)

    def test_done_can_be_undone(self):
        tid = self.add()
        self.store.set_status(tid, DONE)
        self.store.set_status(tid, OPEN)
        self.assertEqual([t.id for t in self.store.live_tickets()], [tid])

    def test_unknown_ids_are_rejected_not_crashing(self):
        self.assertFalse(self.store.set_served(9999, True)["ok"])
        self.assertFalse(self.store.set_status(9999, DONE)["ok"])

    def test_same_bill_number_on_two_days_stays_separate(self):
        """영수증번호는 날마다 다시 쓰인다. 영업일이 열쇠에 들어가야 한다."""
        self.add(key="2026-09-03:0033-0001", table="2층-21")
        self.add(key="2026-09-04:0033-0001", table="2층-21")
        self.assertEqual(len(self.store.live_tickets()), 2)

    def test_second_ticket_for_a_table_does_not_disturb_the_first(self):
        first = self.add(key="d1:0033-0001", table="2층-21", menus=("피자",))
        item = self.store.get(first).items[0]
        self.store.set_served(item.id, True)
        self.add(key="d1:0033-0002", table="2층-21", menus=("파스타",), ticket_kind="추가")
        self.assertTrue(self.store.get(first).items[0].served)
        self.assertEqual(len(self.store.live_tickets()), 2)

    def test_oldest_ticket_comes_first(self):
        now = time.time()
        self.add(key="a", table="새 주문", received_at=now)
        self.add(key="b", table="오래된 주문", received_at=now - 600)
        self.assertEqual(self.store.live_tickets()[0].table_label, "오래된 주문")

    def test_search_only_returns_todays_orders(self):
        self.store.add_ticket(source="test", source_key="old", table_label="2층-1",
                              items=items("고르곤졸라 피자"), day="2020-01-01")
        self.assertEqual(self.store.search_by_menu("고르곤졸라"), [])

    def test_menu_list_counts_todays_menus(self):
        self.add(key="a", menus=("피자", "피자", "파스타"))
        listed = {row["menu"]: row["count"] for row in self.store.menu_list()}
        self.assertEqual(listed["피자"], 2)

    def test_purge_removes_old_tickets_and_their_items(self):
        old = self.add(key="old", received_at=time.time() - 40 * 86400)
        self.add(key="new")
        self.assertEqual(self.store.purge_old(), 1)
        self.assertIsNone(self.store.get(old))


class ConcurrencyTest(unittest.TestCase):
    """홀에서 두 사람이 동시에 눌러도 숫자가 어긋나면 안 된다."""

    def setUp(self):
        self.store = TicketStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_many_threads_checking_different_items(self):
        tid = self.store.add_ticket(
            source="t", source_key="k", table_label="2층-1",
            items=[{"menu": "메뉴%d" % i} for i in range(16)],
        )
        ids = [i.id for i in self.store.get(tid).items]
        threads = [threading.Thread(target=self.store.set_served, args=(i, True)) for i in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.store.get(tid).served_count, 16)

    def test_two_threads_on_the_last_item_produce_one_became_ready(self):
        tid = self.store.add_ticket(
            source="t", source_key="k2", table_label="2층-2", items=items("피자"),
        )
        item_id = self.store.get(tid).items[0].id
        flags = []

        def check():
            flags.append(self.store.set_served(item_id, True)["became_ready"])

        threads = [threading.Thread(target=check) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for f in flags if f), 1, "확인창이 두 번 뜨면 안 된다")


if __name__ == "__main__":
    unittest.main()

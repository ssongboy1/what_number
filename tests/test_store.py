import unittest

from what_number.store import OrderStore


def add(store, *, text="테이블 1", table="1", printer="192.168.0.50", now=1000.0, hash_="h1"):
    return store.add(
        content_hash=hash_,
        table_no=table,
        order_no=None,
        order_type="홀",
        printed_at=None,
        item_summary="김치찌개",
        raw_text=text,
        printer_ip=printer,
        now=now,
    )


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = OrderStore(":memory:", dedup_window=120.0, retention_hours=48.0)

    def tearDown(self):
        self.store.close()

    def test_add_and_read_back(self):
        add(self.store)
        recent = self.store.recent()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].table_no, "1")
        self.assertEqual(recent[0].printers, ["192.168.0.50"])

    def test_same_receipt_on_two_printers_is_one_order(self):
        """주방·바 프린터로 같은 전표가 나가도 목록에는 한 건만 보여야 한다."""
        first = add(self.store, printer="192.168.0.50", now=1000.0)
        second = add(self.store, printer="192.168.0.51", now=1001.0)
        self.assertEqual(first, second)
        self.assertEqual(self.store.count(), 1)
        order = self.store.recent()[0]
        self.assertEqual(order.printers, ["192.168.0.50", "192.168.0.51"])
        self.assertEqual(order.copies, 2)

    def test_same_receipt_much_later_is_a_new_order(self):
        add(self.store, now=1000.0)
        add(self.store, now=1000.0 + 600)
        self.assertEqual(self.store.count(), 2)

    def test_different_receipts_are_separate(self):
        add(self.store, hash_="h1")
        add(self.store, hash_="h2", table="2")
        self.assertEqual(self.store.count(), 2)

    def test_recent_is_newest_first(self):
        add(self.store, hash_="h1", table="1", now=1000.0)
        add(self.store, hash_="h2", table="2", now=2000.0)
        self.assertEqual([o.table_no for o in self.store.recent()], ["2", "1"])

    def test_filter_by_table(self):
        add(self.store, hash_="h1", table="1")
        add(self.store, hash_="h2", table="2")
        self.assertEqual([o.table_no for o in self.store.recent(table="2")], ["2"])

    def test_limit(self):
        for i in range(5):
            add(self.store, hash_=f"h{i}", now=1000.0 + i * 300)
        self.assertEqual(len(self.store.recent(limit=3)), 3)

    def test_tables_sorted_numerically(self):
        import time

        now = time.time()
        for i, table in enumerate(["10", "2", "1"]):
            add(self.store, hash_=f"h{i}", table=table, now=now)
        self.assertEqual(self.store.tables(), ["1", "2", "10"])

    def test_purge_removes_only_old_rows(self):
        add(self.store, hash_="old", now=1000.0)
        add(self.store, hash_="new", now=1000.0 + 48 * 3600)
        removed = self.store.purge_old(now=1000.0 + 48 * 3600 + 1)
        self.assertEqual(removed, 1)
        self.assertEqual(self.store.count(), 1)

    def test_to_dict_shape(self):
        add(self.store)
        payload = self.store.recent()[0].to_dict()
        for key in ("id", "table", "items", "raw_text", "order_type", "copies", "has_raster"):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()

import unittest

from what_number import orders

SAMPLE = """
     주방 주문서
================================
테이블 : 12
주문번호 : A-1043
2026-08-22 18:31:07
--------------------------------
김치찌개              x2
계란말이              x1
공기밥                 3
--------------------------------
합계                26,000
감사합니다
"""


class TableTest(unittest.TestCase):
    def test_colon_form(self):
        self.assertEqual(orders.find_table("테이블 : 12"), "12")

    def test_no_space_form(self):
        self.assertEqual(orders.find_table("테이블7"), "7")

    def test_reversed_form(self):
        self.assertEqual(orders.find_table("5번 테이블"), "5")

    def test_english_form(self):
        self.assertEqual(orders.find_table("TABLE #4"), "4")

    def test_room_form(self):
        self.assertEqual(orders.find_table("룸 : 3"), "3")

    def test_leading_zero_normalized(self):
        self.assertEqual(orders.find_table("테이블 : 07"), "7")

    def test_missing_table_returns_none(self):
        self.assertIsNone(orders.find_table("포장 주문입니다"))

    def test_custom_pattern(self):
        self.assertEqual(orders.find_table("자리 88", [r"자리\s*(\d+)"]), "88")


class OrderTypeTest(unittest.TestCase):
    def test_hall_is_default(self):
        self.assertEqual(orders.find_order_type("테이블 3"), "홀")

    def test_takeout(self):
        self.assertEqual(orders.find_order_type("[포장] 김밥 2줄"), "포장")

    def test_delivery_wins_over_takeout(self):
        self.assertEqual(orders.find_order_type("배달 · 포장용기 포함"), "배달")


class ItemTest(unittest.TestCase):
    def test_x_notation(self):
        self.assertEqual(orders.find_items("김치찌개  x2"), [("김치찌개", 2)])

    def test_gae_notation(self):
        self.assertEqual(orders.find_items("계란말이 1개"), [("계란말이", 1)])

    def test_column_notation(self):
        self.assertEqual(orders.find_items("제육볶음      3"), [("제육볶음", 3)])

    def test_total_line_is_not_an_item(self):
        self.assertEqual(orders.find_items("합계          26,000"), [])

    def test_separator_line_ignored(self):
        self.assertEqual(orders.find_items("--------------------"), [])


class BuildOrderTest(unittest.TestCase):
    def setUp(self):
        self.order = orders.build_order(SAMPLE)

    def test_table(self):
        self.assertEqual(self.order.table, "12")

    def test_order_no(self):
        self.assertEqual(self.order.order_no, "A-1043")

    def test_printed_at(self):
        self.assertEqual(self.order.printed_at, "2026-08-22 18:31:07")

    def test_items(self):
        self.assertEqual(
            self.order.items, [("김치찌개", 2), ("계란말이", 1), ("공기밥", 3)]
        )

    def test_summary(self):
        self.assertEqual(self.order.item_summary, "김치찌개 x2, 계란말이, 공기밥 x3")

    def test_total_line_excluded(self):
        self.assertNotIn("합계", self.order.item_summary)


class HashTest(unittest.TestCase):
    def test_same_receipt_same_hash(self):
        self.assertEqual(orders.content_hash("테이블 1\n김밥"), orders.content_hash("테이블 1\n김밥"))

    def test_whitespace_differences_ignored(self):
        """같은 주문서가 프린터마다 여백이 조금 달라도 한 건으로 묶여야 한다."""
        self.assertEqual(orders.content_hash("테이블 1  김밥"), orders.content_hash("테이블 1 김밥"))

    def test_different_receipt_different_hash(self):
        self.assertNotEqual(orders.content_hash("테이블 1"), orders.content_hash("테이블 2"))


if __name__ == "__main__":
    unittest.main()

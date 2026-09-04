import pathlib
import random
import unittest

from what_number import sample_menu, sample_tickets
from what_number.ticket_feed import TicketFeed
from what_number.ticket_store import TicketStore

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


class CatalogTest(unittest.TestCase):
    def test_catalog_is_not_empty(self):
        self.assertGreater(len(sample_menu.MENUS), 50)
        self.assertTrue(sample_menu.TICKET_SIZES)

    def test_every_menu_has_options_listed(self):
        for name, _ in sample_menu.MENUS:
            self.assertIn(name, sample_menu.OPTIONS_BY_MENU)

    def test_catalog_carries_no_store_or_sales_data(self):
        """카탈로그는 메뉴 목록이지 매출 자료가 아니다.

        설명 문구는 빼고 실제 데이터만 검사한다.
        """
        import re

        text = (SRC / "what_number" / "sample_menu.py").read_text(encoding="utf-8")
        body = re.sub(r'"""[\s\S]*?"""', "", text, count=1)  # 첫 설명 문단 제거

        for secret in ("라라코스트", "칠곡"):
            self.assertNotIn(secret, body, "매장 정보가 들어가면 안 된다")
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", body), "날짜가 들어가면 안 된다")
        # 금액으로 보이는 큰 숫자(만원 단위)가 없어야 한다
        self.assertEqual(re.findall(r"\d{4,}00", body), [], "금액이 들어가면 안 된다")

    def test_catalog_is_cp949_safe(self):
        text = (SRC / "what_number" / "sample_menu.py").read_text(encoding="utf-8")
        text.encode("cp949")  # 실패하면 한글 윈도우에서 출력 중 죽는다


class NoOpenpyxlInShippedCodeTest(unittest.TestCase):
    """openpyxl 은 개발 도구에서만 쓴다. exe 에 딸려 들어가면 안 된다."""

    def test_src_never_imports_openpyxl(self):
        offenders = [
            path.name for path in SRC.rglob("*.py")
            if "openpyxl" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


class SampleTicketTest(unittest.TestCase):
    def setUp(self):
        random.seed(1234)
        self.store = TicketStore(":memory:")
        self.feed = TicketFeed(self.store)

    def tearDown(self):
        self.store.close()

    def test_seed_fills_the_board(self):
        sample_tickets.seed(self.feed, 6)
        tickets = self.store.live_tickets()
        self.assertGreaterEqual(len(tickets), 6)
        self.assertTrue(all(t.items for t in tickets))

    def test_seed_includes_an_added_order_for_one_table(self):
        sample_tickets.seed(self.feed, 6)
        kinds = [t.ticket_kind for t in self.store.live_tickets()]
        self.assertIn("추가", kinds)

    def test_tables_use_the_printed_format(self):
        for _ in range(30):
            label = sample_tickets.random_table()
            self.assertTrue(
                label.startswith("포장") or "층-" in label, label
            )

    def test_seeded_tickets_are_oldest_first(self):
        sample_tickets.seed(self.feed, 5)
        times = [t.received_at for t in self.store.live_tickets()]
        self.assertEqual(times, sorted(times))


if __name__ == "__main__":
    unittest.main()

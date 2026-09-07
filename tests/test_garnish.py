import unittest

from what_number import garnish
from what_number.garnish_data import GARNISH
from what_number.hall_web import garnish_table


class LookupTest(unittest.TestCase):
    def test_finds_a_plain_menu(self):
        found = garnish.lookup("더블 포크 스테이크")
        self.assertEqual(found["sprinkle"], ["파슬리", "어린잎"])
        self.assertEqual(found["separate"], ["콘샐러드"])
        self.assertEqual(found["tools"], "나이프")

    def test_ignores_event_prefixes(self):
        """주문서에는 (리뷰), [말카드] 같은 표시가 붙어 나온다."""
        self.assertIsNotNone(garnish.lookup("[말카드]빠네크림파스타"))
        self.assertEqual(garnish.lookup("[말카드]빠네크림파스타")["menu"], "빠네 크림 파스타")

    def test_spacing_does_not_matter(self):
        self.assertEqual(
            garnish.lookup("치킨텐더샐러드")["menu"], garnish.lookup("치킨 텐더 샐러드")["menu"]
        )

    def test_drinks_have_no_garnish(self):
        for drink in ("크림생맥주 300ml", "자몽에이드", "삿포로생맥주"):
            self.assertIsNone(garnish.lookup(drink), drink)

    def test_unknown_menu_returns_none(self):
        self.assertIsNone(garnish.lookup("있을 리 없는 메뉴"))
        self.assertIsNone(garnish.lookup(""))

    def test_short_names_do_not_match_by_accident(self):
        """이름이 짧다고 아무 메뉴에나 붙으면 엉뚱한 안내가 나간다."""
        found = garnish.lookup("밥")
        self.assertIsNone(found)


class DataTest(unittest.TestCase):
    def test_table_is_not_empty(self):
        self.assertGreater(len(GARNISH), 50)

    def test_conditional_notes_are_kept_whole(self):
        """'고르곤졸라, 바질크림치즈 : 스위트너' 처럼 조건부 표기는 쪼개면 뜻이 바뀐다."""
        found = garnish.lookup("콰트로 피자")
        self.assertTrue(any(":" in step for step in found["sprinkle"]))

    def test_no_leftover_table_marks(self):
        for menu, (sprinkle, separate, tools, _) in GARNISH.items():
            for value in list(sprinkle) + list(separate) + [tools]:
                self.assertNotIn("|", value, menu)
                self.assertNotIn("▶", value, menu)
                self.assertNotIn("●", value, menu)

    def test_data_is_cp949_safe(self):
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[1] / "src" / "what_number"
        (path / "garnish_data.py").read_text(encoding="utf-8").encode("cp949")


class ApiTest(unittest.TestCase):
    def test_table_is_keyed_for_lookup(self):
        rows = garnish_table()["garnish"]
        self.assertIn(garnish.normalize("더블 포크 스테이크"), rows)
        self.assertEqual(rows[garnish.normalize("더블 포크 스테이크")]["tools"], "나이프")

    def test_entries_without_any_guidance_are_dropped(self):
        for row in garnish_table()["garnish"].values():
            self.assertTrue(row["sprinkle"] or row["separate"] or row["tools"])


if __name__ == "__main__":
    unittest.main()

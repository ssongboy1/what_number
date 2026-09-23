import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from what_number import kitchen_log
from what_number.demo import (
    kitchen_log_entry, kitchen_log_line, kitchen_ticket, write_sample_kitchen_log,
)

WHEN = datetime(2026, 9, 22, 17, 21, 15)


def ticket(*args, **kwargs):
    return kitchen_log.parse_ticket(kitchen_ticket(*args, **kwargs))


class ParseTicketTest(unittest.TestCase):
    def test_reads_the_header(self):
        found = ticket("홀-2", "0002-0001", [("43. 오븐 토마토 파스타", 1, [])], "신규", "가니", WHEN)
        self.assertEqual(found.kind, "신규")
        self.assertEqual(found.station, "가니")
        self.assertEqual(found.table, "홀-2")
        self.assertEqual(found.order_no, "0002-0001")
        self.assertEqual(found.pos_no, "POS-01")
        self.assertEqual(found.printed_at, WHEN.timestamp())

    def test_joins_names_cut_by_the_narrow_column(self):
        found = ticket("홀-1", "0006-0001", [("34. 감바스 오일 파스타", 1, [])], when=WHEN)
        line = found.lines[0]
        self.assertEqual((line.code, line.menu, line.quantity), ("34", "감바스 오일 파스타", 1))

    def test_options_belong_to_the_menu_above(self):
        found = ticket("홀-4", "0004-0001", [
            ("49. 빠네 크림 파스타", 2, ["크림 소스 추가"]),
            ("20. 비프 찹 스테이크", 1, ["순한맛", "(약)"]),
        ], when=WHEN)
        self.assertEqual(
            [(l.menu, l.quantity, l.options) for l in found.lines],
            [("빠네 크림 파스타", 2, ["크림 소스 추가"]), ("비프 찹 스테이크", 1, ["순한맛", "(약)"])],
        )

    def test_short_divider_between_menus_is_not_the_end(self):
        # 실측: 메뉴가 둘 이상이면 사이에 짧은 선이 들어간다. 여기서 끊으면 뒤 메뉴를 놓친다.
        found = ticket("홀-4", "0004-0003", [
            ("49. 빠네 크림 파스타", -1, ["크림 소스 추가"]),
            ("20. 비프 찹 스테이크", -1, ["순한맛"]),
        ], "추가", when=WHEN)
        self.assertEqual([(l.menu, l.quantity) for l in found.lines],
                         [("빠네 크림 파스타", -1), ("비프 찹 스테이크", -1)])
        self.assertTrue(all(l.cancelled for l in found.lines))

    def test_names_cut_at_a_space_keep_the_space(self):
        """메뉴명 칸(20칸)이 띄어쓰기 자리에서 끝나면 그 공백이 사라지기 쉽다.

        실제 매장 메뉴 283종으로 확인해 찾은 문제다. 세 가지 경우가 모두 나온다.
        """
        cases = [
            "43. 오븐 토마토 파스타",        # 글자 중간에서 잘림
            "(배달)베이컨 토마토 파스타",     # 띄어쓰기가 앞줄 끝에 딱 들어감
            "★31. 갈릭 로스트 치킨 스테이크",  # 띄어쓰기 자리에서 줄이 바뀜
            "카페라떼 [스페셜 티 원두]",
            "(HOT) 아메리카노 [스페셜티 원두]",
        ]
        for name in cases:
            found = ticket("홀 1", "0001-0001", [(name, 1, [])], when=WHEN)
            line = found.lines[0]
            read = (line.code + ". " + line.menu) if line.code else line.menu
            self.assertEqual(read, name)

    def test_options_cut_at_a_space_keep_the_space(self):
        option = "★모짜렐라 샐러드 레몬 요거트"
        found = ticket("홀 1", "0001-0001", [("20. 비프 찹 스테이크", 1, [option])], when=WHEN)
        self.assertEqual(found.lines[0].options, [option])

    def test_table_names_with_spaces(self):
        """매장에 따라 '홀 17', '배달 배민원1' 처럼 띄어쓰기로 적힌다."""
        for table in ("홀 17", "2층 28", "배달 배민원1", "포장 3"):
            found = ticket(table, "0001-0001", [("34. 감바스 오일 파스타", 1, [])], when=WHEN)
            self.assertEqual(found.table, table)

    def test_names_without_numbers(self):
        found = ticket("포장-3", "0010-0001", [("콜라 500", 2, [])], when=WHEN)
        self.assertEqual((found.lines[0].menu, found.lines[0].quantity, found.lines[0].code), ("콜라 500", 2, ""))

    def test_non_ticket_bytes(self):
        self.assertIsNone(kitchen_log.parse_ticket(b"\x1b@\n\n"))


class ReadEntriesTest(unittest.TestCase):
    def sample(self):
        return (
            kitchen_log_line(WHEN, "CTransData::SendDataLink", "SendDataLink start")
            + kitchen_log_entry(kitchen_ticket("홀-2", "0002-0001", [("43. 오븐 토마토 파스타", 1, [])], when=WHEN), WHEN)
            + kitchen_log_line(WHEN, "CTransData::SetUpdateFlag", "Print SUCCESS")
        )

    def test_splits_complete_entries(self):
        data = self.sample()
        entries, consumed = kitchen_log.read_entries(data)
        self.assertEqual(len(entries), 3)
        self.assertEqual(consumed, len(data))
        self.assertEqual(entries[1][1], "CTransData::SetPrintOrderData")
        self.assertIsNotNone(kitchen_log.ticket_from_entry(*entries[1][0:1], entries[1][2]))

    def test_waits_for_a_ticket_still_being_written(self):
        data = self.sample()
        cut = data.index(b"POS-01")  # 주문서 중간에서 끊긴 상태
        entries, consumed = kitchen_log.read_entries(data[:cut])
        self.assertEqual(len(entries), 1)
        first_line = kitchen_log_line(WHEN, "CTransData::SendDataLink", "SendDataLink start")
        self.assertEqual(consumed, len(first_line))  # 주문서 기록이 시작하는 곳에서 멈춘다

    def test_waits_for_an_unfinished_line(self):
        data = self.sample()
        entries, consumed = kitchen_log.read_entries(data[:-5])
        self.assertEqual(len(entries), 2)
        self.assertLess(consumed, len(data) - 5)

    def test_final_reads_everything(self):
        data = self.sample()
        cut = data.index(b"POS-01")
        entries, consumed = kitchen_log.read_entries(data[:cut], final=True)
        self.assertEqual(consumed, cut)
        self.assertEqual(len(entries), 2)


class MemoryOffsets:
    def __init__(self):
        self.saved = {}

    def get_offset(self, name):
        return self.saved.get(name)

    def set_offset(self, name, offset):
        self.saved[name] = offset


class LogFollowerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.seen = []

    def tearDown(self):
        self._tmp.cleanup()

    def append(self, name, data):
        with open(self.folder / name, "ab") as handle:
            handle.write(data)

    def entry(self, order_no, table="홀-1", when=WHEN):
        return kitchen_log_entry(kitchen_ticket(table, order_no, [("34. 감바스 오일 파스타", 1, [])], when=when), when)

    def test_follows_new_tickets(self):
        name = "kitchenPrinter_trace_20260922.log"
        self.append(name, self.entry("0001-0001"))
        follower = kitchen_log.LogFollower(self.folder, self.seen.append)
        self.assertEqual(follower.poll(), 1)
        self.assertEqual(follower.poll(), 0)
        self.append(name, self.entry("0002-0001"))
        self.assertEqual(follower.poll(), 1)
        self.assertEqual([t.order_no for t in self.seen], ["0001-0001", "0002-0001"])
        self.assertTrue(follower.status()["ok"])

    def test_half_written_ticket_is_read_once_complete(self):
        name = "kitchenPrinter_trace_20260922.log"
        whole = self.entry("0001-0001")
        self.append(name, whole[:120])
        follower = kitchen_log.LogFollower(self.folder, self.seen.append)
        self.assertEqual(follower.poll(), 0)
        self.append(name, whole[120:])
        self.assertEqual(follower.poll(), 1)

    def test_restart_continues_where_it_stopped(self):
        name = "kitchenPrinter_trace_20260922.log"
        self.append(name, self.entry("0001-0001"))
        offsets = MemoryOffsets()
        kitchen_log.LogFollower(self.folder, self.seen.append, offsets=offsets).poll()
        self.append(name, self.entry("0002-0001"))
        again = kitchen_log.LogFollower(self.folder, self.seen.append, offsets=offsets)
        self.assertEqual(again.poll(), 1)
        self.assertEqual([t.order_no for t in self.seen], ["0001-0001", "0002-0001"])

    def test_moves_to_the_next_day_file(self):
        self.append("kitchenPrinter_trace_20260922.log", self.entry("0001-0001"))
        follower = kitchen_log.LogFollower(self.folder, self.seen.append)
        follower.poll()
        self.append("kitchenPrinter_trace_20260922.log", self.entry("0002-0001"))
        tomorrow = WHEN + timedelta(days=1)
        self.append("kitchenPrinter_trace_20260923.log", self.entry("0001-0001", when=tomorrow))
        self.assertEqual(follower.poll(), 2)  # 어제 파일의 남은 것 + 오늘 것
        self.assertEqual(follower.current.name, "kitchenPrinter_trace_20260923.log")

    def test_file_recreated_smaller(self):
        name = "kitchenPrinter_trace_20260922.log"
        self.append(name, self.entry("0001-0001") + self.entry("0002-0001"))
        follower = kitchen_log.LogFollower(self.folder, self.seen.append)
        follower.poll()
        (self.folder / name).write_bytes(self.entry("0003-0001"))
        self.assertEqual(follower.poll(), 1)
        self.assertEqual(self.seen[-1].order_no, "0003-0001")

    def test_missing_files_are_reported_not_raised(self):
        follower = kitchen_log.LogFollower(self.folder, self.seen.append)
        self.assertEqual(follower.poll(), 0)
        self.assertIn("없습니다", follower.status()["errors"][0])

    def test_sample_log_is_readable(self):
        write_sample_kitchen_log(self.folder)
        follower = kitchen_log.LogFollower(self.folder, self.seen.append)
        self.assertEqual(follower.poll(), 8)  # 주문서 4장이 프린터 2대로


class FindLogFolderTest(unittest.TestCase):
    def test_finds_the_most_recent_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "PaLiDa" / "backup"
            new = Path(tmp) / "PaLiDa" / "bin" / "log"
            for folder, stamp in ((old, 1000), (new, 2000)):
                folder.mkdir(parents=True)
                path = folder / "kitchenPrinter_trace_20260922.log"
                path.write_bytes(b"x")
                os.utime(path, (stamp, stamp))
            (Path(tmp) / "PaLiDa" / "pos_trace_20260922.log").write_bytes(b"x")
            self.assertEqual(kitchen_log.find_log_folder([Path(tmp) / "PaLiDa"]), new)

    def test_nothing_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(kitchen_log.find_log_folder([tmp, os.path.join(tmp, "없음")]))


if __name__ == "__main__":
    unittest.main()


class SampleLogTest(unittest.TestCase):
    def test_every_ticket_lands_on_todays_business_day(self):
        """새벽 5시 직후에 만들어도 시험용 주문이 전날로 넘어가면 안 된다."""
        from what_number.menu_store import business_day

        with tempfile.TemporaryDirectory() as tmp:
            for hour, minute in ((5, 2), (5, 20), (12, 0), (4, 30)):
                now = datetime(2026, 9, 22, hour, minute)
                path = write_sample_kitchen_log(tmp, now=now)
                seen = []
                kitchen_log.LogFollower(tmp, seen.append).poll()
                days = {business_day(t.when) for t in seen}
                self.assertEqual(days, {business_day(now.timestamp())}, (hour, minute))
                path.unlink()

import contextlib
import io
import os
import struct
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from what_number import changes

ON_WINDOWS = os.name == "nt"


def notify_entry(action, name, last=False):
    encoded = name.encode("utf-16-le")
    size = 12 + len(encoded)
    size += (-size) % 4  # 윈도우는 항목을 4바이트 단위로 맞춘다
    body = struct.pack("<III", 0 if last else size, action, len(encoded)) + encoded
    return body + b"\x00" * (size - len(body))


def no_stat(_path):
    return (10, False)


class ParseNotificationsTest(unittest.TestCase):
    def test_reads_every_entry(self):
        data = notify_entry(3, "Palida\\Data\\pos.db") + notify_entry(1, "주문\\0001.json", last=True)
        self.assertEqual(
            changes.parse_notifications(data),
            [(3, "Palida\\Data\\pos.db"), (1, "주문\\0001.json")],
        )

    def test_empty_buffer(self):
        self.assertEqual(changes.parse_notifications(b""), [])


class ClassifyTest(unittest.TestCase):
    def test_pos_folder_comes_first(self):
        self.assertEqual(changes.classify("C:\\Palida\\Temp\\a.db", ["C:\\Palida"]), "pos")

    def test_windows_is_noise(self):
        self.assertEqual(changes.classify("C:\\Windows\\System32\\x.log"), "noise")
        self.assertEqual(changes.classify("C:\\pagefile.sys"), "noise")
        self.assertEqual(
            changes.classify("C:\\Users\\pos\\AppData\\Local\\Microsoft\\Edge\\x"), "noise"
        )

    def test_our_own_folder_is_ignored(self):
        self.assertEqual(changes.classify("D:\\tool\\data\\변화찾기.txt", ignore=["D:\\tool\\data"]), "noise")

    def test_temp_is_quiet(self):
        self.assertEqual(changes.classify("C:\\Users\\pos\\AppData\\Local\\Temp\\p.tmp"), "quiet")

    def test_program_data_elsewhere_is_normal(self):
        self.assertEqual(changes.classify("C:\\Users\\pos\\AppData\\Roaming\\VD\\order.db"), "normal")

    def test_similar_prefix_is_not_inside(self):
        self.assertEqual(changes.classify("C:\\Palida2\\a.db", ["C:\\Palida"]), "normal")

    def test_forward_slashes_and_case(self):
        self.assertEqual(changes.classify("c:/palida/data/a.db", ["C:\\PALIDA"]), "pos")


class AssignTest(unittest.TestCase):
    def test_change_goes_to_the_next_enter(self):
        hits, outside = changes.assign([100.0], [110.0, 115.0])
        self.assertEqual(hits, frozenset({0}))
        self.assertEqual(outside, 0)

    def test_one_change_is_not_counted_for_many_marks(self):
        # 엔터를 연달아 눌러도 한 번의 변화는 한 표시에만 붙는다.
        hits, _ = changes.assign([100.0], [101.0, 102.0, 103.0])
        self.assertEqual(len(hits), 1)

    def test_late_write_after_enter(self):
        hits, outside = changes.assign([205.0], [200.0])
        self.assertEqual(hits, frozenset({0}))
        self.assertEqual(outside, 0)

    def test_far_changes_are_outside(self):
        hits, outside = changes.assign([0.0, 500.0], [300.0])
        self.assertEqual(hits, frozenset())
        self.assertEqual(outside, 2)


class ChangeLogTest(unittest.TestCase):
    def test_quick_repeats_are_one_change(self):
        log = changes.ChangeLog(stat=no_stat)
        for moment in (0.0, 0.5, 1.9, 3.5):
            log.add("C:\\Palida\\pos.db", changes.MODIFIED, moment)
        log.add("C:\\Palida\\pos.db", changes.MODIFIED, 10.0)
        record = log.records["c:\\palida\\pos.db"]
        self.assertEqual(record.events, 5)
        self.assertEqual(record.bursts, [0.0, 10.0])

    def test_noise_is_only_counted(self):
        log = changes.ChangeLog(stat=no_stat)
        log.add("C:\\Windows\\System32\\a.log", changes.MODIFIED, 1.0)
        self.assertEqual(log.records, {})
        self.assertEqual(log.noise_events, 1)
        self.assertEqual(log.noise["C:\\Windows\\System32"], 1)

    def test_folders_themselves_are_dropped(self):
        log = changes.ChangeLog(stat=lambda path: (0, True))
        log.add("C:\\Palida\\Data", changes.MODIFIED, 1.0)
        log.add("C:\\Palida\\Data", changes.MODIFIED, 9.0)
        self.assertEqual(log.records, {})

    def test_size_before_comes_from_the_snapshot(self):
        log = changes.ChangeLog(["C:\\Palida"], stat=lambda path: (150, False))
        log.baseline = {"c:\\palida\\pos.db": 100}
        log.add("C:\\Palida\\pos.db", changes.MODIFIED, 1.0)
        record = log.records["c:\\palida\\pos.db"]
        self.assertEqual((record.size_before, record.size_first), (100, 150))

    def test_live_lines_are_throttled_and_skip_temp(self):
        log = changes.ChangeLog(["C:\\Palida"], stat=no_stat)
        log.add("C:\\Palida\\pos.db", changes.MODIFIED, 0.0)
        log.add("C:\\Palida\\pos.db", changes.MODIFIED, 5.0)  # 새 변화지만 30초 안
        log.add("C:\\Users\\a\\AppData\\Local\\Temp\\x.tmp", changes.ADDED, 5.0)
        live = log.take_live()
        self.assertEqual([(path, kind) for _, path, kind, _ in live], [("C:\\Palida\\pos.db", "pos")])
        log.add("C:\\Palida\\pos.db", changes.MODIFIED, 40.0)
        self.assertEqual(len(log.take_live()), 1)

    def test_mark_lists_changes_since_the_last_mark(self):
        log = changes.ChangeLog(["C:\\Palida"], stat=no_stat)
        log.add("C:\\Other\\old.txt", changes.MODIFIED, 100.0)
        self.assertEqual([p for p, _, _ in log.mark(110.0)], ["C:\\Other\\old.txt"])
        log.add("C:\\Other\\b.txt", changes.MODIFIED, 115.0)
        log.add("C:\\Palida\\pos.db", changes.MODIFIED, 116.0)
        recent = [path for path, _, _ in log.mark(120.0)]
        self.assertEqual(recent, ["C:\\Palida\\pos.db", "C:\\Other\\b.txt"])

    def test_ranking_prefers_files_that_follow_every_order(self):
        log = changes.ChangeLog(["C:\\Palida"], stat=no_stat)
        markers = [100.0, 400.0, 700.0]
        for mark in markers:
            log.add("C:\\Users\\a\\AppData\\Roaming\\VD\\order.db", changes.MODIFIED, mark - 5)
            log.add("C:\\Palida\\pos.log", changes.MODIFIED, mark - 5)
        for moment in range(0, 800, 20):  # 늘 바뀌는 기록 파일
            log.add("C:\\Palida\\heartbeat.log", changes.MODIFIED, float(moment))
        log.add("C:\\Other\\once.txt", changes.MODIFIED, 390.0)
        log.add("C:\\Other\\never.txt", changes.MODIFIED, 250.0)
        for mark in markers:
            log.mark(mark)
        names = [os.path.basename(record.path) for record, _, _ in log.ranked()]
        self.assertEqual(names[:2], ["pos.log", "order.db"])
        self.assertLess(names.index("order.db"), names.index("heartbeat.log"))
        self.assertLess(names.index("once.txt"), names.index("never.txt"))

    def test_missed_changes_are_added_once(self):
        log = changes.ChangeLog(["C:\\Palida"], stat=no_stat)
        log.add("C:\\Palida\\seen.db", changes.MODIFIED, 1.0)
        self.assertFalse(log.add_missed("C:\\Palida\\seen.db", changes.MODIFIED, 9.0))
        self.assertTrue(log.add_missed("C:\\Palida\\late.db", changes.ADDED, 9.0))
        self.assertIn("끝에 비교", log.records["c:\\palida\\late.db"].note)


class SnapshotTest(unittest.TestCase):
    def test_compare_finds_added_changed_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "keep.txt").write_text("a", encoding="utf-8")
            (folder / "grow.db").write_bytes(b"1")
            (folder / "gone.tmp").write_bytes(b"1")
            before = changes.snapshot([folder])
            (folder / "grow.db").write_bytes(b"12")
            (folder / "gone.tmp").unlink()
            (folder / "sub").mkdir()
            (folder / "sub" / "new.json").write_text("{}", encoding="utf-8")
            after = changes.snapshot([folder])
        found = {os.path.basename(path): action for path, action in changes.compare(before, after)}
        self.assertEqual(
            found, {"grow.db": changes.MODIFIED, "gone.tmp": changes.REMOVED, "new.json": changes.ADDED}
        )

    def test_incomplete_snapshot_does_not_guess_removals(self):
        before = {"c:\\a": ("C:\\a", 1, 1.0)}
        self.assertEqual(changes.compare(before, {}, complete=False), [])


class InspectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_recognizes_sqlite(self):
        path = self.folder / "pos.db"
        path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
        self.assertEqual(changes.inspect(path), ("SQLite 데이터베이스", []))

    def test_text_log_shows_last_lines_with_phone_hidden(self):
        path = self.folder / "order.log"
        lines = ["부팅"] + [f"12:00:{i:02d} 주문 테이블 {i} 김치찌개" for i in range(10)]
        lines.append("배달 010-1234-5678 password=abc")
        path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp949"))
        kind, shown = changes.inspect(path)
        self.assertEqual(kind, "글자 파일")
        self.assertEqual(len(shown), 6)
        self.assertIn("테이블 9", shown[-2])
        self.assertNotIn("1234-5678", shown[-1])
        self.assertNotIn("abc", shown[-1])

    def test_long_text_drops_the_cut_first_line(self):
        path = self.folder / "big.log"
        path.write_text("\n".join(f"줄 {i} 파스타 주문" for i in range(2000)), encoding="utf-8")
        kind, shown = changes.inspect(path)
        self.assertEqual(kind, "글자 파일")
        self.assertEqual(shown[-1], "줄 1999 파스타 주문")
        self.assertTrue(all(line.startswith("줄 ") for line in shown))

    def test_utf16_text(self):
        path = self.folder / "u16.log"
        path.write_bytes("\ufeff주문 테이블 3\r\n".encode("utf-16-le"))
        self.assertEqual(changes.inspect(path), ("글자 파일", ["주문 테이블 3"]))

    def test_unknown_binary_shows_first_bytes(self):
        path = self.folder / "blob.dat"
        path.write_bytes(bytes(range(1, 40)) + b"\x00" * 10)
        kind, shown = changes.inspect(path)
        self.assertIn("알 수 없는 형식", kind)
        self.assertIn("0102030405", kind)
        self.assertEqual(shown, [])

    def test_extension_hint(self):
        path = self.folder / "POS.FDB"
        path.write_bytes(b"\x01\x00\x39\x30" + b"\x00" * 100)
        self.assertEqual(changes.inspect(path)[0], "Firebird 데이터베이스")

    def test_missing_file(self):
        self.assertIn("지금은 없음", changes.inspect(self.folder / "none.db")[0])

    def test_read_shared_reads_a_range(self):
        path = self.folder / "range.bin"
        path.write_bytes(b"0123456789")
        self.assertEqual(changes.read_shared(path, 3, 4), b"3456")

    @unittest.skipUnless(ON_WINDOWS, "윈도우 전용")
    def test_reading_does_not_block_rename_or_delete(self):
        """읽는 동안에도 포스가 파일 이름을 바꾸거나 지울 수 있어야 한다."""
        import msvcrt

        path = self.folder / "rotate.log"
        path.write_bytes(b"line\n")
        kernel = changes._kernel32()
        raw = kernel.CreateFileW(
            str(path), changes.GENERIC_READ, changes.SHARE_ALL, None, changes.OPEN_EXISTING, 0, None
        )
        handle = os.fdopen(msvcrt.open_osfhandle(raw, os.O_RDONLY), "rb")
        try:
            os.replace(path, self.folder / "rotate.old")
        finally:
            handle.close()
        self.assertTrue((self.folder / "rotate.old").exists())


class CopyCandidatesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def file(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
        return str(path)

    def picked(self, log):
        rows = log.ranked()
        return [os.path.basename(r.path) for r in changes.copy_candidates(rows, sorted(log.markers))]

    def test_pos_files_that_changed_with_orders(self):
        pos = self.root / "Palida"
        log = changes.ChangeLog([pos], stat=no_stat)
        log.add(self.file("Palida/pos.db"), changes.MODIFIED, 95.0)
        log.add(self.file("Palida/unrelated.ini"), changes.MODIFIED, 500.0)
        log.mark(100.0)
        self.assertEqual(self.picked(log), ["pos.db"])

    def test_files_outside_need_to_follow_every_order_and_nothing_else(self):
        log = changes.ChangeLog([self.root / "Palida"], stat=no_stat)
        exact = self.file("VD/order.db")
        loose = self.file("Other/cache.bin")
        for moment in (95.0, 395.0):
            log.add(exact, changes.MODIFIED, moment)
        for moment in (95.0, 250.0, 395.0):  # 주문 사이에도 한 번 바뀐다
            log.add(loose, changes.MODIFIED, moment)
        log.mark(100.0)
        log.mark(400.0)
        self.assertEqual(self.picked(log), ["order.db"])

    def test_one_order_is_not_enough_outside_the_pos_folder(self):
        log = changes.ChangeLog([self.root / "Palida"], stat=no_stat)
        log.add(self.file("VD/order.db"), changes.MODIFIED, 95.0)
        log.mark(100.0)
        self.assertEqual(self.picked(log), [])

    def test_messengers_are_never_copied(self):
        pos = self.root / "Palida"
        log = changes.ChangeLog([pos], stat=no_stat)
        log.add(self.file("Palida/Kakao/chat.db"), changes.MODIFIED, 95.0)
        log.mark(100.0)
        self.assertEqual(self.picked(log), [])

    def test_missing_files_are_skipped(self):
        log = changes.ChangeLog([self.root / "Palida"], stat=no_stat)
        log.add(str(self.root / "Palida" / "gone.db"), changes.REMOVED, 95.0)
        log.mark(100.0)
        self.assertEqual(self.picked(log), [])


class InstallRootTest(unittest.TestCase):
    def test_custom_folder_goes_to_the_top(self):
        root = changes.install_root("C:\\Palida\\bin\\x64")
        self.assertEqual(str(root).replace("/", "\\").lower(), "c:\\palida")

    def test_program_files_keeps_the_vendor(self):
        root = changes.install_root("C:\\Program Files (x86)\\VD\\POS\\bin")
        self.assertEqual(str(root).replace("/", "\\").lower(), "c:\\program files (x86)\\vd")

    def test_user_folders_stay_as_they_are(self):
        root = changes.install_root("C:\\Users\\pos\\AppData\\Local\\VDPos")
        self.assertEqual(str(root).replace("/", "\\").lower(), "c:\\users\\pos\\appdata\\local\\vdpos")

    def test_custom_install_detection(self):
        self.assertTrue(changes.is_custom_install("C:\\Palida\\Palida.exe"))
        self.assertFalse(changes.is_custom_install("C:\\Program Files\\Git\\git.exe"))
        self.assertFalse(changes.is_custom_install("C:\\Windows\\explorer.exe"))


class KeyReaderTest(unittest.TestCase):
    def make(self, lines):
        feed = iter(lines)

        def read():
            try:
                return next(feed)
            except StopIteration:
                raise EOFError

        clock = iter(range(100, 200))
        return changes.KeyReader(read=read, clock=lambda: float(next(clock)))

    def test_each_enter_is_a_mark_and_finish_word_stops(self):
        reader = self.make(["", "  ", "끝", ""])
        reader.start()
        reader.join(2)
        self.assertEqual(reader.take_marks(), [100.0, 101.0])
        self.assertTrue(reader.finished.is_set())
        self.assertFalse(reader.closed)

    def test_finish_typed_in_the_wrong_keyboard_mode(self):
        for word in ("Rmx", "ㅂ", "q"):
            reader = self.make([word])
            reader.start()
            reader.join(2)
            self.assertTrue(reader.finished.is_set(), word)

    def test_no_console_is_not_an_error(self):
        reader = self.make([])
        reader.start()
        reader.join(2)
        self.assertTrue(reader.closed)
        self.assertFalse(reader.finished.is_set())
        self.assertEqual(reader.take_marks(), [])


@unittest.skipUnless(ON_WINDOWS, "윈도우 전용")
class DirectoryWatcherTest(unittest.TestCase):
    def test_hears_changes_and_stops_quickly(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            watcher = changes.DirectoryWatcher(tmp, lambda path, action, now: seen.append((action, path)))
            self.assertTrue(watcher.start(), watcher.errors)
            try:
                time.sleep(0.2)
                target = os.path.join(tmp, "sub", "order.db")
                os.makedirs(os.path.dirname(target))
                with open(target, "wb") as handle:
                    handle.write(b"x")
                deadline = time.time() + 5
                while time.time() < deadline and not any(path == target for _, path in seen):
                    time.sleep(0.05)
            finally:
                started = time.time()
                watcher.stop()
                stop_took = time.time() - started
        self.assertIn((changes.ADDED, target), seen)
        self.assertFalse(watcher.running)
        self.assertLess(stop_took, 2.0)

    def test_fixed_drives_include_the_system_drive(self):
        system = os.environ.get("SystemDrive", "C:") + "\\"
        self.assertIn(system.upper(), changes.fixed_drives())


@unittest.skipUnless(ON_WINDOWS, "윈도우 전용")
class SessionTest(unittest.TestCase):
    def test_finds_the_order_file_and_writes_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            pos = Path(tmp) / "Palida"
            (pos / "Data").mkdir(parents=True)
            database = pos / "Data" / "pos.db"
            database.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
            (pos / "Data" / "pos.db-wal").write_bytes(b"recent orders")

            session = changes.Session([pos], roots=[str(pos)])
            session.start(probe=False)
            try:
                time.sleep(0.3)
                with open(database, "ab") as handle:
                    handle.write(b"order")
                deadline = time.time() + 5
                while time.time() < deadline and not session.log.records:
                    time.sleep(0.05)
                session.log.mark(time.time())
            finally:
                session.finish(probe=False)

            archive_path = session.pack(Path(tmp) / "out" / "result.zip")
            with zipfile.ZipFile(str(archive_path)) as archive:
                names = archive.namelist()
                copied = archive.read("files/1_pos.db")
                wal = archive.read("files/1_pos.db-wal")
                packed_report = archive.read("report.txt").decode("utf-8")
            report = session.report()
            saved = session.save(Path(tmp) / "out" / "result.txt")
            summary = "\n".join(session.console_summary())

        self.assertIn(str(database), report)
        self.assertIn("SQLite 데이터베이스", report)
        self.assertIn("주문 표시 1번 중 1번", report)
        self.assertIn("+5 바이트", report)
        self.assertTrue(saved.name.endswith(".txt"))
        self.assertIn("pos.db", summary)
        self.assertTrue(copied.endswith(b"order"))
        self.assertEqual(wal, b"recent orders")
        self.assertIn("report.txt", names)
        self.assertIn("files/1_pos.db", packed_report)
        self.assertEqual(session.copied_count, 2)
        report.encode("cp949")  # 결과를 창에 찍어도 죽지 않아야 한다


@unittest.skipUnless(ON_WINDOWS, "윈도우 전용")
class FindChangesCommandTest(unittest.TestCase):
    def test_runs_without_a_console_and_saves_the_result(self):
        """더블클릭이 아닌 환경(입력 없음)에서도 정해진 시간 뒤 끝나고 결과를 남긴다."""
        from what_number import app

        with tempfile.TemporaryDirectory() as tmp:
            pos = Path(tmp) / "Palida"
            pos.mkdir()
            with mock.patch("what_number.config.app_dir", return_value=Path(tmp)), \
                    mock.patch("sys.stdin", io.StringIO("")), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                code = app.main(["--changes", str(pos), "--seconds", "1", "--no-browser"])
            reports = list((Path(tmp) / "data").glob("변화찾기_*.txt"))
            archives = list((Path(tmp) / "data").glob("변화찾기_*.zip"))
            text = reports[0].read_text(encoding="utf-8") if reports else ""

        self.assertEqual(code, 0)
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(archives), 1)
        self.assertIn("변화찾기 결과", text)
        self.assertIn(str(pos), out.getvalue())


if __name__ == "__main__":
    unittest.main()

"""패킷 → 전표 → 화면 까지 실제 흐름 그대로 확인한다."""

import json
import socket
import struct
import unittest
import urllib.request

from what_number import config as config_module
from what_number.app import Application
from what_number.sniffer import parse_ipv4_tcp
from what_number.store import OrderStore
from what_number.web import serve


def cp949(text):
    return text.encode("cp949")


def receipt_bytes(table, items, order_no="A-1001"):
    """실제 포스가 보내는 것과 비슷한 주방 주문서를 만든다."""
    out = bytearray()
    out += b"\x1b@"  # 초기화
    out += b"\x1ba\x01" + b"\x1b!\x30" + cp949("주방 주문서") + b"\n"  # 가운데/큰글씨
    out += b"\x1b!\x00" + b"\x1ba\x00"
    out += cp949("================================") + b"\n"
    out += b"\x1b!\x30" + cp949(f"테이블 : {table}") + b"\x1b!\x00" + b"\n"
    out += cp949(f"주문번호 : {order_no}") + b"\n"
    out += cp949("2026-08-22 18:31:07") + b"\n"
    out += cp949("--------------------------------") + b"\n"
    for name, qty in items:
        out += cp949(f"{name}") + b"\t" + cp949(f"x{qty}") + b"\n"
    out += cp949("--------------------------------") + b"\n"
    out += b"\x1dk\x04" + f"{order_no}".encode() + b"\x00"  # 바코드
    out += b"\x1bd\x03"  # 3줄 이송
    out += b"\x1dV\x42\x00"  # 용지 절단
    return bytes(out)


def packet(payload, *, src="192.168.0.10", dst="192.168.0.50", seq=1000, flags=0x18, sport=50000):
    tcp = struct.pack("!HHIIBBHHH", sport, 9100, seq, 0, 5 << 4, flags, 8192, 0, 0)
    body = tcp + payload
    ip = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), 0, 0, 64, 6, 0,
        socket.inet_aton(src), socket.inet_aton(dst),
    )
    return ip + body


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        cfg = config_module.Config(keep_raw_dumps=0, open_browser=False)
        self.app = Application(cfg, store=OrderStore(":memory:"))
        self.app.log = lambda *args: None

    def tearDown(self):
        self.app.store.close()

    def feed(self, data, *, dst="192.168.0.50", seq=1000, sport=50000):
        """패킷을 조각내어 실제처럼 여러 번에 나눠 흘려보낸다."""
        chunk = 512
        offset = 0
        while offset < len(data):
            piece = data[offset : offset + chunk]
            seg = parse_ipv4_tcp(packet(piece, dst=dst, seq=seq + offset, sport=sport), {9100})
            self.app.reassembler.add(seg)
            offset += len(piece)
        fin = parse_ipv4_tcp(
            packet(b"", dst=dst, seq=seq + len(data), flags=0x11, sport=sport), {9100}
        )
        self.app.reassembler.add(fin)

    def test_full_pipeline(self):
        self.feed(receipt_bytes(12, [("김치찌개", 2), ("계란말이", 1)]))
        stored = self.app.store.recent()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].table_no, "12")
        self.assertEqual(stored[0].order_no, "A-1001")
        self.assertIn("김치찌개", stored[0].item_summary)
        self.assertFalse(stored[0].has_raster)

    def test_same_order_to_two_printers_is_one_row(self):
        data = receipt_bytes(7, [("제육볶음", 1)])
        self.feed(data, dst="192.168.0.50", sport=50000)
        self.feed(data, dst="192.168.0.51", seq=7000, sport=50001)
        self.assertEqual(self.app.store.count(), 1)
        self.assertEqual(self.app.store.recent()[0].copies, 2)

    def test_two_different_orders(self):
        self.feed(receipt_bytes(3, [("김밥", 1)], order_no="A-1"), sport=50000)
        self.feed(receipt_bytes(4, [("라면", 1)], order_no="A-2"), seq=9000, sport=50001)
        self.assertEqual([o.table_no for o in self.app.store.recent()], ["4", "3"])

    def test_image_receipt_is_flagged_not_dropped(self):
        image = b"\x1b@" + b"\x1dv0\x00\x30\x00\x40\x00" + bytes(0x30 * 0x40) + b"\x1dV\x42\x00"
        self.feed(image)
        stored = self.app.store.recent()
        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0].has_raster)

    def test_printer_status_polling_creates_no_order(self):
        """프린터 상태 조회 같은 통신이 빈 주문으로 쌓이면 안 된다."""
        self.feed(b"\x10\x04\x01")
        self.assertEqual(self.app.store.count(), 0)

    def test_web_api_serves_stored_orders(self):
        self.feed(receipt_bytes(21, [("돈까스", 1)]))
        httpd, _ = serve(self.app.store, 0, lambda: {"capturing": True})
        port = httpd.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/orders", timeout=5) as res:
                payload = json.loads(res.read().decode("utf-8"))
            self.assertEqual(payload["orders"][0]["table"], "21")
            self.assertEqual(payload["tables"], ["21"])
            self.assertTrue(payload["capturing"])

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as res:
                page = res.read().decode("utf-8")
            self.assertIn("최근 주문", page)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()


class DemoReceiptTest(unittest.TestCase):
    """시연용 전표도 실제 경로를 그대로 지나야 한다."""

    def test_demo_receipt_round_trip(self):
        from what_number.demo import make_receipt

        app = Application(config_module.Config(keep_raw_dumps=0), store=OrderStore(":memory:"))
        app.log = lambda *args: None
        try:
            app.feed(make_receipt(9, "D-0001", [("김치찌개", 2)]))
            stored = app.store.recent()[0]
            self.assertEqual(stored.table_no, "9")
            self.assertEqual(stored.order_no, "D-0001")
            self.assertIn("김치찌개", stored.item_summary)
        finally:
            app.store.close()


class RasterHandlingTest(unittest.TestCase):
    """대부분의 전표에는 가게 로고가 이미지로 들어간다. 그것 때문에 경고가 뜨면 안 된다."""

    def setUp(self):
        self.app = Application(config_module.Config(keep_raw_dumps=0), store=OrderStore(":memory:"))
        self.app.log = lambda *args: None

    def tearDown(self):
        self.app.store.close()

    @staticmethod
    def _logo(width_bytes=8, height=16):
        header = struct.pack("<HH", width_bytes, height)  # xL xH yL yH
        return b"\x1dv0\x00" + header + bytes(width_bytes * height)

    def test_logo_plus_text_is_not_flagged_unreadable(self):
        self.app.feed(b"\x1b@" + self._logo() + cp949("테이블 6") + b"\n" + cp949("김밥  x1") + b"\n")
        stored = self.app.store.recent()[0]
        self.assertEqual(stored.table_no, "6")
        self.assertFalse(stored.has_raster, "로고가 있다고 '읽을 수 없음'으로 표시하면 안 된다")

    def test_image_only_receipt_is_flagged(self):
        self.app.feed(b"\x1b@" + self._logo(48, 200) + b"\x1dV\x42\x00")
        self.assertTrue(self.app.store.recent()[0].has_raster)

    def test_two_different_image_receipts_stay_separate(self):
        """글자가 없어 지문이 같아지는 탓에 서로 다른 주문이 하나로 합쳐지면 안 된다."""
        self.app.feed(b"\x1b@" + self._logo(48, 200) + b"\x1dV\x42\x00")
        self.app.feed(b"\x1b@" + self._logo(48, 260) + b"\x1dV\x42\x00")
        self.assertEqual(self.app.store.count(), 2)

    def test_identical_image_receipt_to_two_printers_still_merges(self):
        data = b"\x1b@" + self._logo(48, 200) + b"\x1dV\x42\x00"
        self.app.feed(data, printer_ip="192.168.0.50")
        self.app.feed(data, printer_ip="192.168.0.51")
        self.assertEqual(self.app.store.count(), 1)


class ConsoleEncodingTest(unittest.TestCase):
    """콘솔이 한글을 담지 못하는 환경에서도 프로그램이 죽으면 안 된다."""

    def test_runs_under_a_console_that_cannot_encode_korean(self):
        import os
        import subprocess
        import sys
        import tempfile

        from what_number.demo import make_receipt

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            sample = os.path.join(tmp, "sample.bin")
            with open(sample, "wb") as f:
                f.write(make_receipt(17, "B-0042", [("김치찌개", 2)]))

            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.join(root, "src")
            env["PYTHONIOENCODING"] = "cp1252"  # 한글을 담지 못하는 인코딩
            env.pop("PYTHONUTF8", None)
            result = subprocess.run(
                [sys.executable, os.path.join(root, "launcher.py"), "--replay", sample],
                env=env,
                capture_output=True,
                timeout=60,
            )

        self.assertEqual(
            result.returncode, 0, f"한글 출력에서 죽었다:\n{result.stderr.decode('utf-8', 'replace')}"
        )
        self.assertIn("B-0042", result.stdout.decode("utf-8", "replace"))

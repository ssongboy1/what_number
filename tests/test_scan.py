import unittest

from what_number.reassembly import Segment
from what_number.scan import Scanner, looks_like_receipt
from what_number.sniffer import parse_ipv4_tcp


def cp949(text):
    return text.encode("cp949")


def seg(payload, ip="192.168.0.50", port=9100):
    return Segment("192.168.0.10", 50000, ip, port, 1000, payload, ts=100.0)


class LooksLikeReceiptTest(unittest.TestCase):
    def test_escpos_init_command(self):
        self.assertTrue(looks_like_receipt(b"\x1b@" + cp949("테이블 3") + b"\n"))

    def test_cut_command(self):
        self.assertTrue(looks_like_receipt(b"abcdefgh\x1dV\x42\x00"))

    def test_plain_korean_without_commands(self):
        self.assertTrue(looks_like_receipt(cp949("테이블 3 김치찌개 두개")))

    def test_https_noise_is_not_a_receipt(self):
        self.assertFalse(looks_like_receipt(bytes(range(200))))

    def test_short_data_is_not_a_receipt(self):
        self.assertFalse(looks_like_receipt(b"\x1b@"))

    def test_english_text_is_not_a_receipt(self):
        self.assertFalse(looks_like_receipt(b"GET /index.html HTTP/1.1\r\nHost: x\r\n"))


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.scanner = Scanner()

    def test_counts_traffic_per_target(self):
        self.scanner.add(seg(b"hello world", port=9100))
        self.scanner.add(seg(b"more data!!", port=9100))
        target = self.scanner.targets[("192.168.0.50", 9100)]
        self.assertEqual(target.packets, 2)
        self.assertEqual(target.total_bytes, 22)

    def test_separates_targets_by_ip_and_port(self):
        self.scanner.add(seg(b"aaaaaaaa", ip="192.168.0.50", port=9100))
        self.scanner.add(seg(b"bbbbbbbb", ip="192.168.0.51", port=9100))
        self.scanner.add(seg(b"cccccccc", ip="192.168.0.50", port=9600))
        self.assertEqual(len(self.scanner.targets), 3)

    def test_flags_receipt_traffic(self):
        self.scanner.add(seg(b"\x1b@" + cp949("테이블 7") + b"\n", port=9600))
        target = self.scanner.targets[("192.168.0.50", 9600)]
        self.assertEqual(target.receipt_hits, 1)

    def test_ignores_empty_payloads(self):
        self.scanner.add(seg(b""))
        self.assertEqual(self.scanner.targets, {})

    def test_report_names_the_receipt_port(self):
        self.scanner.add(seg(b"\x1b@" + cp949("테이블 7 김치찌개") + b"\n", port=9600))
        report = self.scanner.report()
        self.assertIn("9600", report)
        self.assertIn("★", report)
        self.assertIn("printer_ports", report)

    def test_report_when_nothing_looks_like_a_receipt(self):
        self.scanner.add(seg(b"GET / HTTP/1.1\r\nHost: example.com\r\n", port=8080))
        report = self.scanner.report()
        self.assertIn("시리얼", report)

    def test_report_when_no_traffic_at_all(self):
        report = self.scanner.report()
        self.assertIn("주문을 한 건 넣어보세요", report)

    def test_web_noise_is_hidden_but_receipts_are_never_hidden(self):
        self.scanner.add(seg(b"x" * 5000, port=443))
        self.scanner.add(seg(b"\x1b@" + cp949("테이블 2") + b"\n", port=443))
        report = self.scanner.report()
        self.assertIn("443", report, "주문서가 나간 포트는 흔한 포트여도 숨기면 안 된다")

    def test_preview_shows_readable_text(self):
        self.scanner.add(seg(b"\x1b@" + cp949("테이블 7 김치찌개") + b"\n", port=9600))
        self.assertIn("테이블", self.scanner.targets[("192.168.0.50", 9600)].preview)


class ScanAllPortsTest(unittest.TestCase):
    """탐색 모드는 포트를 가리지 않고 모두 받아야 한다."""

    @staticmethod
    def _packet(dst_port):
        import socket
        import struct

        tcp = struct.pack("!HHIIBBHHH", 50000, dst_port, 1000, 0, 5 << 4, 0x18, 8192, 0, 0)
        body = tcp + b"DATA"
        return (
            struct.pack(
                "!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), 0, 0, 64, 6, 0,
                socket.inet_aton("192.168.0.10"), socket.inet_aton("192.168.0.50"),
            )
            + body
        )

    def test_none_accepts_every_port(self):
        for port in (9100, 9600, 4001, 12345):
            self.assertIsNotNone(parse_ipv4_tcp(self._packet(port), None))

    def test_filter_still_works_when_given(self):
        self.assertIsNotNone(parse_ipv4_tcp(self._packet(9600), {9600}))
        self.assertIsNone(parse_ipv4_tcp(self._packet(9600), {9100}))


if __name__ == "__main__":
    unittest.main()

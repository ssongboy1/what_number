import socket
import struct
import unittest

from what_number.sniffer import parse_ipv4_tcp


def ipv4_tcp(payload=b"", *, dst_port=9100, seq=1000, flags=0x18, ihl=5, proto=6):
    tcp = struct.pack("!HHIIBBHHH", 50000, dst_port, seq, 0, 5 << 4, flags, 8192, 0, 0)
    body = tcp + payload
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        (4 << 4) | ihl,
        0,
        20 + len(body),
        0,
        0,
        64,
        proto,
        0,
        socket.inet_aton("192.168.0.10"),
        socket.inet_aton("192.168.0.50"),
    )
    return ip + body


class ParsePacketTest(unittest.TestCase):
    def test_extracts_payload_and_addresses(self):
        seg = parse_ipv4_tcp(ipv4_tcp(b"HELLO"), {9100})
        self.assertIsNotNone(seg)
        self.assertEqual(seg.payload, b"HELLO")
        self.assertEqual(seg.src_ip, "192.168.0.10")
        self.assertEqual(seg.dst_ip, "192.168.0.50")
        self.assertEqual(seg.dst_port, 9100)
        self.assertEqual(seg.seq, 1000)

    def test_other_ports_ignored(self):
        self.assertIsNone(parse_ipv4_tcp(ipv4_tcp(b"X", dst_port=443), {9100}))

    def test_multiple_watched_ports(self):
        self.assertIsNotNone(parse_ipv4_tcp(ipv4_tcp(b"X", dst_port=515), {9100, 515}))

    def test_non_tcp_ignored(self):
        self.assertIsNone(parse_ipv4_tcp(ipv4_tcp(b"X", proto=17), {9100}))

    def test_fin_flag(self):
        seg = parse_ipv4_tcp(ipv4_tcp(b"", flags=0x11), {9100})
        self.assertTrue(seg.fin)

    def test_rst_flag(self):
        seg = parse_ipv4_tcp(ipv4_tcp(b"", flags=0x14), {9100})
        self.assertTrue(seg.rst)

    def test_ip_options_are_skipped(self):
        """IP 헤더에 옵션이 붙어 길어져도 본문 위치를 정확히 찾아야 한다."""
        packet = bytearray(ipv4_tcp(b"DATA", ihl=6))
        packet[0] = (4 << 4) | 6
        packet[2:4] = struct.pack("!H", len(packet) + 4)
        packet[20:20] = b"\x00\x00\x00\x00"  # 4바이트 옵션 삽입
        seg = parse_ipv4_tcp(bytes(packet), {9100})
        self.assertEqual(seg.payload, b"DATA")

    def test_trailing_ethernet_padding_is_trimmed(self):
        """작은 패킷 뒤에 붙는 채움 바이트가 전표에 섞이면 안 된다."""
        seg = parse_ipv4_tcp(ipv4_tcp(b"AB") + b"\x00" * 20, {9100})
        self.assertEqual(seg.payload, b"AB")

    def test_truncated_packet_is_ignored(self):
        self.assertIsNone(parse_ipv4_tcp(b"\x45\x00\x00", {9100}))

    def test_ipv6_ignored(self):
        packet = bytearray(ipv4_tcp(b"X"))
        packet[0] = 6 << 4
        self.assertIsNone(parse_ipv4_tcp(bytes(packet), {9100}))


if __name__ == "__main__":
    unittest.main()

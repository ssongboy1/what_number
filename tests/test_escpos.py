import unittest

from what_number import escpos


def cp949(text: str) -> bytes:
    return text.encode("cp949")


class DecodeTextTest(unittest.TestCase):
    def test_cp949_korean(self):
        text, encoding = escpos.decode_text(cp949("김치찌개"))
        self.assertEqual(text, "김치찌개")
        self.assertEqual(encoding, "cp949")

    def test_utf8_korean(self):
        text, encoding = escpos.decode_text("김치찌개".encode("utf-8"))
        self.assertEqual(text, "김치찌개")
        self.assertEqual(encoding, "utf-8")

    def test_broken_bytes_do_not_raise(self):
        text, _ = escpos.decode_text(b"\xff\xfe abc")
        self.assertIn("abc", text)


class ParseTest(unittest.TestCase):
    def test_strips_commands_and_keeps_text(self):
        data = b"\x1b@" + b"\x1ba\x01" + b"\x1b!\x30" + cp949("테이블 7") + b"\n"
        receipt = escpos.parse(data)
        self.assertEqual(receipt.text, "테이블 7")
        self.assertEqual(receipt.unknown_commands, [])

    def test_multiple_lines(self):
        data = cp949("테이블 3") + b"\n" + cp949("제육볶음  x2") + b"\n" + cp949("공기밥  1") + b"\n"
        receipt = escpos.parse(data)
        self.assertEqual(receipt.lines, ["테이블 3", "제육볶음  x2", "공기밥  1"])

    def test_cut_command_detected(self):
        receipt = escpos.parse(cp949("주문") + b"\n\x1dV\x41\x10")
        self.assertTrue(receipt.cut)
        self.assertEqual(receipt.text, "주문")

    def test_cut_without_extra_arg(self):
        receipt = escpos.parse(cp949("주문") + b"\n\x1dV\x00" + cp949("다음"))
        self.assertTrue(receipt.cut)
        self.assertEqual(receipt.text, "주문\n다음")

    def test_raster_image_is_skipped_not_printed_as_garbage(self):
        # GS v 0 m xL xH yL yH + 데이터 (가로 2바이트 x 세로 3줄 = 6바이트)
        image = b"\x1dv0\x00\x02\x00\x03\x00" + bytes(6)
        receipt = escpos.parse(image + cp949("테이블 5") + b"\n")
        self.assertTrue(receipt.has_raster)
        self.assertEqual(receipt.raster_bytes, 6)
        self.assertEqual(receipt.text, "테이블 5")

    def test_esc_star_bitmap_is_skipped(self):
        # ESC * m=33(24점) nL=2 nH=0 → 2 * 3 = 6바이트
        data = b"\x1b*\x21\x02\x00" + bytes(6) + cp949("끝") + b"\n"
        receipt = escpos.parse(data)
        self.assertTrue(receipt.has_raster)
        self.assertEqual(receipt.text, "끝")

    def test_barcode_null_terminated_is_skipped(self):
        data = b"\x1dk\x04" + b"8801234567890\x00" + cp949("테이블 2") + b"\n"
        receipt = escpos.parse(data)
        self.assertEqual(receipt.text, "테이블 2")

    def test_barcode_length_prefixed_is_skipped(self):
        payload = b"12345678"
        data = b"\x1dk\x49" + bytes([len(payload)]) + payload + cp949("테이블 9") + b"\n"
        receipt = escpos.parse(data)
        self.assertEqual(receipt.text, "테이블 9")

    def test_drawer_kick_does_not_eat_text(self):
        data = b"\x1bp\x00\x19\xfa" + cp949("테이블 11") + b"\n"
        self.assertEqual(escpos.parse(data).text, "테이블 11")

    def test_tab_positions_null_terminated(self):
        data = b"\x1bD\x08\x10\x18\x00" + cp949("항목") + b"\n"
        self.assertEqual(escpos.parse(data).text, "항목")

    def test_carriage_return_does_not_duplicate_lines(self):
        data = cp949("한줄") + b"\r\n" + cp949("두줄") + b"\r\n"
        self.assertEqual(escpos.parse(data).lines, ["한줄", "두줄"])

    def test_unknown_command_is_recorded(self):
        receipt = escpos.parse(b"\x1b\xf0\x01" + cp949("남은글자") + b"\n")
        self.assertEqual(receipt.unknown_commands, ["ESC 0xf0"])
        self.assertEqual(receipt.text, "남은글자")

    def test_truncated_raster_header_does_not_hang(self):
        receipt = escpos.parse(b"\x1dv0\x00\x02")
        self.assertTrue(receipt.is_empty)

    def test_empty_input(self):
        receipt = escpos.parse(b"")
        self.assertTrue(receipt.is_empty)
        self.assertEqual(receipt.lines, [])


if __name__ == "__main__":
    unittest.main()

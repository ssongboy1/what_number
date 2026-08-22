import unittest

from what_number.reassembly import PrintJob, Reassembler, Segment


def seg(seq, payload, *, fin=False, rst=False, ts=100.0, src_port=50000):
    return Segment(
        src_ip="192.168.0.10",
        src_port=src_port,
        dst_ip="192.168.0.50",
        dst_port=9100,
        seq=seq,
        payload=payload,
        fin=fin,
        rst=rst,
        ts=ts,
    )


class ReassemblyTest(unittest.TestCase):
    def setUp(self):
        self.jobs: list[PrintJob] = []
        self.r = Reassembler(self.jobs.append, idle_seconds=2.0)

    def test_single_segment_flushed_on_fin(self):
        self.r.add(seg(1000, b"HELLO"))
        self.r.add(seg(1005, b"", fin=True))
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.jobs[0].data, b"HELLO")
        self.assertEqual(self.jobs[0].dst_ip, "192.168.0.50")

    def test_segments_joined_in_order(self):
        self.r.add(seg(1000, b"AAA"))
        self.r.add(seg(1003, b"BBB"))
        self.r.add(seg(1006, b"CCC", fin=True))
        self.assertEqual(self.jobs[0].data, b"AAABBBCCC")

    def test_out_of_order_segments_are_sorted(self):
        self.r.add(seg(1003, b"BBB"))
        self.r.add(seg(1000, b"AAA"))
        self.r.add(seg(1006, b"CCC", fin=True))
        self.assertEqual(self.jobs[0].data, b"AAABBBCCC")

    def test_retransmission_is_not_duplicated(self):
        self.r.add(seg(1000, b"AAA"))
        self.r.add(seg(1000, b"AAA"))  # 재전송
        self.r.add(seg(1003, b"BBB", fin=True))
        self.assertEqual(self.jobs[0].data, b"AAABBB")

    def test_overlapping_segments_are_trimmed(self):
        self.r.add(seg(1000, b"ABCDEF"))
        self.r.add(seg(1003, b"DEFGHI", fin=True))
        self.assertEqual(self.jobs[0].data, b"ABCDEFGHI")

    def test_idle_timeout_finishes_job(self):
        self.r.add(seg(1000, b"ORDER1", ts=100.0))
        self.r.tick(now=101.0)
        self.assertEqual(self.jobs, [])
        self.r.tick(now=103.0)
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.jobs[0].data, b"ORDER1")

    def test_two_jobs_on_one_connection(self):
        """연결을 유지한 채 두 장을 연달아 보내는 포스도 있다."""
        self.r.add(seg(1000, b"FIRST", ts=100.0))
        self.r.tick(now=103.0)
        self.r.add(seg(1005, b"SECOND", ts=110.0))
        self.r.tick(now=113.0)
        self.assertEqual([j.data for j in self.jobs], [b"FIRST", b"SECOND"])

    def test_separate_printers_are_separate_jobs(self):
        self.r.add(seg(1000, b"KITCHEN", src_port=50001))
        self.r.add(
            Segment("192.168.0.10", 50002, "192.168.0.51", 9100, 2000, b"BAR", ts=100.0)
        )
        self.r.flush_all()
        self.assertEqual({j.data for j in self.jobs}, {b"KITCHEN", b"BAR"})

    def test_sequence_wraparound(self):
        start = (1 << 32) - 3
        self.r.add(seg(start, b"AAA"))
        self.r.add(seg(0, b"BBB", fin=True))
        self.assertEqual(self.jobs[0].data, b"AAABBB")

    def test_empty_stream_produces_no_job(self):
        self.r.add(seg(1000, b"", fin=True))
        self.assertEqual(self.jobs, [])

    def test_rst_flushes(self):
        self.r.add(seg(1000, b"PARTIAL"))
        self.r.add(seg(1007, b"", rst=True))
        self.assertEqual(len(self.jobs), 1)

    def test_max_size_guard(self):
        r = Reassembler(self.jobs.append, max_job_bytes=10)
        r.add(seg(1000, b"0123456789ABC"))
        self.assertEqual(len(self.jobs), 1)


if __name__ == "__main__":
    unittest.main()

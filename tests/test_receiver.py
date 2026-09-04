import socket
import threading
import time
import unittest

from what_number.receiver import PrinterReceiver, status_reply


def cp949(text):
    return text.encode("cp949")


RECEIPT = b"\x1b@" + cp949("테이블 14") + b"\n" + cp949("김치찌개  x2") + b"\n\x1dV\x42\x00"


class StatusReplyTest(unittest.TestCase):
    """진짜 프린터처럼 상태 조회에 답해야 포스가 고장으로 보지 않는다."""

    def test_answers_dle_eot(self):
        self.assertEqual(len(status_reply(b"\x10\x04\x01")), 1)

    def test_answers_each_query_once(self):
        self.assertEqual(len(status_reply(b"\x10\x04\x01\x10\x04\x04")), 2)

    def test_answers_gs_r(self):
        self.assertEqual(len(status_reply(b"\x1dr\x01")), 1)

    def test_no_answer_for_plain_receipt(self):
        self.assertEqual(status_reply(RECEIPT), b"")

    def test_does_not_mistake_cut_command_for_a_query(self):
        self.assertEqual(status_reply(b"\x1dV\x42\x00"), b"")

    def test_truncated_query_is_ignored(self):
        self.assertEqual(status_reply(b"\x10\x04"), b"")


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.jobs = []
        self.receiver = PrinterReceiver(self.jobs.append, port=0, idle_seconds=0.4)
        # 포트 0 은 빈 포트를 자동 배정받는다.
        self.assertTrue(self.receiver.start(), self.receiver.errors)
        self.port = self.receiver._server.getsockname()[1]

    def tearDown(self):
        self.receiver.stop()

    def send(self, data, close=True, wait=0.9):
        client = socket.socket()
        client.settimeout(5)
        client.connect(("127.0.0.1", self.port))
        client.sendall(data)
        if close:
            client.close()
        time.sleep(wait)
        return client

    def test_receives_a_receipt(self):
        self.send(RECEIPT)
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(self.jobs[0].data, RECEIPT)

    def test_receipt_split_across_packets_is_joined(self):
        client = socket.socket()
        client.connect(("127.0.0.1", self.port))
        client.sendall(RECEIPT[:10])
        time.sleep(0.05)
        client.sendall(RECEIPT[10:])
        client.close()
        time.sleep(0.9)
        self.assertEqual(self.jobs[0].data, RECEIPT)

    def test_two_receipts_on_one_connection_are_separate(self):
        """연결을 유지한 채 두 장을 연달아 보내도 따로 잡혀야 한다."""
        client = socket.socket()
        client.connect(("127.0.0.1", self.port))
        client.sendall(RECEIPT)
        time.sleep(0.9)  # 조용해지면 한 장이 끝난 것으로 본다
        client.sendall(RECEIPT)
        time.sleep(0.9)
        client.close()
        self.assertEqual(len(self.jobs), 2)

    def test_status_query_gets_an_answer_back(self):
        client = socket.socket()
        client.settimeout(5)
        client.connect(("127.0.0.1", self.port))
        client.sendall(b"\x10\x04\x01")
        answer = client.recv(8)
        client.close()
        self.assertEqual(len(answer), 1, "상태 조회에 답하지 않으면 포스가 오류를 낼 수 있다")

    def test_two_printers_at_once(self):
        for _ in range(2):
            threading.Thread(target=self.send, args=(RECEIPT,)).start()
        time.sleep(1.4)
        self.assertEqual(len(self.jobs), 2)

    def test_job_records_the_virtual_printer(self):
        self.send(RECEIPT)
        self.assertIn("가상프린터", self.jobs[0].dst_ip)

    def test_empty_connection_creates_no_job(self):
        client = socket.socket()
        client.connect(("127.0.0.1", self.port))
        client.close()
        time.sleep(0.5)
        self.assertEqual(self.jobs, [])


class PortInUseTest(unittest.TestCase):
    def test_reports_error_instead_of_crashing(self):
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            receiver = PrinterReceiver(lambda job: None, port=port, host="127.0.0.1")
            self.assertFalse(receiver.start())
            self.assertTrue(receiver.errors)
        finally:
            blocker.close()


if __name__ == "__main__":
    unittest.main()

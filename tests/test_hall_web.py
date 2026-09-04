import json
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from what_number import hall_web
from what_number.store import OrderStore
from what_number.ticket_store import TicketStore
from what_number.web import serve


def post(url, payload, content_type="application/json", headers=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", content_type)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=5) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8") or "{}")


def get(url):
    with urllib.request.urlopen(url, timeout=5) as res:
        return res.status, res.read().decode("utf-8")


class ChosungTest(unittest.TestCase):
    def test_korean_becomes_initials(self):
        self.assertEqual(hall_web.chosung("고르곤졸라 피자"), "ㄱㄹㄱㅈㄹ ㅍㅈ")

    def test_non_korean_is_left_alone(self):
        self.assertEqual(hall_web.chosung("ICE 아메리카노"), "ICE ㅇㅁㄹㅋㄴ")


class HostGuardTest(unittest.TestCase):
    def test_private_addresses_allowed(self):
        for host in ("localhost:8710", "127.0.0.1:8710", "192.168.0.5:8710", "10.1.2.3"):
            self.assertTrue(hall_web.host_allowed(host), host)

    def test_outside_names_rejected(self):
        for host in ("evil.example.com", "8.8.8.8", "", "attacker.co.kr:8710"):
            self.assertFalse(hall_web.host_allowed(host), host)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tickets = TicketStore(":memory:")
        self.orders = OrderStore(":memory:")
        self.httpd, _ = serve(self.orders, 0, lambda: {}, tickets=self.tickets)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tickets.close()
        self.orders.close()

    def add(self, key="k1", table="2층-21", menus=("고르곤졸라 피자", "봉골레 파스타")):
        return self.tickets.add_ticket(
            source="test", source_key=key, table_label=table,
            items=[{"menu": m} for m in menus],
        )

    def test_page_is_served(self):
        status, body = get(self.base + "/hall")
        self.assertEqual(status, 200)
        self.assertIn("홀 주문서", body)

    def test_state_lists_oldest_first(self):
        now = time.time()
        self.add(key="new", table="새 주문", menus=("피자",))
        self.tickets.add_ticket(source="test", source_key="old", table_label="오래된 주문",
                                items=[{"menu": "파스타"}], received_at=now - 900)
        _, body = get(self.base + "/api/hall/state")
        self.assertEqual(json.loads(body)["tickets"][0]["table"], "오래된 주문")

    def test_check_shows_up_in_the_next_read(self):
        tid = self.add()
        item = self.tickets.get(tid).items[0]
        status, result = post(self.base + "/api/hall/items/%d/served" % item.id, {"served": True})
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        _, body = get(self.base + "/api/hall/state")
        self.assertEqual(json.loads(body)["tickets"][0]["served_count"], 1)

    def test_same_check_twice_gives_the_same_answer(self):
        tid = self.add()
        item = self.tickets.get(tid).items[0]
        url = self.base + "/api/hall/items/%d/served" % item.id
        first = post(url, {"served": True})[1]
        second = post(url, {"served": True})[1]
        self.assertEqual(first["served_count"], second["served_count"])

    def test_completing_a_ticket_reports_became_ready_once(self):
        tid = self.add(menus=("피자",))
        item = self.tickets.get(tid).items[0]
        url = self.base + "/api/hall/items/%d/served" % item.id
        self.assertTrue(post(url, {"served": True})[1]["became_ready"])
        self.assertFalse(post(url, {"served": True})[1]["became_ready"])

    def test_ticket_can_be_cleared_and_restored(self):
        tid = self.add()
        post(self.base + "/api/hall/tickets/%d/status" % tid, {"status": "done"})
        _, body = get(self.base + "/api/hall/state")
        self.assertEqual(json.loads(body)["tickets"], [])
        post(self.base + "/api/hall/tickets/%d/status" % tid, {"status": "open"})
        _, body = get(self.base + "/api/hall/state")
        self.assertEqual(len(json.loads(body)["tickets"]), 1)

    def test_form_post_is_refused(self):
        """다른 사이트에서 몰래 보내는 요청을 막는 핵심 장치."""
        status, _ = post(self.base + "/api/hall/items/1/served", b"served=true",
                         content_type="application/x-www-form-urlencoded")
        self.assertEqual(status, 415)

    def test_cross_site_request_is_refused(self):
        status, _ = post(self.base + "/api/hall/items/1/served", {"served": True},
                         headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_strange_host_is_refused(self):
        status, _ = post(self.base + "/api/hall/items/1/served", {"served": True},
                         headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)

    def test_broken_json_is_refused(self):
        status, _ = post(self.base + "/api/hall/items/1/served", b"{nope")
        self.assertEqual(status, 400)

    def test_wrong_value_is_refused(self):
        tid = self.add()
        item = self.tickets.get(tid).items[0]
        status, _ = post(self.base + "/api/hall/items/%d/served" % item.id, {"served": "네"})
        self.assertEqual(status, 400)

    def test_unknown_item_is_not_found(self):
        status, _ = post(self.base + "/api/hall/items/99999/served", {"served": True})
        self.assertEqual(status, 404)

    def test_oversized_body_is_refused(self):
        status, _ = post(self.base + "/api/hall/items/1/served",
                         b"x" * (hall_web.MAX_BODY + 10))
        self.assertEqual(status, 413)

    def test_search_finds_the_menu(self):
        self.add(key="a", table="2층-5", menus=("고르곤졸라 피자",))
        self.add(key="b", table="2층-6", menus=("봉골레 파스타",))
        _, body = get(self.base + "/api/hall/search?menu=" +
                      urllib.parse.quote("고르곤졸라"))
        self.assertEqual([t["table"] for t in json.loads(body)["tickets"]], ["2층-5"])

    def test_menus_include_initials(self):
        self.add(key="a", menus=("고르곤졸라 피자",))
        _, body = get(self.base + "/api/hall/menus")
        self.assertEqual(json.loads(body)["menus"][0]["cho"], "ㄱㄹㄱㅈㄹ ㅍㅈ")


class PortExclusiveTest(unittest.TestCase):
    """윈도우에서 두 번 켜면 조용히 갈라지지 않고 확실히 실패해야 한다."""

    def test_second_bind_on_same_port_fails(self):
        orders = OrderStore(":memory:")
        first, _ = serve(orders, 0, lambda: {})
        port = first.server_address[1]
        try:
            with self.assertRaises(OSError):
                serve(orders, port, lambda: {})
        finally:
            first.shutdown()
            first.server_close()
            orders.close()


if __name__ == "__main__":
    unittest.main()

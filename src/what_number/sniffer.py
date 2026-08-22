"""포스 PC가 프린터로 내보내는 패킷을 엿듣는다.

윈도우가 기본으로 갖고 있는 raw socket(SIO_RCVALL)을 쓰므로
Npcap·와이어샤크 같은 별도 설치가 필요 없다. 대신 관리자 권한이 필요하다.
"""

from __future__ import annotations

import ctypes
import os
import socket
import struct
import threading
import time
from typing import Callable, Iterable

from .reassembly import Segment

SIO_RCVALL = 0x98000001
RCVALL_ON = 1
RCVALL_OFF = 0


def is_admin() -> bool:
    """관리자 권한으로 실행 중인지."""
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def parse_ipv4_tcp(packet: bytes, ports: Iterable[int], ts: float = 0.0) -> Segment | None:
    """IPv4 패킷에서 TCP 정보를 뽑는다. 대상 포트가 아니면 None."""
    if len(packet) < 20:
        return None
    version_ihl = packet[0]
    if version_ihl >> 4 != 4:
        return None
    ip_header_len = (version_ihl & 0x0F) * 4
    if ip_header_len < 20 or len(packet) < ip_header_len + 20:
        return None
    protocol = packet[9]
    if protocol != socket.IPPROTO_TCP:
        return None
    total_length = struct.unpack("!H", packet[2:4])[0]
    if total_length and total_length <= len(packet):
        packet = packet[:total_length]
    src_ip = socket.inet_ntoa(packet[12:16])
    dst_ip = socket.inet_ntoa(packet[16:20])

    tcp = packet[ip_header_len:]
    src_port, dst_port, seq = struct.unpack("!HHI", tcp[:8])
    if dst_port not in ports:
        return None
    data_offset = (tcp[12] >> 4) * 4
    if data_offset < 20 or len(tcp) < data_offset:
        return None
    flags = tcp[13]
    return Segment(
        src_ip=src_ip,
        src_port=src_port,
        dst_ip=dst_ip,
        dst_port=dst_port,
        seq=seq,
        payload=tcp[data_offset:],
        fin=bool(flags & 0x01),
        rst=bool(flags & 0x04),
        ts=ts or time.time(),
    )


def local_ipv4_addresses() -> list[str]:
    """이 PC가 가진 IPv4 주소들. 랜카드가 여러 개일 수 있어 전부 모은다."""
    found: list[str] = []

    def add(ip: str) -> None:
        if ip and not ip.startswith("127.") and ip not in found:
            found.append(ip)

    try:
        # 바깥으로 나가는 경로에 쓰이는 주소(실제로 보내지는 않는다)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 53))
            add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    return found


class RawSocketSniffer:
    """랜카드마다 raw socket 을 하나씩 열어 패킷을 읽는다."""

    def __init__(
        self,
        on_segment: Callable[[Segment], None],
        ports: Iterable[int] = (9100,),
        bind_ips: Iterable[str] | None = None,
    ):
        self.on_segment = on_segment
        self.ports = set(ports)
        self.bind_ips = list(bind_ips) if bind_ips else local_ipv4_addresses()
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []
        self._stop = threading.Event()
        self.packets_seen = 0
        self.errors: list[str] = []

    def start(self) -> None:
        if not self.bind_ips:
            self.errors.append("랜카드 주소를 찾지 못했습니다. 네트워크 연결을 확인하세요.")
            return
        for ip in self.bind_ips:
            try:
                sock = self._open(ip)
            except OSError as exc:
                self.errors.append(f"{ip} 감시 실패: {exc}")
                continue
            self._sockets.append(sock)
            thread = threading.Thread(target=self._loop, args=(sock,), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _open(self, ip: str) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
        sock.bind((ip, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        if os.name == "nt":
            sock.ioctl(SIO_RCVALL, RCVALL_ON)  # 오가는 패킷을 모두 받는다
        sock.settimeout(1.0)
        return sock

    def _loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                packet = sock.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.2)
                continue
            self.packets_seen += 1
            segment = parse_ipv4_tcp(packet, self.ports)
            if segment is not None and (segment.payload or segment.fin or segment.rst):
                try:
                    self.on_segment(segment)
                except Exception as exc:  # 한 건의 오류로 감시가 멈추지 않도록
                    self.errors.append(f"처리 오류: {exc}")

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def stop(self) -> None:
        self._stop.set()
        for sock in self._sockets:
            try:
                if os.name == "nt":
                    sock.ioctl(SIO_RCVALL, RCVALL_OFF)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._sockets.clear()

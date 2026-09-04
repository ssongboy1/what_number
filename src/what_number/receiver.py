"""가상 프린터. 포스가 보내는 주문서를 프린터인 척 받아낸다.

포스 설정에서 프린터를 하나 더 만들어 이쪽을 가리키게 하면, 포스가 주문서를
한 장 더 보내준다. 기존 프린터들은 전혀 건드리지 않는다.

패킷을 엿듣는 방식과 달리 관리자 권한이 필요 없다.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Callable

from .reassembly import PrintJob

# ESC/POS 프린터는 상태를 물으면 대답한다. 대답이 없으면 포스가 고장으로 볼 수 있어
# "이상 없음"에 해당하는 값을 돌려준다.
_STATUS_OK = 0x12  # DLE EOT 응답의 고정 비트만 세운 값 = 정상
_PAPER_OK = 0x00  # GS r 응답 = 용지 충분


def status_reply(data: bytes) -> bytes:
    """상태 조회 명령에 대한 답. 조회가 없으면 빈 값."""
    reply = bytearray()
    index = 0
    while index < len(data):
        byte = data[index]
        if byte == 0x10 and index + 2 < len(data) and data[index + 1] == 0x04:
            reply.append(_STATUS_OK)  # DLE EOT n
            index += 3
        elif byte == 0x1D and index + 2 < len(data) and data[index + 1] == 0x72:
            reply.append(_PAPER_OK)  # GS r n
            index += 3
        else:
            index += 1
    return bytes(reply)


class PrinterReceiver:
    """프린터인 척 TCP 연결을 받아 주문서를 모은다."""

    def __init__(
        self,
        on_job: Callable[[PrintJob], None],
        port: int = 9100,
        host: str = "0.0.0.0",
        idle_seconds: float = 2.0,
        max_job_bytes: int = 1 << 20,
    ):
        self.on_job = on_job
        self.port = port
        self.host = host
        self.idle_seconds = idle_seconds
        self.max_job_bytes = max_job_bytes
        self.connections = 0
        self.jobs = 0
        self.errors: list[str] = []
        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> bool:
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 윈도우의 SO_REUSEADDR 는 이미 쓰이는 포트에도 붙어버려, 프로그램을 두 번
            # 켰을 때 주문이 갈라져 조용히 누락된다. 충돌이 드러나도록 독점으로 연다.
            if os.name == "nt":
                server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(8)
            server.settimeout(1.0)
        except OSError as exc:
            self.errors.append(f"{self.port} 포트를 열 수 없습니다: {exc}")
            return False
        self._server = server
        thread = threading.Thread(target=self._accept_loop, daemon=True)
        thread.start()
        self._threads.append(thread)
        return True

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.2)
                continue
            self.connections += 1
            worker = threading.Thread(target=self._handle, args=(client, address), daemon=True)
            worker.start()
            self._threads.append(worker)

    def _handle(self, client: socket.socket, address: tuple) -> None:
        """연결 하나를 처리한다. 조용해지면 한 장이 끝난 것으로 본다."""
        source_ip = address[0]
        buffer = bytearray()
        started = time.time()
        client.settimeout(self.idle_seconds)
        try:
            while not self._stop.is_set():
                try:
                    chunk = client.recv(8192)
                except socket.timeout:
                    if buffer:
                        self._emit(buffer, source_ip, started)
                        buffer = bytearray()
                        started = time.time()
                    continue
                except OSError:
                    break
                if not chunk:
                    break

                answer = status_reply(chunk)
                if answer:
                    try:
                        client.sendall(answer)
                    except OSError:
                        pass

                buffer.extend(chunk)
                if len(buffer) >= self.max_job_bytes:
                    self._emit(buffer, source_ip, started)
                    buffer = bytearray()
                    started = time.time()
        finally:
            if buffer:
                self._emit(buffer, source_ip, started)
            try:
                client.close()
            except OSError:
                pass

    def _emit(self, buffer: bytearray, source_ip: str, started: float) -> None:
        self.jobs += 1
        job = PrintJob(
            src_ip=source_ip,
            dst_ip=f"가상프린터:{self.port}",
            dst_port=self.port,
            started_at=started,
            ended_at=time.time(),
            data=bytes(buffer),
        )
        try:
            self.on_job(job)
        except Exception as exc:  # 한 건의 오류로 수신이 멈추지 않도록
            self.errors.append(f"처리 오류: {exc}")

    @property
    def running(self) -> bool:
        return self._server is not None and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

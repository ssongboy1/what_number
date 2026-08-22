"""TCP 조각을 모아 전표 한 장(인쇄 작업) 단위로 되돌린다.

포스는 연결 하나로 여러 장을 연달아 보내기도 하므로,
연결이 끊길 때뿐 아니라 '잠시 조용해지면' 한 장이 끝난 것으로 본다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

_WRAP = 1 << 32


@dataclass(frozen=True)
class Segment:
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    seq: int
    payload: bytes
    fin: bool = False
    rst: bool = False
    ts: float = 0.0


@dataclass
class PrintJob:
    """프린터로 보내진 인쇄 작업 한 건."""

    src_ip: str
    dst_ip: str
    dst_port: int
    started_at: float
    ended_at: float
    data: bytes


@dataclass
class _Stream:
    base_seq: int | None = None
    chunks: dict[int, bytes] = field(default_factory=dict)
    first_ts: float = 0.0
    last_ts: float = 0.0
    total: int = 0


class Reassembler:
    """세그먼트를 받아 인쇄 작업이 완성되면 on_job 으로 넘긴다."""

    def __init__(
        self,
        on_job: Callable[[PrintJob], None],
        idle_seconds: float = 2.0,
        max_job_bytes: int = 1 << 20,
    ):
        self.on_job = on_job
        self.idle_seconds = idle_seconds
        self.max_job_bytes = max_job_bytes
        self._streams: dict[tuple, _Stream] = {}
        self._lock = threading.Lock()

    def add(self, seg: Segment) -> None:
        key = (seg.src_ip, seg.src_port, seg.dst_ip, seg.dst_port)
        now = seg.ts or time.time()
        finished = None
        with self._lock:
            stream = self._streams.get(key)
            if stream is None:
                stream = _Stream()
                self._streams[key] = stream

            if seg.payload:
                if stream.base_seq is None:
                    stream.base_seq = seg.seq
                    stream.first_ts = now
                offset = (seg.seq - stream.base_seq) % _WRAP
                if offset > _WRAP // 2:
                    # 기준보다 앞선 조각(재전송 등)은 기준을 앞당겨 다시 맞춘다.
                    shift = _WRAP - offset
                    stream.chunks = {o + shift: d for o, d in stream.chunks.items()}
                    stream.base_seq = seg.seq
                    offset = 0
                previous = stream.chunks.get(offset)
                if previous is None or len(seg.payload) > len(previous):
                    stream.chunks[offset] = seg.payload
                    stream.total = max(stream.total, offset + len(seg.payload))
                stream.last_ts = now

            if seg.fin or seg.rst or stream.total >= self.max_job_bytes:
                finished = self._take(key, stream)

        if finished is not None:
            self.on_job(finished)

    def tick(self, now: float | None = None) -> None:
        """주기적으로 불러 조용해진 스트림을 마감한다."""
        now = now or time.time()
        done = []
        with self._lock:
            for key, stream in list(self._streams.items()):
                if stream.chunks and now - stream.last_ts >= self.idle_seconds:
                    job = self._take(key, stream)
                    if job is not None:
                        done.append(job)
        for job in done:
            self.on_job(job)

    def flush_all(self) -> None:
        done = []
        with self._lock:
            for key, stream in list(self._streams.items()):
                job = self._take(key, stream)
                if job is not None:
                    done.append(job)
        for job in done:
            self.on_job(job)

    def _take(self, key: tuple, stream: _Stream) -> PrintJob | None:
        """스트림에 쌓인 내용을 인쇄 작업으로 꺼내고 스트림을 비운다."""
        data = _assemble(stream.chunks)
        job = None
        if data:
            src_ip, _src_port, dst_ip, dst_port = key
            job = PrintJob(
                src_ip=src_ip,
                dst_ip=dst_ip,
                dst_port=dst_port,
                started_at=stream.first_ts,
                ended_at=stream.last_ts,
                data=data,
            )
        # 연결이 유지된 채 다음 장이 올 수 있으므로 스트림 자체는 남기고 비운다.
        stream.chunks = {}
        stream.base_seq = None
        stream.total = 0
        stream.first_ts = 0.0
        return job


def _assemble(chunks: dict[int, bytes]) -> bytes:
    """겹치는 부분을 정리하며 오프셋 순서대로 이어 붙인다."""
    if not chunks:
        return b""
    out = bytearray()
    for offset in sorted(chunks):
        payload = chunks[offset]
        if offset < len(out):
            payload = payload[len(out) - offset :]  # 이미 담긴 만큼 잘라낸다
        elif offset > len(out):
            out.extend(b"\x00" * (offset - len(out)))  # 빠진 조각은 자리만 채운다
        out.extend(payload)
    return bytes(out)

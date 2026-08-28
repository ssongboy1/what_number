"""어느 포트로 주문서가 나가는지 직접 찾아낸다.

프린터 포트를 추측하는 대신 모든 TCP 통신을 잠시 지켜보면서,
주문서처럼 생긴 데이터가 어디로 가는지 집계한다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .reassembly import Segment

# 흔한 웹·시스템 통신은 결과에서 빼서 눈에 띄게 한다.
NOISE_PORTS = {80, 443, 53, 123, 137, 138, 139, 445, 3389, 5353}


@dataclass
class Target:
    """어떤 상대(IP·포트)로 얼마나, 어떤 데이터가 갔는지."""

    ip: str
    port: int
    packets: int = 0
    total_bytes: int = 0
    receipt_hits: int = 0  # 주문서처럼 보인 횟수
    first_seen: float = 0.0
    last_seen: float = 0.0
    sample: bytes = b""
    preview: str = ""

    @property
    def score(self) -> int:
        return self.receipt_hits


def looks_like_receipt(data: bytes) -> bool:
    """ESC/POS 주문서처럼 보이는지.

    ESC/POS 는 ESC(0x1B) 로 시작하는 제어 명령을 쓰고, 초기화(ESC @)나
    용지 절단(GS V) 이 거의 항상 들어간다.
    """
    if len(data) < 8:
        return False
    if b"\x1b@" in data or b"\x1dV" in data or b"\x1b!" in data or b"\x1ba" in data:
        return True
    # 명령이 없더라도 한글이 그대로 흐르면 주문서일 수 있다.
    try:
        text = data.decode("cp949")
    except UnicodeDecodeError:
        return False
    korean = sum(1 for ch in text if "가" <= ch <= "힣")
    return korean >= 4


def _preview(data: bytes) -> str:
    """사람이 알아볼 수 있게 앞부분만 글자로 바꿔 본다."""
    for encoding in ("cp949", "utf-8"):
        try:
            text = data.decode(encoding, "replace")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return ""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(cleaned.split())[:70]


class Scanner:
    """모든 포트를 지켜보며 상대별로 집계한다."""

    def __init__(self):
        self.targets: dict[tuple, Target] = {}
        self._lock = threading.Lock()
        self.packets = 0

    def add(self, segment: Segment) -> None:
        if not segment.payload:
            return
        now = segment.ts or time.time()
        key = (segment.dst_ip, segment.dst_port)
        with self._lock:
            self.packets += 1
            target = self.targets.get(key)
            if target is None:
                target = Target(ip=segment.dst_ip, port=segment.dst_port, first_seen=now)
                self.targets[key] = target
            target.packets += 1
            target.total_bytes += len(segment.payload)
            target.last_seen = now
            if looks_like_receipt(segment.payload):
                target.receipt_hits += 1
                if not target.sample:
                    target.sample = segment.payload[:400]
                    target.preview = _preview(segment.payload[:400])
            elif not target.sample:
                target.sample = segment.payload[:400]
                target.preview = _preview(segment.payload[:400])

    def report(self, hide_noise: bool = True) -> str:
        with self._lock:
            targets = list(self.targets.values())

        if hide_noise:
            targets = [t for t in targets if t.port not in NOISE_PORTS or t.receipt_hits]

        # 주문서로 보인 것부터, 그다음 데이터가 많은 것부터
        targets.sort(key=lambda t: (t.score, t.total_bytes), reverse=True)

        lines = []
        lines.append("")
        lines.append("=" * 72)
        lines.append(f"  지켜본 패킷 {self.packets:,}개 / 상대 {len(targets)}곳")
        lines.append("=" * 72)

        if not targets:
            lines.append("")
            lines.append("  아직 아무 통신도 잡히지 않았습니다.")
            lines.append("  포스에서 주문을 한 건 넣어보세요.")
            lines.append("")
            lines.append("  주문을 넣었는데도 계속 비어 있다면, 프린터가 네트워크가 아니라")
            lines.append("  시리얼(COM) 로 연결되어 있다는 뜻입니다.")
            return "\n".join(lines)

        lines.append("")
        lines.append(f"  {'상대 주소':<24}{'포트':>7}{'데이터':>12}{'주문서?':>9}")
        lines.append("  " + "-" * 68)
        for target in targets[:25]:
            mark = f"★ {target.receipt_hits}건" if target.receipt_hits else "-"
            size = f"{target.total_bytes:,}B"
            lines.append(f"  {target.ip:<24}{target.port:>7}{size:>12}{mark:>9}")
            if target.preview:
                lines.append(f"      내용: {target.preview}")

        found = [t for t in targets if t.receipt_hits]
        lines.append("")
        if found:
            lines.append("  ★ 표시된 곳이 주문서가 나가는 프린터입니다.")
            ports = sorted({t.port for t in found})
            lines.append(f"  config.json 의 printer_ports 를 {ports} 로 두면 됩니다.")
        else:
            lines.append("  주문서처럼 보이는 데이터는 없었습니다.")
            lines.append("  프린터가 시리얼(COM) 연결일 가능성이 높습니다.")
        return "\n".join(lines)

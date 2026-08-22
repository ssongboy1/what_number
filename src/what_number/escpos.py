"""ESC/POS 인쇄 바이트를 사람이 읽을 수 있는 전표 텍스트로 되돌린다.

포스가 프린터로 보내는 바이트에는 글자와 제어 명령이 섞여 있다.
제어 명령을 정확한 길이만큼 건너뛰어야 글자만 깨끗하게 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ESC = 0x1B
GS = 0x1D
FS = 0x1C
DLE = 0x10

# ESC <x> 뒤에 붙는 고정 인자 바이트 수. None 은 별도 처리.
_ESC_FIXED = {
    0x20: 1,  # ESC SP  문자 간격
    0x21: 1,  # ESC !   인쇄 모드 일괄 지정
    0x24: 2,  # ESC $   절대 위치
    0x25: 1,  # ESC %   사용자 정의 글꼴 사용
    0x26: None,  # ESC & 사용자 정의 글꼴 등록 (가변)
    0x2A: None,  # ESC * 비트 이미지 (가변)
    0x2D: 1,  # ESC -   밑줄
    0x32: 0,  # ESC 2   기본 줄간격
    0x33: 1,  # ESC 3   줄간격 지정
    0x3D: 1,  # ESC =   주변장치 선택
    0x3F: 1,  # ESC ?   사용자 정의 글꼴 삭제
    0x40: 0,  # ESC @   초기화
    0x41: 1,  # ESC A   줄간격(구형)
    0x44: None,  # ESC D 탭 위치 (NUL 로 끝남)
    0x45: 1,  # ESC E   강조
    0x47: 1,  # ESC G   이중 인쇄
    0x4A: 1,  # ESC J   용지 이송
    0x4B: 1,  # ESC K   역방향 이송
    0x4C: 0,  # ESC L   페이지 모드
    0x4D: 1,  # ESC M   글꼴 선택
    0x52: 1,  # ESC R   국제 문자셋
    0x53: 0,  # ESC S   표준 모드
    0x54: 1,  # ESC T   인쇄 방향
    0x56: 1,  # ESC V   90도 회전
    0x57: 8,  # ESC W   인쇄 영역 지정
    0x5C: 2,  # ESC \   상대 위치
    0x61: 1,  # ESC a   정렬
    0x63: None,  # ESC c 3/4/5 (센서 설정)
    0x64: 1,  # ESC d   n줄 이송
    0x65: 1,  # ESC e   역방향 n줄
    0x66: 2,  # ESC f   대기 시간
    0x69: 0,  # ESC i   용지 절단
    0x6D: 0,  # ESC m   용지 절단
    0x70: 3,  # ESC p   금전함 열기
    0x72: 1,  # ESC r   인쇄 색상
    0x74: 1,  # ESC t   코드 페이지
    0x75: 1,  # ESC u   주변장치 상태
    0x76: 0,  # ESC v   용지 상태 전송
    0x7B: 1,  # ESC {   상하 반전
}

_GS_FIXED = {
    0x21: 1,  # GS !   문자 크기
    0x24: 2,  # GS $   세로 절대 위치
    0x28: None,  # GS ( 확장 명령 (가변)
    0x2A: None,  # GS * 다운로드 비트 이미지 (가변)
    0x2F: 1,  # GS /   다운로드 비트 이미지 인쇄
    0x38: None,  # GS 8 L 대용량 그래픽 (가변)
    0x3A: 0,  # GS :   매크로 정의 시작/끝
    0x42: 1,  # GS B   흑백 반전
    0x43: None,  # GS C 카운터 (가변)
    0x45: 1,  # GS E   인쇄 속도
    0x48: 1,  # GS H   바코드 문자 인쇄 위치
    0x49: 1,  # GS I   프린터 정보 요청
    0x4C: 2,  # GS L   좌측 여백
    0x50: 2,  # GS P   기본 단위
    0x54: 1,  # GS T   인쇄 위치 초기화
    0x56: None,  # GS V 용지 절단 (가변)
    0x57: 2,  # GS W   인쇄 영역 폭
    0x5C: 2,  # GS \   세로 상대 위치
    0x5E: 3,  # GS ^   매크로 실행
    0x61: 1,  # GS a   자동 상태 전송
    0x62: 1,  # GS b   평활화
    0x66: 1,  # GS f   바코드 글꼴
    0x67: None,  # GS g 유지보수 카운터 (가변)
    0x68: 1,  # GS h   바코드 높이
    0x6A: 1,  # GS j   잉크 상태 전송
    0x6B: None,  # GS k 바코드 인쇄 (가변)
    0x72: 1,  # GS r   상태 전송
    0x76: None,  # GS v 래스터 비트 이미지 (가변)
    0x77: 1,  # GS w   바코드 폭
    0x7A: 1,  # GS z   설정값
}

_FS_FIXED = {
    0x21: 1,  # FS !   한글 인쇄 모드
    0x26: 0,  # FS &   한글 모드 지정
    0x28: None,  # FS ( 확장 명령 (가변)
    0x2D: 1,  # FS -   한글 밑줄
    0x2E: 0,  # FS .   한글 모드 해제
    0x32: None,  # FS 2 사용자 정의 한글 등록 (가변)
    0x43: 1,  # FS C   한글 코드 체계
    0x53: 2,  # FS S   한글 문자 간격
    0x57: 1,  # FS W   한글 4배 크기
    0x67: None,  # FS g NV 메모리 (가변)
    0x70: 2,  # FS p   NV 비트 이미지 인쇄
    0x71: None,  # FS q NV 비트 이미지 정의 (가변)
}

_DLE_FIXED = {
    0x04: 1,  # DLE EOT  실시간 상태 전송
    0x05: 1,  # DLE ENQ  실시간 요청
    0x14: 3,  # DLE DC4  실시간 출력
}


@dataclass
class Receipt:
    """전표 한 장을 해석한 결과."""

    text: str = ""
    lines: list[str] = field(default_factory=list)
    has_raster: bool = False  # 주문서가 이미지로 전송된 경우
    raster_bytes: int = 0
    cut: bool = False  # 용지 절단 명령이 있었는지
    unknown_commands: list[str] = field(default_factory=list)
    encoding: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def decode_text(raw: bytes, encoding: str | None = None) -> tuple[str, str]:
    """전표 글자 바이트를 문자열로. (문자열, 사용한 인코딩)을 돌려준다."""
    if encoding:
        return raw.decode(encoding, errors="replace"), encoding
    # UTF-8 은 자기 검증이 되므로 먼저 시도하면 판별기 역할을 한다.
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace"), "cp949"


class _Parser:
    def __init__(self, data: bytes, encoding: str | None):
        self.data = data
        self.pos = 0
        self.encoding = encoding
        self.text_buf = bytearray()
        self.out: list[str] = []
        self.receipt = Receipt()
        self.used_encoding = ""

    # --- 바이트 읽기 도우미 ---
    def _peek(self, offset: int = 0) -> int | None:
        i = self.pos + offset
        return self.data[i] if i < len(self.data) else None

    def _u16(self, at: int) -> int:
        lo = self.data[at] if at < len(self.data) else 0
        hi = self.data[at + 1] if at + 1 < len(self.data) else 0
        return lo + (hi << 8)

    # --- 글자 버퍼 ---
    def _flush_line(self) -> None:
        text, enc = decode_text(bytes(self.text_buf), self.encoding)
        if enc and not self.used_encoding:
            self.used_encoding = enc
        self.text_buf.clear()
        self.out.append(text.rstrip())

    def parse(self) -> Receipt:
        data = self.data
        n = len(data)
        while self.pos < n:
            b = data[self.pos]
            if b == ESC:
                self.pos += 1
                self._command(ESC, _ESC_FIXED, self._esc_variable)
            elif b == GS:
                self.pos += 1
                self._command(GS, _GS_FIXED, self._gs_variable)
            elif b == FS:
                self.pos += 1
                self._command(FS, _FS_FIXED, self._fs_variable)
            elif b == DLE and self._peek(1) in _DLE_FIXED:
                sub = data[self.pos + 1]
                self.pos += 2 + _DLE_FIXED[sub]
            elif b == 0x0A:  # LF 줄바꿈
                self.pos += 1
                self._flush_line()
            elif b == 0x0C:  # FF 페이지 배출
                self.pos += 1
                self._flush_line()
            elif b == 0x0D:  # CR 은 ESC/POS 에서 대개 무시된다
                self.pos += 1
            elif b == 0x09:  # HT 탭
                self.pos += 1
                self.text_buf.extend(b"  ")
            elif b < 0x20 or b == 0x7F:  # 그 밖의 제어문자는 버린다
                self.pos += 1
            else:
                self.text_buf.append(b)
                self.pos += 1

        if self.text_buf:
            self._flush_line()

        r = self.receipt
        r.lines = self.out
        r.text = "\n".join(self.out).strip("\n")
        r.encoding = self.used_encoding
        return r

    def _command(self, prefix: int, table: dict, variable_handler) -> None:
        """명령 한 개를 건너뛴다. 인자 길이를 알아야 뒤 글자가 깨지지 않는다."""
        sub = self._peek()
        if sub is None:
            self.pos += 1
            return
        if sub not in table:
            name = f"{_prefix_name(prefix)} {sub:#04x}"
            if name not in self.receipt.unknown_commands:
                self.receipt.unknown_commands.append(name)
            # 모르는 명령은 인자 1바이트짜리로 가정한다(가장 흔한 형태).
            self.pos += 2
            return
        length = table[sub]
        if length is None:
            variable_handler(sub)
        else:
            self.pos += 1 + length

    # --- 가변 길이 명령 ---
    def _esc_variable(self, sub: int) -> None:
        start = self.pos  # sub 를 가리키는 위치
        if sub == 0x2A:  # ESC * m nL nH  세로 비트 이미지
            m = self.data[start + 1] if start + 1 < len(self.data) else 0
            count = self._u16(start + 2)
            per_col = 3 if m in (32, 33) else 1
            size = count * per_col
            self.receipt.has_raster = True
            self.receipt.raster_bytes += size
            self.pos = start + 4 + size
        elif sub == 0x44:  # ESC D  탭 위치, NUL 로 끝난다
            end = self.data.find(b"\x00", start + 1)
            self.pos = len(self.data) if end < 0 else end + 1
        elif sub == 0x63:  # ESC c 3 / c 4 / c 5
            self.pos = start + 3
        elif sub == 0x26:  # ESC & y c1 c2 [n + n*y 바이트] * (c2-c1+1)
            y = self.data[start + 1] if start + 1 < len(self.data) else 0
            c1 = self.data[start + 2] if start + 2 < len(self.data) else 0
            c2 = self.data[start + 3] if start + 3 < len(self.data) else 0
            p = start + 4
            for _ in range(max(0, c2 - c1 + 1)):
                if p >= len(self.data):
                    break
                width = self.data[p]
                p += 1 + width * y
            self.pos = p
        else:
            self.pos = start + 1

    def _gs_variable(self, sub: int) -> None:
        start = self.pos
        if sub == 0x76:  # GS v 0 m xL xH yL yH  래스터 비트 이미지
            width_bytes = self._u16(start + 3)
            height = self._u16(start + 5)
            size = width_bytes * height
            self.receipt.has_raster = True
            self.receipt.raster_bytes += size
            self.pos = start + 7 + size
        elif sub == 0x28:  # GS ( <fn> pL pH + 데이터
            size = self._u16(start + 2)
            self.receipt.has_raster |= self._peek(1) in (0x4C, 0x38)  # GS ( L / 8
            self.pos = start + 4 + size
        elif sub == 0x38:  # GS 8 L p1..p4 + 데이터
            p = start + 2
            size = 0
            for i in range(4):
                byte = self.data[p + i] if p + i < len(self.data) else 0
                size |= byte << (8 * i)
            self.receipt.has_raster = True
            self.receipt.raster_bytes += size
            self.pos = p + 4 + size
        elif sub == 0x56:  # GS V m [n]
            m = self.data[start + 1] if start + 1 < len(self.data) else 0
            self.receipt.cut = True
            self.pos = start + (3 if m in (65, 66, 97, 98) else 2)
        elif sub == 0x6B:  # GS k  바코드
            m = self.data[start + 1] if start + 1 < len(self.data) else 0
            if m >= 65:  # 길이 지정형
                length = self.data[start + 2] if start + 2 < len(self.data) else 0
                self.pos = start + 3 + length
            else:  # NUL 종료형
                end = self.data.find(b"\x00", start + 2)
                self.pos = len(self.data) if end < 0 else end + 1
        elif sub == 0x2A:  # GS * x y + x*y*8 바이트
            x = self.data[start + 1] if start + 1 < len(self.data) else 0
            y = self.data[start + 2] if start + 2 < len(self.data) else 0
            self.receipt.has_raster = True
            self.receipt.raster_bytes += x * y * 8
            self.pos = start + 3 + x * y * 8
        elif sub == 0x43:  # GS C 0/1/2/;
            nxt = self._peek(1)
            self.pos = start + (4 if nxt in (0x30, 0x31) else 2)
        elif sub == 0x67:  # GS g 0 m nL nH / GS g 2 m nL nH
            self.pos = start + 5
        else:
            self.pos = start + 1

    def _fs_variable(self, sub: int) -> None:
        start = self.pos
        if sub == 0x28:  # FS ( <fn> pL pH + 데이터
            size = self._u16(start + 2)
            self.pos = start + 4 + size
        elif sub == 0x71:  # FS q n  [xL xH yL yH + 데이터] * n
            count = self.data[start + 1] if start + 1 < len(self.data) else 0
            p = start + 2
            for _ in range(count):
                if p + 4 > len(self.data):
                    break
                size = self._u16(p) * self._u16(p + 2)
                self.receipt.has_raster = True
                self.receipt.raster_bytes += size
                p += 4 + size
            self.pos = p
        elif sub == 0x32:  # FS 2 c1 c2 + 글꼴 데이터
            self.pos = start + 3
        elif sub == 0x67:  # FS g 1 / FS g 2
            self.pos = start + 2
        else:
            self.pos = start + 1


def _prefix_name(prefix: int) -> str:
    return {ESC: "ESC", GS: "GS", FS: "FS"}.get(prefix, hex(prefix))


def parse(data: bytes, encoding: str | None = None) -> Receipt:
    """인쇄 바이트 한 뭉치를 전표로 해석한다."""
    return _Parser(data, encoding).parse()

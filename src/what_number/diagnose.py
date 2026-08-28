"""포스 PC 환경 진단.

프린터가 네트워크(TCP)인지 시리얼(COM)인지, 주문 데이터가 어디에 있는지를
한 번에 조사해 사람이 읽을 수 있는 보고서로 남긴다.
읽기만 하며 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 주문 데이터가 들어 있을 만한 파일 형식
DB_SUFFIXES = (".mdb", ".accdb", ".mdf", ".db", ".sqlite", ".sqlite3", ".dbf", ".fdb", ".gdb")

# 포스 프로그램이 설치되어 있을 만한 곳
SEARCH_ROOTS = ("C:\\", "C:\\Program Files", "C:\\Program Files (x86)", "D:\\")

# 폴더 이름에서 찾을 단어
POS_HINTS = ("okpos", "오케이포스", "pos", "포스")

# 프린터 통신에 쓰일 만한 포트
PRINTER_PORTS = ("9100", "9101", "9102", "515", "4001", "6001", "950")

_MAX_DIRS = 40
_MAX_FILES = 60


def _run(command: list, timeout: int = 40) -> str:
    """명령을 돌려 표준 출력을 문자열로. 실패해도 예외를 내지 않는다."""
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(실행 실패: {exc})"
    raw = result.stdout or result.stderr or b""
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace").strip()


def _powershell(script: str, timeout: int = 40) -> str:
    return _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout
    )


def serial_ports() -> list:
    """COM 포트 목록. 이름으로 물리 포트인지 네트워크 가상 포트인지 가늠할 수 있다."""
    lines = []

    # 레지스트리: 실제로 존재하는 COM 포트가 무엇인지
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            lines.append(f"{value}  ←  {name}")
            index += 1
        winreg.CloseKey(key)
    except (ImportError, OSError):
        pass

    # 장치 이름: 제조사까지 나와야 물리인지 가상인지 판별하기 쉽다
    detail = _powershell(
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.Name -match '\\(COM\\d+\\)' } | "
        "ForEach-Object { $_.Name + '  [제조사: ' + $_.Manufacturer + ']' }"
    )
    if detail and not detail.startswith("("):
        lines.extend(line.strip() for line in detail.splitlines() if line.strip())

    return lines or ["(COM 포트를 찾지 못했습니다)"]


def network_activity() -> list:
    """프린터로 나가는 TCP 연결이 실제로 있는지."""
    output = _run(["netstat", "-ano"])
    if output.startswith("("):
        return [output]

    hits = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].upper().startswith("TCP"):
            continue
        port = parts[2].rsplit(":", 1)[-1]
        if port in PRINTER_PORTS:
            hits.append(line.strip())
    return hits or ["(프린터로 나가는 TCP 연결이 보이지 않습니다. 시리얼 방식일 가능성이 있습니다)"]


def _looks_like_pos(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in POS_HINTS)


def pos_folders() -> list:
    """포스 프로그램이 설치된 것으로 보이는 폴더."""
    found = []
    for root in SEARCH_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        try:
            for entry in base.iterdir():
                if len(found) >= _MAX_DIRS:
                    return found
                if entry.is_dir() and _looks_like_pos(entry.name):
                    found.append(entry)
        except (OSError, PermissionError):
            continue
    return found


def database_files(folders: list) -> list:
    """주문 데이터가 들어 있을 만한 파일.

    이름·크기·수정시각만 본다. 내용은 열지 않으므로 개인정보가 보고서에 담기지 않는다.
    """
    results = []
    for folder in folders:
        for pattern in ("*", "*/*", "*/*/*"):
            try:
                for path in folder.glob(pattern):
                    if len(results) >= _MAX_FILES:
                        return results
                    if path.suffix.lower() not in DB_SUFFIXES:
                        continue
                    try:
                        if not path.is_file():
                            continue
                        stat = path.stat()
                    except OSError:
                        continue
                    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    results.append((path, stat.st_size, modified))
            except (OSError, PermissionError):
                continue
    results.sort(key=lambda item: item[2], reverse=True)  # 최근에 바뀐 것부터
    return results


def pos_processes() -> list:
    output = _powershell(
        "Get-Process | Where-Object { $_.ProcessName -match 'pos|okpos|sql|print' } | "
        "ForEach-Object { $_.ProcessName + '  (' + $_.Id + ')' }"
    )
    if not output or output.startswith("("):
        return ["(해당하는 프로그램이 없습니다)"]
    return [line.strip() for line in output.splitlines() if line.strip()]


def database_services() -> list:
    output = _powershell(
        "Get-Service | Where-Object { $_.Name -match 'SQL|MSSQL|Firebird|Postgre|MySQL' } | "
        "ForEach-Object { $_.Name + ' : ' + $_.Status }"
    )
    if not output or output.startswith("("):
        return ["(데이터베이스 서비스가 보이지 않습니다)"]
    return [line.strip() for line in output.splitlines() if line.strip()]


def build_report() -> str:
    from .sniffer import is_admin, local_ipv4_addresses

    out = []

    def section(title: str) -> None:
        out.append("")
        out.append("── " + title + " " + "─" * max(0, 48 - len(title)))

    out.append("=" * 60)
    out.append("  포스 PC 진단 결과")
    out.append(f"  작성 시각: {datetime.now():%Y-%m-%d %H:%M:%S}")
    out.append("=" * 60)

    section("기본 정보")
    out.append(f"운영체제   : {sys.platform}")
    out.append(f"컴퓨터 이름: {socket.gethostname()}")
    out.append(f"관리자 권한: {'예' if is_admin() else '아니오'}")
    out.append(f"IP 주소    : {', '.join(local_ipv4_addresses()) or '(없음)'}")

    if os.name != "nt":
        out.append("")
        out.append("! 윈도우가 아니어서 아래 조사는 건너뜁니다.")
        return "\n".join(out)

    section("COM 포트 (여기가 핵심입니다)")
    out.append("이름에 NPort / Virtual / Serial-over-LAN 등이 보이면 네트워크 방식,")
    out.append("'통신 포트' 나 USB 변환기 이름만 보이면 물리 시리얼 방식입니다.")
    out.append("")
    out.extend(serial_ports())

    section("프린터로 나가는 네트워크 연결")
    out.extend(network_activity())

    section("포스 프로그램 폴더")
    folders = pos_folders()
    if folders:
        out.extend(str(folder) for folder in folders)
    else:
        out.append("(찾지 못했습니다)")

    section("주문 데이터가 들어 있을 만한 파일")
    files = database_files(folders)
    if files:
        for path, size, modified in files:
            out.append(f"{modified}  {size / 1048576:8.1f} MB  {path}")
    else:
        out.append("(찾지 못했습니다)")

    section("실행 중인 관련 프로그램")
    out.extend(pos_processes())

    section("데이터베이스 서비스")
    out.extend(database_services())

    out.append("")
    out.append("=" * 60)
    out.append("  이 파일을 그대로 보내주시면 다음 방법을 정할 수 있습니다.")
    out.append("  ※ 주문 내용이나 개인정보는 담기지 않습니다(파일 이름과 크기만).")
    out.append("=" * 60)
    return "\n".join(out)


def run(output_dir: Path) -> Path:
    report = build_report()
    try:
        print(report)
    except UnicodeEncodeError:
        # 콘솔이 일부 글자를 담지 못해도 보고서 파일은 남겨야 한다.
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(report.encode(encoding, "replace").decode(encoding, "replace"))
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"진단결과_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(report, encoding="utf-8")
    return path

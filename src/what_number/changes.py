"""주문이 들어올 때 이 PC 의 어떤 파일이 바뀌는지 찾는다 (변화찾기).

포스 프로그램은 주문을 받으면 그 내용을 PC 어딘가에 적어 둔다. 테이블 화면에 주문을
띄우고, 껐다 켜도 주문이 남아 있으려면 그래야 한다. 그 파일을 알아내면 포스 화면을
거치지 않고 주문을 읽어올 수 있다.

어디에 무엇을 적는지는 포스마다 달라서 추측하지 않는다. 드라이브 전체의 파일 변화를
윈도우에게 통지받아 모아 두고, 사람이 "방금 주문을 넣었다" 고 표시한 시각과 맞춰
보아 주문과 함께 바뀌는 파일을 골라낸다.

읽기만 한다. 파일을 고치지 않으며, 내용을 볼 때도 포스가 그 파일을 쓰거나 지우거나
이름을 바꾸는 것을 막지 않는 방식으로 잠깐 연다.
"""

from __future__ import annotations

import collections
import functools
import os
import queue
import re
import socket
import struct
import threading
import time
import zipfile
from bisect import bisect_left
from datetime import datetime
from pathlib import Path, PureWindowsPath

from . import diagnose

# 주문을 넣고 이 창으로 와서 엔터를 누르기까지 걸릴 만한 시간(초)
MARK_BEFORE = 120.0
# 엔터를 누른 뒤에 늦게 기록되는 경우(초)
MARK_AFTER = 10.0
# 이 간격 안에 이어진 변화는 한 번으로 센다. DB 는 저장 한 번에 여러 번 통지된다
BURST_GAP = 2.0
# 같은 파일을 화면에 다시 알리는 최소 간격(초)
LIVE_REPEAT = 30.0
# 통지를 받을 수 없을 때 포스 폴더를 직접 훑는 간격(초)
POLL_SECONDS = 5.0

MAX_TRACKED = 20000
MAX_TIMES = 500
MAX_SNAPSHOT = 20000
TAIL_BYTES = 2048
DETAILED = 15

# 끝날 때 복사해 묶을 파일 수와 크기 한도
COPY_LIMIT = 6
COPY_MAX_FILE = 300 * 1048576
COPY_MAX_TOTAL = 800 * 1048576
# SQLite 는 최근 기록을 옆 파일에 따로 둘 수 있어 함께 복사한다
COMPANIONS = ("-wal", "-shm", "-journal")
# 주문과 우연히 같은 때 바뀌었더라도 복사하면 안 되는 개인용 프로그램
PRIVATE_APPS = ("kakao", "\\line\\", "telegram", "nateon", "whatsapp", "\\band\\")

# 윈도우가 알려주는 변화의 종류
ADDED, REMOVED, MODIFIED, RENAMED_OLD, RENAMED_NEW = 1, 2, 3, 4, 5
ACTION_NAMES = {
    ADDED: "새로 생김",
    REMOVED: "지워짐",
    MODIFIED: "내용 바뀜",
    RENAMED_OLD: "이름 바뀜(전)",
    RENAMED_NEW: "이름 바뀜(후)",
}

# 드라이브 바로 아래에서 윈도우가 쓰는 곳. 세기만 하고 보고하지 않는다.
ROOT_NOISE = (
    "windows", "$recycle.bin", "system volume information", "$extend", "config.msi",
    "pagefile.sys", "swapfile.sys", "hiberfil.sys", "dumpstack.log.tmp",
)

# 어디에 있든 주문과 상관없이 늘 바뀌는 곳
NOISE_PARTS = (
    "\\programdata\\microsoft\\",
    "\\program files\\windowsapps\\",
    "\\appdata\\local\\microsoft\\",
    "\\appdata\\roaming\\microsoft\\",
    "\\appdata\\local\\packages\\",
    "\\appdata\\local\\google\\",
    "\\appdata\\local\\naver\\",
    "\\appdata\\local\\mozilla\\",
    "\\appdata\\roaming\\mozilla\\",
    "\\appdata\\local\\connecteddevicesplatform\\",
    "\\appdata\\local\\d3dscache\\",
    "\\ntuser.dat",
    "\\usrclass.dat",
)

# 여러 프로그램이 함께 쓰는 임시 폴더. 기록은 하되 화면에 바로 띄우지는 않는다.
QUIET_PARTS = ("\\temp\\", "\\tmp\\")

# 드라이브 맨 위의 윈도우 기본 폴더. 이 밖에 있는 프로그램은 따로 설치된 것이다.
STANDARD_ROOTS = ("windows", "program files", "program files (x86)", "programdata", "users")

# 파일 앞부분으로 알아보는 형식
SIGNATURES = (
    (b"SQLite format 3\x00", "SQLite 데이터베이스"),
    (b"\x00\x01\x00\x00Standard Jet DB", "Access 데이터베이스(mdb)"),
    (b"\x00\x01\x00\x00Standard ACE DB", "Access 데이터베이스(accdb)"),
    (b"H:2,", "H2 데이터베이스(자바)"),
    (b"\x37\x7f\x06\x82", "SQLite 임시 기록(wal)"),
    (b"\x37\x7f\x06\x83", "SQLite 임시 기록(wal)"),
    (b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7", "SQLite 임시 기록(journal)"),
    (b"PK\x03\x04", "압축 파일(zip)"),
    (b"%PDF", "PDF 문서"),
    (b"\x89PNG", "그림(PNG)"),
    (b"\xff\xd8\xff", "그림(JPG)"),
)

# 앞부분으로 모를 때 확장자로 짐작하는 형식
EXTENSION_KINDS = {
    ".mdf": "SQL Server 데이터베이스",
    ".ldf": "SQL Server 기록",
    ".ndf": "SQL Server 데이터베이스",
    ".fdb": "Firebird 데이터베이스",
    ".gdb": "Firebird 데이터베이스",
    ".dbf": "dBase 데이터베이스",
    ".edb": "ESE 데이터베이스",
    ".realm": "Realm 데이터베이스",
    ".ldb": "LevelDB 또는 Access 잠금 파일",
    ".sst": "LevelDB/RocksDB 데이터",
}

_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")


# ── 경로 분류 ────────────────────────────────────────────────────────

def _norm(path) -> str:
    """윈도우 경로를 비교하기 좋게. 대소문자와 빗금 방향을 맞춘다."""
    return str(path).replace("/", "\\").rstrip("\\").lower()


def _under(norm_path: str, norm_folder: str) -> bool:
    return norm_path == norm_folder or norm_path.startswith(norm_folder + "\\")


def classify(path, pos_folders=(), ignore=()) -> str:
    """'pos'(포스 폴더 안) / 'noise'(늘 바뀌는 곳) / 'quiet'(임시 폴더) / 'normal'."""
    lowered = _norm(path)
    if any(_under(lowered, _norm(folder)) for folder in ignore):
        return "noise"
    if any(_under(lowered, _norm(folder)) for folder in pos_folders):
        return "pos"
    parts = lowered.split("\\")
    if len(parts) > 1 and parts[1] in ROOT_NOISE:
        return "noise"
    padded = lowered + "\\"
    if any(part in padded for part in NOISE_PARTS):
        return "noise"
    if any(part in padded for part in QUIET_PARTS):
        return "quiet"
    return "normal"


def _noise_key(path) -> str:
    """늘 바뀌는 곳을 셀 때 쓰는 폴더 이름. 앞의 세 단계까지만."""
    parts = str(path).replace("/", "\\").split("\\")
    return "\\".join(parts[:3])


def _stat(path):
    """(크기, 폴더인지). 없으면 None. 크기만 보므로 파일을 잠그지 않는다."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_size, (info.st_mode & 0o170000) == 0o040000


# ── 변화 기록 ────────────────────────────────────────────────────────

class FileRecord:
    """한 파일이 지켜보는 동안 어떻게 바뀌었는지."""

    __slots__ = (
        "path", "kind", "bursts", "events", "actions", "last_event",
        "size_before", "size_first", "size_last", "note",
    )

    def __init__(self, path: str, kind: str):
        self.path = path
        self.kind = kind
        self.bursts = []  # 변화가 시작된 시각들
        self.events = 0
        self.actions = set()
        self.last_event = 0.0
        self.size_before = None  # 지켜보기 전 크기(포스 폴더만 안다)
        self.size_first = None  # 처음 바뀌었을 때 잰 크기
        self.size_last = None
        self.note = ""


def _in_window(times: list, low: float, high: float) -> bool:
    index = bisect_left(times, low)
    return index < len(times) and times[index] <= high


def assign(bursts: list, markers: list) -> tuple:
    """변화 하나하나를 어느 주문 표시에 붙일지. (겹친 표시 번호들, 표시 밖 변화 수)

    변화는 그 뒤에 처음 누른 엔터에 붙인다. 주문을 넣고 나서 엔터를 누르기 때문이다.
    뒤따르는 엔터가 너무 멀면 바로 앞 엔터 직후에 늦게 기록된 것인지 본다.
    엔터를 짧은 간격으로 여러 번 눌러도 변화 한 번이 여러 표시에 겹쳐 세어지지 않는다.
    """
    hits = set()
    outside = 0
    for moment in bursts:
        index = bisect_left(markers, moment)
        if index < len(markers) and markers[index] - moment <= MARK_BEFORE:
            hits.add(index)
        elif index > 0 and moment - markers[index - 1] <= MARK_AFTER:
            hits.add(index - 1)
        else:
            outside += 1
    return frozenset(hits), outside


class ChangeLog:
    """통지받은 변화를 파일별로 모은다. 여러 드라이브의 감시가 동시에 부른다."""

    def __init__(self, pos_folders=(), ignore=(), stat=_stat):
        self.pos_folders = [str(folder) for folder in pos_folders]
        self.ignore = [str(folder) for folder in ignore]
        self.baseline = {}  # 정규화 경로 -> 지켜보기 전 크기
        self.records = {}
        self.noise = collections.Counter()
        self.noise_events = 0
        self.dropped = 0
        self.markers = []
        self._stat = stat
        self._dirs = set()
        self._live = collections.deque(maxlen=300)
        self._last_live = {}
        self._lock = threading.Lock()

    def add(self, path: str, action: int, now: float) -> None:
        kind = classify(path, self.pos_folders, self.ignore)
        key = _norm(path)
        with self._lock:
            if kind == "noise":
                self.noise_events += 1
                folder = _noise_key(path)
                if folder in self.noise or len(self.noise) < 500:
                    self.noise[folder] += 1
                return
            if key in self._dirs:
                return
            record = self.records.get(key)
            if record is None:
                if len(self.records) >= MAX_TRACKED:
                    self.dropped += 1
                    return
                record = FileRecord(path, kind)
                record.size_before = self.baseline.get(key)
                self.records[key] = record
            record.events += 1
            record.actions.add(action)
            new_burst = not record.bursts or now - record.last_event > BURST_GAP
            record.last_event = now
            if not new_burst:
                return
            if len(record.bursts) < MAX_TIMES:
                record.bursts.append(now)

        # 크기는 잠금 밖에서 잰다. 느린 디스크가 다른 드라이브의 기록을 붙잡지 않도록.
        found = self._stat(path)
        with self._lock:
            if found is not None and found[1]:
                # 폴더 자체의 변화는 그 안의 파일 변화로 이미 잡힌다.
                self._dirs.add(key)
                self.records.pop(key, None)
                return
            size = found[0] if found is not None else None
            if record.size_first is None:
                record.size_first = size
            record.size_last = size
            if kind != "quiet" and now - self._last_live.get(key, float("-inf")) >= LIVE_REPEAT:
                self._last_live[key] = now
                self._live.append((now, record.path, kind, action))

    def add_missed(self, path: str, action: int, now: float) -> bool:
        """끝에 폴더를 비교해서야 알게 된 변화. 통지로 이미 잡은 것이면 넘어간다."""
        with self._lock:
            if _norm(path) in self.records:
                return False
        if classify(path, self.pos_folders, self.ignore) == "noise":
            return False
        self.add(path, action, now)
        with self._lock:
            record = self.records.get(_norm(path))
            if record is None:
                return False
            record.note = "끝에 비교해서 찾음 (바뀐 시각은 모름)"
        return True

    def take_live(self) -> list:
        """화면에 알릴 새 변화. [(시각, 경로, 분류, 동작)]"""
        with self._lock:
            items = list(self._live)
            self._live.clear()
        return items

    def mark(self, now: float) -> list:
        """'방금 주문을 넣었다' 는 표시를 남기고, 지난 표시 뒤로 바뀐 파일을 돌려준다."""
        with self._lock:
            low = now - MARK_BEFORE
            if self.markers:
                low = max(low, max(self.markers) + 0.001)
            self.markers.append(now)
            recent = [
                (record.path, record.kind, record.bursts[-1])
                for record in self.records.values()
                if record.kind != "quiet" and _in_window(record.bursts, low, now)
            ]
        recent.sort(key=lambda item: (item[1] != "pos", -item[2]))
        return recent

    def ranked(self) -> list:
        """[(기록, 겹친 표시 번호들, 표시 밖에서 바뀐 횟수)] 가능성 높은 순."""
        with self._lock:
            items = [(record, list(record.bursts)) for record in self.records.values()]
            markers = sorted(self.markers)
        rows = []
        for record, bursts in items:
            hits, outside = assign(bursts, markers)
            rows.append((record, hits, outside))
        # 겹친 표시가 많은 것부터. 그중에서도 주문 때 말고는 거의 안 바뀌는 파일을 앞에 둔다.
        # 몇 초마다 늘 바뀌는 기록 파일은 어느 표시에나 겹치므로 겹친 수만으로는 가릴 수 없다.
        rows.sort(key=lambda row: (
            -len(row[1]), row[2] > len(row[1]), row[0].kind != "pos", row[0].kind == "quiet",
            row[2], row[0].path.lower(),
        ))
        return rows


# ── 폴더 훑기 ────────────────────────────────────────────────────────

def snapshot(folders, limit: int = MAX_SNAPSHOT) -> dict:
    """폴더 안 모든 파일의 크기와 수정 시각. {정규화 경로: (경로, 크기, 수정시각)}

    os.stat 은 파일을 열어 정확한 크기를 잰다. 포스가 열어 둔 DB 도 실제 크기가 나온다.
    """
    result = {}
    for folder in folders:
        for dirpath, _dirnames, filenames in os.walk(str(folder)):
            for name in filenames:
                if len(result) >= limit:
                    return result
                path = os.path.join(dirpath, name)
                try:
                    info = os.stat(path)
                except OSError:
                    continue
                result[_norm(path)] = (path, info.st_size, info.st_mtime)
    return result


def compare(before: dict, after: dict, complete: bool = True) -> list:
    """두 기록 사이에 바뀐 파일. [(경로, 동작)]

    어느 한쪽이 개수 제한에 걸려 잘렸으면(complete=False) 지워진 파일은 판단하지 않는다.
    """
    changes = []
    for key, (path, size, mtime) in after.items():
        old = before.get(key)
        if old is None:
            changes.append((path, ADDED))
        elif old[1] != size or old[2] != mtime:
            changes.append((path, MODIFIED))
    if complete:
        for key, (path, _size, _mtime) in before.items():
            if key not in after:
                changes.append((path, REMOVED))
    return changes


# ── 윈도우 변화 통지 ─────────────────────────────────────────────────

FILE_LIST_DIRECTORY = 0x0001
GENERIC_READ = 0x80000000
SHARE_ALL = 0x1 | 0x2 | 0x4  # 읽기·쓰기·지우기(이름 바꾸기 포함)를 막지 않는다
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
NOTIFY_FLAGS = 0x1 | 0x8 | 0x10  # 파일 이름, 크기, 마지막 쓴 시각
ERROR_OPERATION_ABORTED = 995
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
DRIVE_FIXED = 3
BUFFER_SIZE = 256 * 1024


@functools.lru_cache(maxsize=None)
def _kernel32():
    """다른 모듈이 쓰는 windll.kernel32 의 설정을 건드리지 않도록 따로 불러온다."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.ReadDirectoryChangesW.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p,
    )
    kernel.ReadDirectoryChangesW.restype = wintypes.BOOL
    kernel.CancelIoEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.CancelIoEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.GetLogicalDrives.argtypes = ()
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    return kernel


def _invalid(handle) -> bool:
    import ctypes

    return handle is None or handle == 0 or handle == ctypes.c_void_p(-1).value


def parse_notifications(data: bytes) -> list:
    """윈도우가 돌려준 변화 목록(FILE_NOTIFY_INFORMATION)을 [(동작, 상대경로)] 로."""
    results = []
    offset = 0
    while offset + 12 <= len(data):
        next_offset, action, length = struct.unpack_from("<III", data, offset)
        name = data[offset + 12:offset + 12 + length].decode("utf-16-le", "replace")
        results.append((action, name))
        if next_offset == 0:
            break
        offset += next_offset
    return results


def fixed_drives() -> list:
    """이 PC 의 고정 디스크(C:\\, D:\\ ...). USB·네트워크 드라이브는 뺀다."""
    if os.name != "nt":
        return []
    kernel = _kernel32()
    mask = kernel.GetLogicalDrives()
    drives = []
    for index in range(26):
        if mask & (1 << index):
            root = chr(65 + index) + ":\\"
            if kernel.GetDriveTypeW(root) == DRIVE_FIXED:
                drives.append(root)
    return drives


class DirectoryWatcher:
    """폴더(드라이브 전체도 가능) 아래의 파일 변화를 윈도우에게 통지받는다.

    파일을 하나하나 들여다보는 것이 아니라 윈도우가 이미 알고 있는 변화를 전해 듣기만
    하므로, 드라이브 전체를 걸어도 포스 PC 에 부담이 거의 없다.
    """

    def __init__(self, root, on_change, buffer_size: int = BUFFER_SIZE):
        self.root = str(root)
        self.on_change = on_change
        self.buffer_size = buffer_size
        self.errors = []
        self.overflows = 0
        self._handle = None
        self._thread = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if os.name != "nt":
            self.errors.append("파일 변화 통지는 윈도우에서만 받을 수 있습니다")
            return False
        import ctypes

        handle = _kernel32().CreateFileW(
            self.root, FILE_LIST_DIRECTORY, SHARE_ALL, None, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS, None,
        )
        if _invalid(handle):
            self.errors.append(f"{self.root} 를 지켜볼 수 없습니다 (오류 {ctypes.get_last_error()})")
            return False
        self._handle = handle
        self._thread = threading.Thread(target=self._loop, name=f"watch {self.root}", daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel = _kernel32()
        buffer = ctypes.create_string_buffer(self.buffer_size)
        returned = wintypes.DWORD()
        while not self._stop.is_set():
            ok = kernel.ReadDirectoryChangesW(
                self._handle, ctypes.byref(buffer), self.buffer_size, True, NOTIFY_FLAGS,
                ctypes.byref(returned), None, None,
            )
            if not ok:
                code = ctypes.get_last_error()
                if not self._stop.is_set() and code != ERROR_OPERATION_ABORTED:
                    self.errors.append(f"{self.root} 지켜보기가 멈췄습니다 (오류 {code})")
                return
            if returned.value == 0:
                # 변화가 한꺼번에 너무 많아 윈도우가 목록을 버렸다. 그 사이 것은 놓친다.
                self.overflows += 1
                continue
            now = time.time()
            for action, name in parse_notifications(ctypes.string_at(buffer, returned.value)):
                try:
                    self.on_change(os.path.join(self.root, name), action, now)
                except Exception:  # 한 건 처리에 실패했다고 감시 전체가 멈추면 안 된다
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self._handle is None:
            return
        kernel = _kernel32()
        kernel.CancelIoEx(self._handle, None)
        if self._thread is not None:
            self._thread.join(timeout=3)
        if not self.running:
            kernel.CloseHandle(self._handle)
            self._handle = None


# ── 파일 내용 보기 ───────────────────────────────────────────────────

def open_shared(path):
    """다른 프로그램이 그 파일을 쓰고, 지우고, 이름을 바꾸는 것을 막지 않고 연다.

    파이썬의 open() 은 '지우기 허용' 없이 열기 때문에, 읽는 동안 포스가 기록 파일을
    정리하려 하면 실패할 수 있다. 그래서 직접 연다. 읽기 전용 파일 객체를 돌려준다.
    """
    if os.name != "nt":
        return open(path, "rb")

    import ctypes
    import msvcrt

    kernel = _kernel32()
    raw = kernel.CreateFileW(str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, 0, None)
    if _invalid(raw):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(raw, os.O_RDONLY)
    except OSError:
        kernel.CloseHandle(raw)
        raise
    return os.fdopen(fd, "rb")


def read_shared(path, offset: int, length: int) -> bytes:
    with open_shared(path) as handle:
        handle.seek(offset)
        return handle.read(length)


def mask_private(text: str) -> str:
    """결과 파일을 주고받아도 되도록 전화번호와 비밀번호는 가린다."""
    return diagnose._mask_secrets(_PHONE.sub("(전화번호 가림)", text))


def _decode_text(data: bytes, offset: int, head: bytes):
    """글자 파일이면 문자열을, 아니면 None. 끝부분만 읽었으면 잘린 첫 줄은 버린다."""
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        encoding = "utf-16-le" if head.startswith(b"\xff\xfe") else "utf-16-be"
        if offset % 2:
            data = data[1:]
        if len(data) % 2:
            data = data[:-1]
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            return None
    else:
        if b"\x00" in data:
            return None
        if offset > 0:
            newline = data.find(b"\n")
            if 0 <= newline < len(data) - 1:
                data = data[newline + 1:]
        text = None
        for encoding, skip in (("utf-8", 4), ("cp949", 2)):
            for start in range(skip):
                try:
                    text = data[start:].decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is not None:
                break
        if text is None:
            return None
    text = text.lstrip(chr(0xFEFF))  # 파일 맨 앞의 인코딩 표시(BOM)
    if not text:
        return text
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\t\r\n")
    if controls > len(text) * 0.02:
        return None
    return text


def inspect(path) -> tuple:
    """(종류, 마지막 몇 줄). 앞 16바이트와 끝 2KB 만 본다."""
    try:
        size = os.stat(path).st_size
    except OSError:
        return "지금은 없음 (지워졌거나 이름이 바뀜)", []
    if os.path.isdir(path):
        return "폴더", []
    offset = max(0, size - TAIL_BYTES)
    try:
        head = read_shared(path, 0, 16)
        tail = read_shared(path, offset, TAIL_BYTES) if size else b""
    except OSError as exc:
        return _open_error(exc), []

    for magic, name in SIGNATURES:
        if head.startswith(magic):
            return name, []

    text = _decode_text(tail, offset, head)
    if text is not None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        shown = [mask_private(line)[:200] for line in lines[-6:]]
        return "글자 파일", shown

    extension = EXTENSION_KINDS.get(Path(str(path)).suffix.lower())
    if extension:
        return extension, []
    if not head:
        return "빈 파일", []
    return "알 수 없는 형식 (앞부분: " + head.hex() + ")", []


def _open_error(exc: OSError) -> str:
    code = getattr(exc, "winerror", None)
    if code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
        return "포스가 잠가 두어 열 수 없음"
    if isinstance(exc, PermissionError):
        return "권한이 없어 열 수 없음"
    return f"열 수 없음 ({exc.strerror or exc})"


# ── 복사해 묶기 ──────────────────────────────────────────────────────

def copy_candidates(rows, markers) -> list:
    """복사할 파일. 포스 폴더 안에서 주문과 함께 바뀐 것이 먼저다.

    포스 폴더 밖의 파일은 주문 때마다 빠짐없이, 주문 때만 바뀐 경우에만 넣는다.
    다른 프로그램의 자료가 우연히 섞여 나가지 않게 하기 위해서다.
    """
    picked = []
    for record, hits, outside in rows:
        if len(picked) >= COPY_LIMIT:
            break
        if any(word in _norm(record.path) for word in PRIVATE_APPS):
            continue
        if record.kind == "pos":
            if markers and not hits:
                continue
        elif not (len(markers) >= 2 and len(hits) == len(markers) and outside == 0):
            continue
        found = _stat(record.path)
        if found is None or found[1]:
            continue
        picked.append(record)
    return picked


def _copy_into(archive: zipfile.ZipFile, source: str, name: str) -> int:
    """포스가 쓰는 중이어도 막지 않고 읽어 zip 안에 바로 적는다. 복사한 바이트 수."""
    copied = 0
    with open_shared(source) as reader, archive.open(name, "w", force_zip64=True) as writer:
        while copied < COPY_MAX_FILE:
            chunk = reader.read(1048576)
            if not chunk:
                break
            writer.write(chunk)
            copied += len(chunk)
    return copied


# ── 포스 프로그램과 폴더 찾기 ────────────────────────────────────────

def install_root(folder) -> Path:
    """실행 파일이 있는 폴더에서 설치 폴더 맨 위로 올라간다.

    C:\\Palida\\bin 에서 실행 중이면 자료는 C:\\Palida\\data 에 있을 수 있으므로
    C:\\Palida 전체를 포스 폴더로 본다.
    """
    parts = PureWindowsPath(str(folder)).parts
    if len(parts) < 2:
        return Path(str(folder))
    top = parts[1].lower()
    if top in ("program files", "program files (x86)"):
        return Path(str(PureWindowsPath(*parts[:3]))) if len(parts) >= 3 else Path(str(folder))
    if top in STANDARD_ROOTS:
        return Path(str(folder))
    return Path(str(PureWindowsPath(*parts[:2])))


def find_pos_folders(given=None) -> list:
    """포스 폴더. 알려준 폴더, 실행 중인 포스 프로그램의 위치, 이름으로 찾은 폴더 순."""
    found = []

    def add(folder) -> None:
        key = _norm(folder)
        if any(_under(key, _norm(existing)) for existing in found):
            return
        found[:] = [existing for existing in found if not _under(_norm(existing), key)]
        found.append(Path(str(folder)))

    if given and Path(given).is_dir():
        add(given)
    if os.name == "nt":
        for folder in diagnose.pos_program_dirs():
            add(install_root(folder))
        for folder in diagnose.pos_folders():
            add(folder)
    return found


def programs() -> list:
    """실행 중인 프로그램 [(번호, 이름, 경로)]."""
    output = diagnose._powershell(
        "Get-Process | Where-Object { $_.Path } | "
        "ForEach-Object { [string]$_.Id + '|' + $_.ProcessName + '|' + $_.Path }"
    )
    results = []
    for line in output.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            results.append((parts[0], parts[1], parts[2]))
    return results


def is_pos_program(name: str, path: str, pos_folders) -> bool:
    lowered = _norm(path)
    if any(_under(lowered, _norm(folder)) for folder in pos_folders):
        return True
    if any(word in lowered for word in ("\\windows\\", "microsoft", "common files")):
        return False
    return diagnose._looks_like_pos(name) or diagnose._looks_like_pos(path)


def is_custom_install(path: str) -> bool:
    """드라이브 맨 위의 윈도우 기본 폴더가 아닌 곳에 설치된 프로그램인지."""
    parts = PureWindowsPath(path).parts
    return len(parts) > 2 and parts[1].lower() not in STANDARD_ROOTS


def connections(pids) -> list:
    """해당 프로그램들이 맺고 있는 통신. 주문을 다른 PC 나 인터넷으로 보내는지 알 수 있다."""
    wanted = set(pids)
    if not wanted:
        return []
    output = diagnose._run(["netstat", "-ano"])
    results = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() not in ("TCP", "UDP"):
            continue
        if parts[-1] in wanted:
            state = " ".join(parts[3:-1])
            results.append((parts[-1], f"{parts[0]} {parts[1]} -> {parts[2]} {state}".rstrip()))
    return results


def root_folders() -> list:
    """드라이브 맨 위 폴더 이름. 포스 폴더 이름이 정확하지 않을 때 확인용."""
    results = []
    for drive in fixed_drives():
        try:
            names = sorted(entry.name for entry in os.scandir(drive) if entry.is_dir())
        except OSError:
            continue
        shown = [name for name in names if not name.startswith("$") and name.lower() not in ROOT_NOISE]
        results.append((drive, shown))
    return results


# ── 한 번의 변화찾기 ─────────────────────────────────────────────────

class Session:
    """시작할 때와 끝날 때의 기록, 지켜보는 동안의 변화를 모은다."""

    def __init__(self, pos_folders, ignore=(), roots=None):
        self.pos_folders = [Path(str(folder)) for folder in pos_folders]
        self.ignore = [str(folder) for folder in ignore if folder]
        self.log = ChangeLog(self.pos_folders, self.ignore)
        self.roots = roots  # None 이면 고정 디스크 전체
        self.watchers = []
        self.errors = []
        self.started_at = 0.0
        self.finished_at = None
        self.before = {}
        self.missed = 0
        self.programs_start = []
        self.programs_end = []
        self.connections_start = []
        self.connections_end = []
        self.root_folders = []
        self.copies = None  # 끝날 때 복사한 결과 [(원본, zip 안 이름, 메모)]
        self._last_poll = {}
        self._next_poll = 0.0
        self._inspected = {}

    # --- 진행 ---
    def start(self, probe: bool = True) -> None:
        self.started_at = time.time()
        self.before = snapshot(self.pos_folders)
        self.log.baseline = {key: size for key, (_path, size, _mtime) in self.before.items()}
        self._last_poll = self.before

        roots = list(self.roots) if self.roots is not None else fixed_drives()
        for folder in self.pos_folders:
            if not any(_under(_norm(folder), _norm(root)) for root in roots):
                roots.append(str(folder))
        for root in roots:
            watcher = DirectoryWatcher(root, self.log.add)
            if watcher.start():
                self.watchers.append(watcher)
            else:
                self.errors.extend(watcher.errors)

        if probe:
            self.programs_start = programs()
            self.connections_start = connections(self._pos_pids(self.programs_start))
            self.root_folders = root_folders()

    def poll(self) -> None:
        """통지를 받을 수 없을 때만 포스 폴더를 직접 훑어 비교한다."""
        if self.watching or not self.pos_folders:
            return
        now = time.time()
        if now < self._next_poll:
            return
        self._next_poll = now + POLL_SECONDS
        current = snapshot(self.pos_folders)
        for path, action in compare(self._last_poll, current):
            self.log.add(path, action, now)
        self._last_poll = current

    def finish(self, probe: bool = True) -> None:
        self.finished_at = time.time()
        for watcher in self.watchers:
            watcher.stop()
        after = snapshot(self.pos_folders)
        complete = len(after) < MAX_SNAPSHOT and len(self.before) < MAX_SNAPSHOT
        for path, action in compare(self.before, after, complete):
            if self.log.add_missed(path, action, self.finished_at):
                self.missed += 1
        if probe:
            self.programs_end = programs()
            self.connections_end = connections(self._pos_pids(self.programs_end))

    @property
    def watching(self) -> bool:
        return any(watcher.running for watcher in self.watchers)

    @property
    def overflows(self) -> int:
        return sum(watcher.overflows for watcher in self.watchers)

    def pack(self, zip_path: Path) -> Path:
        """결과와, 주문 파일로 보이는 것들의 복사본을 zip 하나로 묶는다."""
        picked = copy_candidates(self.log.ranked(), sorted(self.log.markers))
        self.copies = []
        total = 0
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as archive:
            for number, record in enumerate(picked, 1):
                sources = [record.path]
                sources += [record.path + end for end in COMPANIONS if os.path.isfile(record.path + end)]
                for source in sources:
                    found = _stat(source)
                    size = found[0] if found is not None else 0
                    if size > COPY_MAX_FILE:
                        self.copies.append((source, "", f"너무 커서 뺐습니다 ({_size(size)})"))
                        continue
                    if total + size > COPY_MAX_TOTAL:
                        self.copies.append((source, "", "전체 용량 한도를 넘어 뺐습니다"))
                        continue
                    name = f"files/{number}_{os.path.basename(source)}"
                    try:
                        copied = _copy_into(archive, source, name)
                    except OSError as exc:
                        self.copies.append((source, "", _open_error(exc)))
                        continue
                    total += copied
                    self.copies.append((source, name, _size(copied)))
            archive.writestr("report.txt", self.report())
        return zip_path

    @property
    def copied_count(self) -> int:
        return sum(1 for _source, name, _memo in self.copies or [] if name)

    def _pos_pids(self, listed) -> list:
        return [pid for pid, name, path in listed if is_pos_program(name, path, self.pos_folders)]

    def _inspect(self, record: FileRecord) -> tuple:
        """같은 파일을 여러 번 열지 않도록 크기가 그대로면 전에 본 결과를 쓴다."""
        found = _stat(record.path)
        key = (_norm(record.path), found)
        if key not in self._inspected:
            self._inspected[key] = inspect(record.path)
        return self._inspected[key]

    # --- 결과 ---
    def report(self) -> str:
        from .sniffer import is_admin

        rows = self.log.ranked()
        markers = sorted(self.log.markers)
        end = self.finished_at or time.time()
        out = []

        def section(title: str) -> None:
            out.append("")
            out.append("── " + title + " " + "─" * max(0, 50 - len(title)))

        out.append("=" * 64)
        out.append("  변화찾기 결과 (주문이 들어올 때 바뀌는 파일)")
        out.append(f"  시작: {_clock(self.started_at, True)}   끝: {_clock(end)}   "
                   f"({_duration(end - self.started_at)})"
                   + ("" if self.finished_at else "   [지켜보는 중 중간 저장]"))
        out.append("=" * 64)

        section("기본 정보")
        out.append(f"컴퓨터 이름 : {socket.gethostname()}")
        out.append(f"관리자 권한 : {'예' if is_admin() else '아니오'}")
        out.append(f"지켜본 곳   : {', '.join(w.root for w in self.watchers) or '(통지를 받지 못해 포스 폴더만 직접 훑음)'}")
        out.append(f"주문 표시   : {len(markers)}번" + (
            f" ({', '.join(_clock(mark) for mark in markers)})" if markers else ""))
        out.append(f"바뀐 파일   : {len(rows):,}개 (윈도우가 늘 바꾸는 파일 {self.log.noise_events:,}번은 따로 셈)")
        out.append(f"놓친 변화   : {self.overflows}번 (변화가 한꺼번에 너무 많아 윈도우가 버린 횟수)")
        out.append(f"끝에 비교해서 더 찾은 파일: {self.missed}개")
        if self.log.dropped:
            out.append(f"너무 많아 기록하지 못한 변화: {self.log.dropped:,}번")

        section("포스 폴더")
        if self.pos_folders:
            out.extend(str(folder) for folder in self.pos_folders)
            files = diagnose.database_files(self.pos_folders)
            if files:
                out.append("")
                out.append("(폴더 안에서 데이터베이스로 보이는 파일)")
                for path, size, modified in files:
                    out.append(f"  {modified}  {size / 1048576:8.1f} MB  {path}")
        else:
            out.append("(찾지 못했습니다. 드라이브 전체의 변화는 그대로 모았습니다)")

        detailed = rows[:DETAILED]
        if markers:
            section("★ 주문과 함께 바뀐 파일 (가능성 높은 순)")
        else:
            section("바뀐 파일 (주문 표시가 없어 포스 폴더 먼저, 덜 바뀐 순)")
        if not detailed:
            out.append("(바뀐 파일이 없습니다)")
        for number, (record, hits, outside) in enumerate(detailed, 1):
            kind, lines = self._inspect(record)
            star = "★ " if record.kind == "pos" else ""
            out.append(f"[{number}] {star}{record.path}")
            out.append(f"    종류     : {kind}")
            out.append(f"    크기     : {_size_change(record)}")
            summary = f"{len(record.bursts)}번"
            if markers:
                summary += f" (주문 표시 {len(markers)}번 중 {len(hits)}번과 겹침 / 그 밖의 때 {outside}번)"
            out.append(f"    바뀐 횟수: {summary}")
            out.append(f"    바뀐 시각: {' '.join(_clock(t) for t in record.bursts[:12])}"
                       + (" ..." if len(record.bursts) > 12 else ""))
            out.append(f"    어떻게   : {', '.join(ACTION_NAMES.get(a, str(a)) for a in sorted(record.actions))}")
            if record.note:
                out.append(f"    참고     : {record.note}")
            # 포스와 상관없는 프로그램의 기록 내용은 결과 파일에 싣지 않는다.
            if lines and (record.kind == "pos" or hits):
                out.append("    마지막 줄:")
                out.extend(f"      | {line}" for line in lines)
            out.append("")

        if markers:
            section("주문과 함께 바뀐 폴더")
            folders = _folder_summary(rows)
            if folders:
                for folder, hits, count in folders[:10]:
                    out.append(f"  주문 표시 {len(markers)}번 중 {hits}번  파일 {count}개  {folder}")
            else:
                out.append("(없음)")

        section("복사한 파일 (zip 안의 files 폴더)")
        if self.copies is None:
            out.append("(끝날 때 복사합니다)")
        elif not self.copies:
            out.append("(복사할 만한 파일이 없었습니다)")
        for source, name, memo in self.copies or []:
            out.append(f"  {name or '(복사 못 함)'}  {memo}")
            out.append(f"      <- {source}")

        section("포스 폴더 안에서 바뀐 파일")
        inside = [row for row in rows if row[0].kind == "pos"]
        out.extend(_row_line(row, markers) for row in inside[:80])
        if not inside:
            out.append("(없음)")
        elif len(inside) > 80:
            out.append(f"  ... 외 {len(inside) - 80}개")

        section("그 밖의 곳에서 바뀐 파일")
        others = [row for row in rows if row[0].kind != "pos"]
        out.extend(_row_line(row, markers) for row in others[:80])
        if not others:
            out.append("(없음)")
        elif len(others) > 80:
            out.append(f"  ... 외 {len(others) - 80}개")

        section("실행 중인 포스 프로그램과 통신")
        listed = {}
        for pid, name, path in self.programs_start + self.programs_end:
            if is_pos_program(name, path, self.pos_folders):
                listed[pid] = (name, path)
        if listed:
            for pid, (name, path) in listed.items():
                out.append(f"  [{pid}] {name}  {path}")
        else:
            out.append("(포스로 보이는 프로그램이 없습니다. 포스를 켜 둔 상태에서 실행해야 합니다)")
        for title, found in (("시작할 때", self.connections_start), ("끝날 때", self.connections_end)):
            out.append(f"  - 통신({title})")
            if found:
                out.extend(f"      [{pid}] {line}" for pid, line in found)
            else:
                out.append("      (없음)")

        section("따로 설치된 프로그램 (드라이브 바로 아래 폴더에서 실행 중)")
        custom = {}
        for pid, name, path in self.programs_start + self.programs_end:
            if is_custom_install(path):
                custom[path.lower()] = f"  {name}  {path}"
        out.extend(sorted(custom.values()) or ["(없음)"])

        section("드라이브 맨 위 폴더 (포스 폴더 이름 확인용)")
        for drive, names in self.root_folders:
            out.append(f"  {drive}  " + ", ".join(names))
        if not self.root_folders:
            out.append("(없음)")

        section("늘 바뀌어서 뺀 곳 (많은 순)")
        for folder, count in self.log.noise.most_common(12):
            out.append(f"  {count:>7,}번  {folder}")
        if not self.log.noise:
            out.append("(없음)")

        section("문제")
        problems = list(self.errors)
        for watcher in self.watchers:
            problems.extend(watcher.errors)
        out.extend(problems or ["(없음)"])

        out.append("")
        out.append("=" * 64)
        out.append("  이 파일을 그대로 보내주시면 주문을 어디서 읽을지 정할 수 있습니다.")
        out.append("  ※ 파일 이름과 크기, 그리고 글자로 된 파일은 마지막 몇 줄이 들어 있습니다.")
        out.append("    이 글에서는 전화번호와 비밀번호를 가렸습니다.")
        out.append("  ※ 함께 묶인 복사본에는 매장 주문 기록이 그대로 들어 있습니다.")
        out.append("=" * 64)
        return "\n".join(out)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.report(), encoding="utf-8")
        return path

    def console_summary(self, limit: int = 5) -> list:
        """창에 보여줄 짧은 결과."""
        rows = self.log.ranked()
        markers = self.log.markers
        lines = [f"  주문 표시 {len(markers)}번, 바뀐 파일 {len(rows):,}개"
                 f" (윈도우가 늘 바꾸는 파일 {self.log.noise_events:,}번은 뺐습니다)", ""]
        if markers:
            picked = [row for row in rows if row[1]][:limit]
            lines.append("  주문과 함께 바뀐 파일 (가능성 높은 순)")
        else:
            picked = [row for row in rows if row[0].kind == "pos"][:limit]
            lines.append("  주문 표시가 없었습니다. 포스 폴더 안에서 바뀐 파일:")
        if not picked:
            lines.append("    (없습니다)")
        for number, (record, hits, outside) in enumerate(picked, 1):
            kind, _lines = self._inspect(record)
            star = "★ " if record.kind == "pos" else "  "
            lines.append(f"    {number}. {star}{record.path}")
            detail = kind
            if markers:
                detail += f" · 주문 표시 {len(markers)}번 중 {len(hits)}번 · 그 밖에 {outside}번"
            lines.append(f"         {detail}")
        return lines


def _folder_summary(rows) -> list:
    """새 파일을 주문마다 하나씩 만드는 포스도 있어 폴더 단위로도 묶어 본다."""
    folders = {}
    for record, hits, _outside in rows:
        if not hits:
            continue
        folder = os.path.dirname(record.path)
        entry = folders.setdefault(folder.lower(), [folder, set(), 0])
        entry[1] |= hits
        entry[2] += 1
    result = [(folder, len(hits), count) for folder, hits, count in folders.values()]
    result.sort(key=lambda item: (-item[1], -item[2], item[0].lower()))
    return result


def _row_line(row, markers) -> str:
    record, hits, _outside = row
    last = _clock(record.bursts[-1]) if record.bursts else "--:--:--"
    overlap = f"  표시 {len(hits)}/{len(markers)}" if markers else ""
    return f"  {len(record.bursts):>4}번  {last}{overlap}  {record.path}"


def _clock(stamp: float, with_date: bool = False) -> str:
    moment = datetime.fromtimestamp(stamp)
    return moment.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}분 {seconds % 60}초"


def _size(value) -> str:
    if value is None:
        return "?"
    if value < 1024:
        return f"{value:,} 바이트"
    if value < 1048576:
        return f"{value / 1024:,.1f} KB"
    return f"{value / 1048576:,.1f} MB"


def _size_change(record: FileRecord) -> str:
    found = _stat(record.path)
    now = found[0] if found is not None else None
    if record.size_before is not None:
        start, label = record.size_before, "지켜보기 전"
    else:
        start, label = record.size_first, "처음 바뀐 뒤"
    text = f"{_size(start)} -> {_size(now)} ({label}부터)"
    if start is not None and now is not None and start != now:
        text += f"  {'+' if now > start else '-'}{_size(abs(now - start))}"
    return text


# ── 엔터 입력 ────────────────────────────────────────────────────────

# '끝' 을 영문 자판 상태로 치면 'Rmx', 'q' 를 한글 자판 상태로 치면 'ㅂ' 이 된다.
FINISH_WORDS = ("끝", "종료", "rmx", "q", "ㅂ", "quit", "exit", "end")


class KeyReader:
    """엔터 한 번 = '방금 주문을 넣었다' 는 표시. '끝' 을 치면 멈춘다.

    콘솔이 없는 환경에서는 input() 이 곧바로 EOFError 를 내므로, 그때는 조용히
    표시 없이 정해진 시간까지 지켜본다.
    """

    def __init__(self, read=input, clock=time.time):
        self.marks = queue.Queue()
        self.finished = threading.Event()
        self.closed = False
        self._read = read
        self._clock = clock
        self._thread = threading.Thread(target=self._loop, name="keys", daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def waiting(self) -> bool:
        """아직 입력을 기다리는 중인지. 그렇다면 창을 닫을 때 그 입력을 쓴다."""
        return self._thread.is_alive()

    def stop(self) -> None:
        self.finished.set()

    def join(self, timeout=None) -> None:
        self._thread.join(timeout)

    def _loop(self) -> None:
        while True:
            try:
                line = self._read()
            except (EOFError, OSError, RuntimeError, ValueError, KeyboardInterrupt):
                self.closed = True
                return
            if self.finished.is_set():
                return  # 이미 끝난 뒤 누른 엔터는 창을 닫는 데 쓴다
            if line.strip().lower() in FINISH_WORDS:
                self.finished.set()
                return
            self.marks.put(self._clock())

    def take_marks(self) -> list:
        marks = []
        while True:
            try:
                marks.append(self.marks.get_nowait())
            except queue.Empty:
                return marks

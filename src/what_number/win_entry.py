"""창 안에 끼워 넣는 윈도우 기본 입력칸.

Tk 의 입력칸은 한글을 조합하는 중에 글자가 밀린다. 윈도우의 입력기(IME)와 Tk 가
조합 중인 글자를 서로 다르게 다루기 때문인데, 손볼 자리가 Tk 안쪽이라 밖에서는
고치기 어렵다. 그래서 **입력칸만** 윈도우가 원래 쓰는 것으로 바꾼다. 조합을
운영체제가 직접 맡으므로 밀림이 생기지 않는다.

창 전체를 다른 틀로 바꾸는 것이 아니라 칸 하나만 바꾸는 것이라, 받아올 것이 없고
exe 크기도 그대로다(파이썬에 들어 있는 ctypes 만 쓴다).

쓰는 법:

    entry = NativeEntry(frame, on_change=..., on_escape=...)   # 안 되면 Unsupported
    entry.get()  entry.insert(0, "감바스")  entry.delete(0, "end")  entry.focus_set()

Tk 의 Entry 와 같은 이름들만 갖춰 두어, 쓰는 쪽에서는 바꿔 끼우기만 하면 된다.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

__all__ = ["NativeEntry", "Unsupported", "available"]


class Unsupported(Exception):
    """이 환경에서는 윈도우 입력칸을 못 만든다. 부르는 쪽에서 Tk 입력칸으로 돌아간다."""


# --- 윈도우 상수 ---
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPCHILDREN = 0x02000000
ES_AUTOHSCROLL = 0x0080
WM_SETFONT = 0x0030
WM_COMMAND = 0x0111
WM_CTLCOLOREDIT = 0x0133
WM_KEYDOWN = 0x0100
WM_NCDESTROY = 0x0082
EM_SETSEL = 0x00B1
EN_CHANGE = 0x0300
VK_ESCAPE = 0x1B
GWLP_WNDPROC = -4
DEFAULT_CHARSET = 1
CLEARTYPE_QUALITY = 5
ERROR_CLASS_EXISTS = 1410

# 창 처리 함수가 돌려주는 값. 64비트에서는 8바이트다. 이것을 안 맞추면 창 번호가 잘린다
LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
WNDPROC = (ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
           if sys.platform == "win32" else None)

CLASS_NAME = "WhatNumberEntryHost"


def available() -> bool:
    return sys.platform == "win32"


class _WindowClass(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC if WNDPROC is not None else ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


# --- 윈도우 함수 준비 ---
# 창 종류(class)에는 **모듈 하나짜리** 처리 함수를 등록한다. 입력칸마다 다른 함수를
# 등록하면, 그 입력칸이 없어진 뒤에 만든 창이 사라진 함수를 가리켜 멈춰 버린다.
_user32 = None
_gdi32 = None
_kernel32 = None
_dispatch_proc = None
_hosts = {}  # 창 번호 -> NativeEntry


def _load() -> None:
    global _user32, _gdi32, _kernel32, _dispatch_proc
    if _user32 is not None:
        return
    if not available():
        raise Unsupported("윈도우에서만 쓸 수 있습니다")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # ctypes 에 인자·결과의 크기를 알려준다. 알려주지 않으면 전부 4바이트 정수로 다뤄서
    # 창 번호나 lParam 처럼 8바이트인 값이 잘리거나 'int too long to convert' 로 터진다.
    message_args = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    for name in ("DefWindowProcW", "SendMessageW"):
        function = getattr(user32, name)
        function.argtypes = message_args
        function.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p] + message_args
    user32.CallWindowProcW.restype = LRESULT
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.GetFocus.restype = wintypes.HWND
    user32.SetFocus.restype = wintypes.HWND
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
    gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    gdi32.SetBkColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    _user32, _gdi32, _kernel32 = user32, gdi32, kernel32
    _dispatch_proc = WNDPROC(_dispatch)
    _register_class()


def _change_proc():
    """창 처리 함수를 바꿔 끼우는 함수. 64비트면 ...LongPtrW, 32비트면 ...LongW."""
    change = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
    change.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    change.restype = ctypes.c_void_p
    return change


def _register_class() -> None:
    window_class = _WindowClass()
    window_class.cbSize = ctypes.sizeof(_WindowClass)
    window_class.style = 0
    window_class.lpfnWndProc = _dispatch_proc
    window_class.hInstance = _kernel32.GetModuleHandleW(None)
    window_class.lpszClassName = CLASS_NAME
    if not _user32.RegisterClassExW(ctypes.byref(window_class)):
        error = ctypes.get_last_error()
        if error != ERROR_CLASS_EXISTS:
            raise Unsupported("입력칸 틀의 창 종류를 등록하지 못했습니다(%d)" % error)


def _dispatch(hwnd, message, wparam, lparam):
    """입력칸 틀로 오는 소식을 그 틀을 가진 NativeEntry 에게 넘긴다."""
    entry = _hosts.get(hwnd)
    if entry is not None:
        try:
            handled = entry._handle(message, wparam, lparam)
        except Exception:
            handled = None  # 화면 쪽 문제로 입력칸이 멈추면 안 된다
        if handled is not None:
            return handled
        if message == WM_NCDESTROY:
            _hosts.pop(hwnd, None)
    return _user32.DefWindowProcW(hwnd, message, wparam, lparam)


def _bgr(color: str) -> int:
    """'#1a1f2b' 를 윈도우가 쓰는 숫자로. 윈도우는 파랑-초록-빨강 순서다."""
    text = color.lstrip("#")
    red, green, blue = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


class NativeEntry:
    """Tk 틀(frame) 안에 윈도우 기본 입력칸을 올린다.

    바깥 틀도 우리가 만든 창으로 둔다. 그래야 입력칸의 글자색·배경색을 어둡게 맞추고
    (WM_CTLCOLOREDIT), 글자가 바뀐 것(EN_CHANGE)도 받을 수 있다. Tk 쪽 창 처리는
    건드리지 않는다.
    """

    # on_change / on_escape 는 **윈도우가 메시지를 처리하는 중**에 불린다.
    # 그 안에서 Tk 를 건드리면 안 된다. Tk 가 안에 담아 둔 상태가 엉켜 프로그램이 죽는다
    # ("Fatal Python error: PyEval_RestoreThread ... the GIL is released").
    # 표시만 남기고, 실제 일은 Tk 쪽 시계(after)에서 하도록 할 것.
    # on_change / on_escape 는 **윈도우가 메시지를 처리하는 중**에 불린다.
    # 그 안에서 Tk 를 건드리면 안 된다. Tk 가 안에 담아 둔 상태가 엉켜 프로그램이 죽는다
    # ("Fatal Python error: PyEval_RestoreThread ... the GIL is released").
    # 표시만 남기고, 실제 일은 Tk 쪽 시계(after)에서 하도록 할 것.
    def __init__(self, frame, on_change=None, on_escape=None, background: str = "#1a1f2b",
                 foreground: str = "#f2f5fa", family: str = "Malgun Gothic", size: int = 18,
                 padding: int = 10):
        _load()
        self.frame = frame
        self.on_change = on_change
        self.on_escape = on_escape
        self.padding = padding
        self._alive = False
        self._host = None
        self._edit = None

        frame.update_idletasks()  # 창이 실제로 만들어져야 번호(HWND)를 받을 수 있다
        parent = frame.winfo_id()
        if not parent:
            raise Unsupported("Tk 틀의 창 번호를 받지 못했습니다")

        self._text_color = _bgr(foreground)
        self._back_color = _bgr(background)
        self._brush = _gdi32.CreateSolidBrush(self._back_color)

        width = max(frame.winfo_width(), 60)
        height = max(frame.winfo_height(), 30)
        instance = _kernel32.GetModuleHandleW(None)
        self._host = _user32.CreateWindowExW(
            0, CLASS_NAME, None, WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN,
            0, 0, width, height, parent, None, instance, None)
        if not self._host:
            raise Unsupported("입력칸 틀을 만들지 못했습니다(%d)" % ctypes.get_last_error())
        _hosts[self._host] = self

        self._edit = _user32.CreateWindowExW(
            0, "EDIT", None, WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            padding, padding, max(width - padding * 2, 10), max(height - padding * 2, 10),
            self._host, 1, instance, None)
        if not self._edit:
            error = ctypes.get_last_error()
            self.destroy()
            raise Unsupported("입력칸을 만들지 못했습니다(%d)" % error)

        self._font = _gdi32.CreateFontW(
            -abs(int(size * 1.34)), 0, 0, 0, 400, 0, 0, 0, DEFAULT_CHARSET, 0, 0,
            CLEARTYPE_QUALITY, 0, family)
        _user32.SendMessageW(self._edit, WM_SETFONT, self._font, 1)

        # Esc 로 지우기. 입력칸 자신이 받는 키라서 입력칸 쪽도 한 겹 감싼다.
        # 이 함수는 입력칸이 사라질 때까지 붙들고 있어야 한다(self 에 담아 둔다)
        self._edit_proc = WNDPROC(self._on_edit_message)
        self._old_edit_proc = _change_proc()(
            self._edit, GWLP_WNDPROC, ctypes.cast(self._edit_proc, ctypes.c_void_p))

        self._alive = True
        frame.bind("<Configure>", self._on_resize, add="+")
        frame.bind("<Destroy>", lambda event: self.destroy(), add="+")

    # --- 윈도우에서 오는 소식 ---
    def _handle(self, message, wparam, lparam):
        """우리가 다룰 소식이면 돌려줄 값을, 아니면 None."""
        if message == WM_CTLCOLOREDIT:
            # 입력칸을 어두운 색으로 칠한다. 이것이 없으면 혼자 하얗게 떠 보인다
            _gdi32.SetTextColor(wparam, self._text_color)
            _gdi32.SetBkColor(wparam, self._back_color)
            return self._brush
        if message == WM_COMMAND and (wparam >> 16) == EN_CHANGE:
            if self.on_change is not None:
                self.on_change()
            return 0
        return None

    def _on_edit_message(self, hwnd, message, wparam, lparam):
        if message == WM_KEYDOWN and wparam == VK_ESCAPE:
            if self.on_escape is not None:
                try:
                    self.on_escape()
                except Exception:
                    pass
            return 0
        return _user32.CallWindowProcW(self._old_edit_proc, hwnd, message, wparam, lparam)

    def _on_resize(self, event) -> None:
        if not self._alive:
            return
        width, height = max(event.width, 20), max(event.height, 20)
        _user32.MoveWindow(self._host, 0, 0, width, height, True)
        _user32.MoveWindow(self._edit, self.padding, self.padding,
                           max(width - self.padding * 2, 10),
                           max(height - self.padding * 2, 10), True)

    # --- Tk 의 Entry 와 같은 이름들 ---
    def get(self) -> str:
        if not self._alive:
            return ""
        length = _user32.GetWindowTextLengthW(self._edit)
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(self._edit, buffer, length + 1)
        return buffer.value

    def _set(self, text: str) -> None:
        if not self._alive:
            return
        _user32.SetWindowTextW(self._edit, text)
        _user32.SendMessageW(self._edit, EM_SETSEL, len(text), len(text))

    def delete(self, first=0, last=None) -> None:
        text = self.get()
        start = 0 if first in (0, "0") else int(first)
        stop = len(text) if last in (None, "end") else int(last)
        self._set(text[:start] + text[stop:])

    def insert(self, index, text) -> None:
        current = self.get()
        at = len(current) if index in ("end", "insert") else int(index)
        self._set(current[:at] + str(text) + current[at:])

    def focus_set(self) -> None:
        """초점을 준다. 이미 입력칸에 있으면 건드리지 않는다(조합 중인 한글이 끊긴다)."""
        if self._alive and _user32.GetFocus() != self._edit:
            _user32.SetFocus(self._edit)

    def destroy(self) -> None:
        self._alive = False
        host, self._host = self._host, None
        if host:
            _hosts.pop(host, None)
            try:
                _user32.DestroyWindow(host)  # 입력칸도 같이 없어진다
            except Exception:
                pass
        for name in ("_font", "_brush"):
            handle = getattr(self, name, None)
            if handle:
                setattr(self, name, None)
                try:
                    _gdi32.DeleteObject(handle)
                except Exception:
                    pass

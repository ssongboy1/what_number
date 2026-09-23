"""프로그램 본체. 감시 → 전표 해석 → 저장 → 화면 을 하나로 엮는다."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from . import config as config_module
from . import escpos, orders
from .reassembly import PrintJob, Reassembler
from .sniffer import RawSocketSniffer, is_admin, local_ipv4_addresses
from .store import OrderStore
from .web import serve

ADMIN_HELP = """
관리자 권한이 필요합니다.

  프로그램 아이콘을 마우스 오른쪽 버튼으로 누른 뒤
  [관리자 권한으로 실행] 을 선택해 주세요.

  (매번 하기 번거로우면 아이콘 우클릭 → 속성 → 호환성 →
   '관리자 권한으로 이 프로그램 실행' 을 체크해 두면 됩니다.)
"""


class Application:
    def __init__(self, cfg: config_module.Config, store: OrderStore | None = None):
        self.cfg = cfg
        self.log = print
        self.store = store or OrderStore(
            cfg.db_path,
            dedup_window=cfg.dedup_window_seconds,
            retention_hours=cfg.retention_hours,
        )
        self.reassembler = Reassembler(self._on_job, idle_seconds=cfg.idle_seconds)
        self.sniffer = RawSocketSniffer(
            self.reassembler.add,
            ports=cfg.printer_ports,
            bind_ips=cfg.bind_ips or None,
        )
        self.jobs_seen = 0
        self.printers: list[str] = []
        self._stop = threading.Event()
        self._httpd = None

    # --- 인쇄 작업 처리 ---
    def _on_job(self, job: PrintJob) -> None:
        self.jobs_seen += 1
        if job.dst_ip not in self.printers:
            self.printers.append(job.dst_ip)
        self._save_dump(job)

        receipt = escpos.parse(job.data, self.cfg.encoding or None)
        if receipt.is_empty and not receipt.has_raster:
            return  # 상태 조회 같은 빈 통신은 무시

        unreadable = receipt.is_empty and receipt.has_raster
        text = receipt.text or "(이미지로 전송된 주문서)"
        order = orders.build_order(text, self.cfg.table_patterns, self.cfg.order_no_patterns)
        fingerprint = text if not unreadable else f"{text}#{len(job.data)}:{receipt.raster_bytes}"
        self.store.add(
            content_hash=orders.content_hash(fingerprint),
            table_no=order.table,
            order_no=order.order_no,
            order_type=order.order_type,
            printed_at=order.printed_at,
            item_summary=order.item_summary,
            raw_text=receipt.text,
            printer_ip=job.dst_ip,
            source_ip=job.src_ip,
            has_raster=unreadable,
            now=job.started_at or time.time(),
        )
        label = f"{order.table}번 테이블" if order.table else "번호 미확인"
        self.log(f"  [{datetime.now():%H:%M:%S}] {label}  ({job.dst_ip})")

    def _save_dump(self, job: PrintJob) -> None:
        """인쇄 원본을 최근 몇 건만 남긴다. 인식이 틀렸을 때 원인을 찾는 근거가 된다."""
        keep = self.cfg.keep_raw_dumps
        if keep <= 0:
            return
        directory = self.cfg.dump_dir
        try:
            directory.mkdir(parents=True, exist_ok=True)
            name = f"{datetime.now():%Y%m%d_%H%M%S}_{job.dst_ip.replace('.', '-')}.bin"
            (directory / name).write_bytes(job.data)
            dumps = sorted(directory.glob("*.bin"))
            for old in dumps[:-keep]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    def feed(self, data: bytes, printer_ip: str = "127.0.0.1", source_ip: str = "127.0.0.1") -> None:
        """인쇄 바이트를 직접 넣는다(시연·재생용)."""
        now = time.time()
        self._on_job(PrintJob(source_ip, printer_ip, 9100, now, now, data))

    # --- 상태 ---
    def status(self) -> dict:
        return {
            "capturing": self.sniffer.running,
            "jobs": self.jobs_seen,
            "printers": self.printers,
            "watching": self.sniffer.bind_ips,
            "ports": sorted(self.cfg.printer_ports),
            "errors": self.sniffer.errors[-5:],
        }

    # --- 실행 ---
    def run(self) -> int:
        self.sniffer.start()
        self._httpd, _ = serve(self.store, self.cfg.web_port, self.status)

        threading.Thread(target=self._maintenance, daemon=True).start()
        self._print_banner()

        if self.cfg.open_browser:
            webbrowser.open(f"http://127.0.0.1:{self.cfg.web_port}")

        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
        return 0

    def _maintenance(self) -> None:
        last_purge = 0.0
        while not self._stop.is_set():
            now = time.time()
            self.reassembler.tick(now)
            if now - last_purge > 600:
                self.store.purge_old(now)
                last_purge = now
            time.sleep(0.5)

    def _print_banner(self) -> None:
        port = self.cfg.web_port
        addresses = self.cfg.bind_ips or local_ipv4_addresses()
        print("=" * 58)
        print("  최근 주문 보기 (what_number)")
        print("=" * 58)
        print(f"  이 PC에서 보기 : http://127.0.0.1:{port}")
        for address in addresses:
            print(f"  주방 폰에서 보기: http://{address}:{port}")
        print(f"  감시 포트      : {', '.join(str(p) for p in sorted(self.cfg.printer_ports))}")
        if os.name != "nt":
            print("  ! 이 감시 방식은 윈도우에서만 동작합니다.")
        if self.sniffer.errors:
            print("  ! " + "\n  ! ".join(self.sniffer.errors))
        print("-" * 58)
        print("  주문이 들어오면 아래에 표시됩니다. 끄려면 이 창을 닫으세요.")
        print()

    def shutdown(self) -> None:
        self._stop.set()
        self.sniffer.stop()
        self.reassembler.flush_all()
        if self._httpd is not None:
            self._httpd.shutdown()
        self.store.close()


def replay(cfg: config_module.Config, paths: list[str]) -> int:
    """저장해 둔 인쇄 원본 파일을 그대로 넣어 인식 결과를 확인한다."""
    for path in paths:
        data = Path(path).read_bytes()
        receipt = escpos.parse(data, cfg.encoding or None)
        order = orders.build_order(receipt.text, cfg.table_patterns, cfg.order_no_patterns)
        print(f"\n─── {path} ({len(data):,} 바이트) ───")
        print(f"  테이블   : {order.table or '인식 실패'}")
        print(f"  주문번호 : {order.order_no or '-'}")
        print(f"  구분     : {order.order_type}")
        print(f"  항목     : {order.item_summary or '-'}")
        if receipt.has_raster:
            print(f"  ! 이미지로 전송된 주문서입니다 ({receipt.raster_bytes:,} 바이트)")
        if receipt.unknown_commands:
            print(f"  ! 모르는 명령: {', '.join(receipt.unknown_commands)}")
        print("  ── 읽어낸 전표 ──")
        for line in receipt.lines:
            print(f"  | {line}")
    return 0


def setup_console() -> None:
    """콘솔이 한글을 출력하다 죽지 않게 맞춘다.

    윈도우 콘솔의 기본 코드 페이지가 한글을 담지 못하면 print 한 번에
    UnicodeEncodeError 로 프로그램 전체가 종료된다.
    """
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


KITCHEN_LOG_HELP = r"""
  포스의 주방 기록 파일(kitchenPrinter_trace_날짜.log)을 찾지 못했습니다.

  - 이 프로그램은 VD 포스가 설치된 PC 에서 켜야 합니다.
  - 기록 파일이 있는 폴더를 알면 이렇게 알려줄 수 있습니다.
      what_number.exe --log C:\PaLiDa\logs
    또는 config.json 의 "kitchen_log_dir" 에 그 폴더를 적어 두세요.
  - 포스가 없는 PC 에서 화면만 보려면 가짜 주문으로 시험할 수 있습니다.
      what_number.exe --demo
"""


def _console_window():
    """이 프로그램이 쓰는 검은 창. 없으면 None.

    더블클릭으로 켜면 윈도우가 이 프로그램만을 위해 검은 창을 하나 만들어 준다.
    반대로 사용자가 명령창에서 실행했다면 그 창은 사용자 것이라 건드리면 안 된다.
    """
    if os.name != "nt":
        return None
    try:
        window = ctypes.windll.kernel32.GetConsoleWindow()
        if not window:
            return None
        users = (ctypes.c_uint * 8)()
        if ctypes.windll.kernel32.GetConsoleProcessList(users, 8) != 1:
            return None  # 명령창도 같이 쓰는 창이다
        return window
    except Exception:
        return None


def _show_console(window, visible: bool) -> None:
    try:
        ctypes.windll.user32.ShowWindow(window, 5 if visible else 0)  # 5=보이기, 0=숨기기
    except Exception:
        pass


def demo_window(cfg: config_module.Config, use_gui: bool = True, use_web: bool = False) -> int:
    """포스 없이 시험해 보는 모드. 가짜 주방 기록을 만들어 창을 연다.

    실제 기록과 섞이지 않도록 임시 폴더에 쓰고, 저장도 메모리에만 한다.
    """
    import tempfile

    from .demo import append_sample_order, write_sample_kitchen_log

    folder = Path(tempfile.mkdtemp(prefix="what_number_demo_"))
    write_sample_kitchen_log(folder)
    print()
    print("  시험 모드입니다. 아래 주문은 모두 가짜이고, 실제 포스와는 상관이 없습니다.")
    print(f"  가짜 기록 폴더: {folder}")
    print("  40초마다 새 주문이 한 건씩 들어옵니다.")

    stopping = threading.Event()

    def drip() -> None:
        seq = 0
        while not stopping.wait(40):
            seq += 1
            try:
                append_sample_order(folder, seq)
            except OSError:
                return

    threading.Thread(target=drip, name="demo", daemon=True).start()
    try:
        return watch_kitchen_log(cfg, str(folder), use_gui=use_gui, use_web=use_web,
                                 store_path=":memory:", title="몇번인가요 (시험 모드)")
    finally:
        stopping.set()


def watch_kitchen_log(cfg: config_module.Config, folder: str | None = None,
                      use_gui: bool = True, use_web: bool = False,
                      store_path=None, title: str = "") -> int:
    """포스의 주방 인쇄 기록을 읽어 메뉴 검색 창을 연다. 관리자 권한이 필요 없다.

    기본은 창 하나로 끝난다. 폰·태블릿에서도 보고 싶을 때만 웹 화면을 함께 연다.
    """
    from . import gui as gui_module
    from .kitchen_log import LogFollower, find_log_folder
    from .menu_store import MenuStore

    target = Path(folder) if folder else (Path(cfg.kitchen_log_dir) if cfg.kitchen_log_dir else None)
    if target is None:
        print("  포스의 주방 기록 파일을 찾는 중입니다...")
        target = find_log_folder()
    if target is None or not target.is_dir():
        if target is not None:
            print(f"\n  ! 폴더가 없습니다: {target}")
        print(KITCHEN_LOG_HELP)
        pause()
        return 1

    store = MenuStore(store_path or cfg.data_dir / "menus.db", retention_hours=cfg.retention_hours)
    caught_up = threading.Event()

    def on_ticket(ticket) -> None:
        added = store.add(ticket)
        if not caught_up.is_set() or not added:
            return  # 켤 때 오늘 것을 한꺼번에 읽는 동안은 하나하나 찍지 않는다
        menus = ", ".join(
            menu
            + (f" ({options})" if options else "")
            + (" 취소" if quantity < 0 else (f" x{quantity}" if quantity > 1 else ""))
            for menu, quantity, options in added
        )
        print(f"  {datetime.fromtimestamp(ticket.when):%H:%M:%S}  {ticket.table or '?':<6} {menus}")

    follower = LogFollower(target, on_ticket, offsets=store)
    follower.poll()
    caught_up.set()

    window = None
    if use_gui:
        if gui_module.available():
            note = "주방 기록: " + gui_module.short_path(target)
            if title:
                note = "시험 모드 - 가짜 주문입니다"
            if use_web:
                addresses = local_ipv4_addresses()
                if addresses:
                    note = f"폰·태블릿에서 보기: http://{addresses[0]}:{cfg.web_port}"
            window = gui_module.SearchWindow(store, follower.status, note=note, title=title)
        else:
            print("  ! 이 PC 에서는 창을 띄울 수 없어 검은 창으로만 보여줍니다.")
            use_web = True

    httpd = None
    if use_web:
        try:
            from .search_web import serve_search

            httpd, _ = serve_search(store, cfg.web_port, follower.status)
        except OSError:
            # 포트를 독점으로 열기 때문에, 이미 켜져 있으면 여기서 걸린다.
            print()
            print(f"  ! {cfg.web_port} 번을 이미 다른 프로그램이 쓰고 있습니다.")
            print("    이 프로그램이 이미 켜져 있는지 확인해 보세요.")
            if window is None:
                store.close()
                pause()
                return 1
    follower.start()

    summary = store.summary()
    print("=" * 62)
    print("  몇번인가요 - 메뉴로 테이블 찾기")
    print("=" * 62)
    print(f"  주방 기록 폴더 : {target}")
    print(f"  오늘 주문서    : {summary['tickets']}장")
    if window is not None:
        print("  화면           : 따로 뜬 창에서 메뉴를 검색하세요")
    if httpd is not None:
        print(f"  이 PC에서 보기 : http://127.0.0.1:{cfg.web_port}")
        for address in local_ipv4_addresses():
            print(f"  폰·태블릿에서  : http://{address}:{cfg.web_port}")
    for message in follower.errors:
        print("  ! " + message)
    print("-" * 62)
    print("  포스의 기록을 읽기만 합니다. 이 창을 닫아도 포스에는 영향이 없습니다.")
    print("  새 주문서가 들어오면 아래에 표시됩니다.")
    print("  끄려면 창을 닫으세요." if window is not None else "  끄려면 이 창을 닫으세요.")
    print()

    if httpd is not None and window is None and cfg.open_browser:
        webbrowser.open(f"http://127.0.0.1:{cfg.web_port}")

    def housekeeping() -> None:
        last_purge = time.time()
        while not stopping.is_set():
            if time.time() - last_purge > 600:
                store.purge_old()
                last_purge = time.time()
            stopping.wait(0.5)

    stopping = threading.Event()
    threading.Thread(target=housekeeping, name="housekeeping", daemon=True).start()
    console = _console_window() if window is not None else None
    try:
        if window is not None:
            # 창이 떴으면 검은 창은 숨긴다. 볼 일이 없고, 실수로 닫으면 프로그램이 꺼진다.
            if console is not None:
                _show_console(console, False)
            window.run()  # 창을 닫으면 여기서 빠져나온다
        else:
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    except Exception:
        if console is not None:
            _show_console(console, True)  # 무슨 일이 났는지는 보여야 한다
        raise
    finally:
        stopping.set()
        follower.stop()
        if httpd is not None:
            httpd.shutdown()
        store.close()
    return 0


def receive(cfg: config_module.Config, port: int = 9100) -> int:
    """가상 프린터로 주문서를 받는다. 관리자 권한이 필요 없다."""
    from .receiver import PrinterReceiver

    app = Application(cfg)
    receiver = PrinterReceiver(app._on_job, port=port, idle_seconds=cfg.idle_seconds)
    started = receiver.start()

    app._httpd, _ = serve(app.store, cfg.web_port, lambda: {
        "capturing": receiver.running,
        "jobs": receiver.jobs,
        "printers": [f"가상프린터:{port}"],
        "watching": [f"{port} 포트에서 대기 중"],
        "ports": [port],
        "errors": receiver.errors[-5:],
    })
    threading.Thread(target=app._maintenance, daemon=True).start()

    addresses = local_ipv4_addresses()
    print("=" * 62)
    print("  가상 프린터로 받는 중")
    print("=" * 62)
    if not started:
        for message in receiver.errors:
            print("  ! " + message)
        print(f"  {port} 포트를 다른 프로그램이 쓰고 있을 수 있습니다.")
        print("  --receive 뒤에 다른 번호를 적어 보세요. 예: --receive 9101")
        app.shutdown()
        pause()
        return 1

    print("  포스 프린터 설정에서 [신규생성] 으로 한 줄 추가하세요.")
    print()
    print("    프린터종류 : NET-Bixolon계열")
    print(f"    프린터포트 : {addresses[0] if addresses else '127.0.0.1'}   <- 이 PC 주소")
    print(f"    프린터속도 : {port}")
    print()
    print("  포스가 여러 대면 포스마다 한 줄씩 추가하되 주소는 모두 같게 하세요.")
    print("  프린터속도 칸에 이 번호를 넣을 수 없으면, 넣을 수 있는 번호를 고른 뒤")
    print("  프로그램을 그 번호로 다시 켜세요. 예: --receive 9600")
    print()
    print(f"  주문 화면 : http://127.0.0.1:{cfg.web_port}")
    for address in addresses:
        print(f"  주방 폰   : http://{address}:{cfg.web_port}")
    print("-" * 62)
    print("  주문이 들어오면 아래에 표시됩니다. 끄려면 이 창을 닫으세요.")
    print()

    if cfg.open_browser:
        webbrowser.open(f"http://127.0.0.1:{cfg.web_port}")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()
        app.shutdown()
    return 0


def scan(cfg: config_module.Config, seconds: int = 180) -> int:
    """모든 포트를 지켜보며 주문서가 어디로 나가는지 찾아낸다."""
    from .scan import Scanner

    scanner = Scanner()
    sniffer = RawSocketSniffer(scanner.add, ports=None, bind_ips=cfg.bind_ips or None)
    sniffer.start()

    print("=" * 72)
    print("  주문서가 어디로 나가는지 찾는 중입니다")
    print("=" * 72)
    print(f"  감시 대상 : {', '.join(sniffer.bind_ips) or '(랜카드를 찾지 못했습니다)'}")
    print(f"  감시 시간 : {seconds}초")
    if sniffer.errors:
        print("  ! " + "\n  ! ".join(sniffer.errors))
    print()
    print("  >>> 지금 포스에서 주문을 한 건 넣어주세요. <<<")
    print()
    print("  (끝날 때까지 기다리거나, Ctrl+C 를 눌러 바로 결과를 봅니다)")

    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            time.sleep(1.0)
            left = int(deadline - time.time())
            if left % 30 == 0 and left > 0:
                print(f"  ... {left}초 남음 (패킷 {scanner.packets:,}개)")
    except KeyboardInterrupt:
        print("\n  중단했습니다.")
    finally:
        sniffer.stop()

    report = scanner.report()
    print(report)
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.data_dir / f"포트탐색_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path.write_text(report, encoding="utf-8")
        print(f"\n결과가 저장되었습니다: {path}")
    except OSError:
        pass
    pause()
    return 0


def find_changes(cfg: config_module.Config, folder: str | None = None, seconds: int = 1800) -> int:
    """주문이 들어올 때 이 PC 의 어떤 파일이 바뀌는지 찾는다. 읽기만 한다."""
    from . import changes

    print("  포스 폴더를 찾고 지금 상태를 기록하는 중입니다...")
    folders = changes.find_pos_folders(folder)
    ignore = [cfg.data_dir, getattr(sys, "_MEIPASS", None)]
    session = changes.Session(folders, ignore=ignore)
    session.start()
    out_path = cfg.data_dir / f"변화찾기_{datetime.now():%Y%m%d_%H%M%S}.txt"

    print("=" * 66)
    print("  주문이 들어올 때 바뀌는 파일 찾기 (변화찾기)")
    print("=" * 66)
    if folder and not Path(folder).is_dir():
        print(f"  ! 알려주신 폴더가 없습니다: {folder}")
    print(f"  포스 폴더  : {', '.join(str(f) for f in folders) or '(찾지 못함. 드라이브 전체를 봅니다)'}")
    print(f"  지켜보는 곳: {', '.join(w.root for w in session.watchers) or '(포스 폴더만)'}"
          "  (읽기만 합니다)")
    length = f"{seconds // 60}분" if seconds >= 60 else f"{seconds}초"
    print(f"  최대 {length} 동안 지켜보며, 결과는 30초마다 저장합니다.")
    for message in session.errors:
        print("  ! " + message)
    print("-" * 66)
    print("  1. 포스에서 주문을 한 건 넣으세요. (주방 주문서가 나올 때까지)")
    print("  2. 곧바로 이 검은 창을 한 번 누르고 [엔터] 를 누르세요.")
    print("     '방금 주문 넣었음' 표시가 남습니다.")
    print("  3. 1~2 를 두세 번 되풀이하면 더 정확해집니다.")
    print("  4. 다 했으면  끝  이라고 치고 [엔터].")
    print("-" * 66)
    print("  바뀐 파일이 아래에 나타납니다. (★ = 포스 폴더 안의 파일)")
    print()

    reader = changes.KeyReader()
    reader.start()
    deadline = time.time() + seconds
    next_save = time.time() + 30
    try:
        while time.time() < deadline and not reader.finished.is_set():
            time.sleep(0.5)
            for stamp, path, kind, action in session.log.take_live():
                star = "★" if kind == "pos" else " "
                name = changes.ACTION_NAMES.get(action, "바뀜")
                print(f"  {datetime.fromtimestamp(stamp):%H:%M:%S} {star} {name:<8} {path}")
            for stamp in reader.take_marks():
                recent = session.log.mark(stamp)
                count = len(session.log.markers)
                print()
                print(f"  ----- {datetime.fromtimestamp(stamp):%H:%M:%S}  주문 표시 {count}번째 -----")
                if recent:
                    print("  직전에 바뀐 파일:")
                    for path, kind, _last in recent[:8]:
                        print(f"    {'★' if kind == 'pos' else ' '} {path}")
                    if len(recent) > 8:
                        print(f"      ... 외 {len(recent) - 8}개")
                else:
                    print("  직전에 바뀐 파일이 없습니다.")
                    print("  주문을 넣은 뒤에 엔터를 눌렀는지 확인해 주세요.")
                print()
            session.poll()
            if time.time() >= next_save:
                next_save += 30
                try:
                    session.save(out_path)
                except OSError:
                    pass
    except KeyboardInterrupt:
        print("\n  중단했습니다.")
    finally:
        reader.stop()
        print("\n  마무리하는 중입니다...")
        session.finish()

    print("  주문 파일로 보이는 것을 복사해 묶는 중입니다. (몇십 초 걸릴 수 있습니다)")
    zip_path = out_path.with_suffix(".zip")
    try:
        session.pack(zip_path)
    except (OSError, ValueError) as exc:
        print(f"  ! 복사해 묶지 못했습니다: {exc}")
        zip_path = None

    print()
    print("=" * 66)
    print("  결과")
    print("=" * 66)
    for line in session.console_summary():
        print(line)
    print()
    try:
        session.save(out_path)
    except OSError as exc:
        print(f"  ! 결과 글을 저장하지 못했습니다: {exc}")
    if zip_path is not None:
        print(f"  보내주실 파일: {zip_path}")
        print(f"  결과 글과 복사한 파일 {session.copied_count}개가 함께 들어 있습니다.")
        print("  ※ 복사본에는 매장 주문 기록이 그대로 들어 있습니다.")
        print("    믿을 수 있는 곳에만 보내 주세요.")
    else:
        print(f"  보내주실 파일: {out_path}")

    if reader.waiting:
        # 입력을 기다리던 쪽이 이 엔터를 받아 끝나므로, 여기서 따로 기다리지 않는다.
        print("\n엔터를 누르면 창이 닫힙니다...")
        reader.join()
    else:
        pause()
    return 0


def pause() -> None:
    """더블클릭으로 실행했을 때 창이 즉시 닫혀 내용을 못 보는 일을 막는다.

    콘솔이 아닌 환경(자동 실행·파이프)에서는 입력을 기다릴 수 없으므로 그냥 넘어간다.
    """
    if os.name != "nt":
        return
    try:
        input("\n엔터를 누르면 창이 닫힙니다...")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    setup_console()

    parser = argparse.ArgumentParser(description="포스가 프린터로 보내는 주문서를 모아 보여줍니다.")
    parser.add_argument("--port", type=int, help="화면 주소의 포트 번호")
    parser.add_argument("--no-browser", action="store_true", help="시작할 때 브라우저를 열지 않음")
    parser.add_argument("--replay", nargs="+", metavar="파일", help="저장된 인쇄 원본으로 인식 시험")
    parser.add_argument("--시험", "--demo", dest="demo", action="store_true",
                        help="포스 없이 가짜 주문으로 창을 시험해 보기")
    parser.add_argument("--진단", "--diagnose", dest="diagnose", action="store_true",
                        help="이 PC의 프린터 연결 방식과 주문 데이터 위치를 조사")
    parser.add_argument("--탐색", "--scan", dest="scan", nargs="?", const=180, type=int,
                        metavar="초", help="모든 포트를 지켜보며 주문서가 어디로 나가는지 찾기")
    parser.add_argument("--수신", "--receive", dest="receive", nargs="?", const=9100, type=int,
                        metavar="포트", help="가상 프린터가 되어 주문서를 받기 (관리자 권한 불필요)")
    parser.add_argument("--기록", "--log", dest="log", nargs="?", const="", metavar="폴더",
                        help="포스의 주방 기록을 읽어 메뉴로 테이블 찾기 (옵션 없이 켜도 기록이 있으면 이 방식)")
    parser.add_argument("--웹", "--web", dest="web", action="store_true",
                        help="폰·태블릿에서도 볼 수 있게 웹 화면을 함께 연다")
    parser.add_argument("--창없이", "--no-gui", dest="no_gui", action="store_true",
                        help="창을 띄우지 않고 검은 창으로만 보여준다")
    parser.add_argument("--변화찾기", "--changes", dest="changes", nargs="?", const="",
                        metavar="폴더", help="주문이 들어올 때 이 PC 의 어떤 파일이 바뀌는지 찾기")
    parser.add_argument("--seconds", type=int, default=1800, metavar="초",
                        help="변화찾기를 몇 초 동안 할지 (기본 1800초 = 30분)")
    args = parser.parse_args(argv)

    cfg = config_module.load()
    if args.port:
        cfg.web_port = args.port
    if args.no_browser:
        cfg.open_browser = False

    if args.diagnose:
        from . import diagnose as diagnose_module

        path = diagnose_module.run(cfg.data_dir)
        print(f"\n결과가 저장되었습니다: {path}")
        pause()
        return 0

    if args.changes is not None:
        return find_changes(cfg, args.changes or None, max(1, args.seconds))

    if args.replay:
        return replay(cfg, args.replay)

    if args.demo:
        return demo_window(cfg, use_gui=not args.no_gui, use_web=args.web)

    if args.receive:
        return receive(cfg, args.receive)

    if args.log is not None:
        return watch_kitchen_log(cfg, args.log or None, use_gui=not args.no_gui, use_web=args.web)

    if not args.scan:
        # 옵션 없이 켰을 때: 포스의 주방 기록이 있으면 그것을 읽는다. 더블클릭만으로 쓰게 하려고.
        from .kitchen_log import find_log_folder

        found = cfg.kitchen_log_dir or find_log_folder()
        if found:
            return watch_kitchen_log(cfg, str(found), use_gui=not args.no_gui, use_web=args.web)
        if not args.receive:
            # 기록이 없으면 무엇을 해야 하는지 알려준다. 예전 감시 방식은 --탐색 으로만 쓴다.
            print(KITCHEN_LOG_HELP)
            pause()
            return 1

    if not is_admin():
        print(ADMIN_HELP)
        pause()
        return 1

    if args.scan:
        return scan(cfg, args.scan)

    try:
        return Application(cfg).run()
    except Exception as exc:  # 창이 즉시 닫혀 원인을 못 보는 일을 막는다
        print(f"\n오류가 발생했습니다: {exc}")
        pause()
        return 1


if __name__ == "__main__":
    sys.exit(main())

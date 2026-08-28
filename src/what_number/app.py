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


def demo(cfg: config_module.Config, count: int = 8) -> int:
    """포스 없이 가짜 주문을 넣어 화면이 제대로 뜨는지 확인한다."""
    from .demo import random_receipt

    cfg.keep_raw_dumps = 0
    app = Application(cfg, store=OrderStore(":memory:", dedup_window=cfg.dedup_window_seconds))
    for i in range(count):
        app.feed(random_receipt(i + 1), printer_ip="192.168.0.50")
    app._httpd, _ = serve(app.store, cfg.web_port, app.status)
    threading.Thread(target=app._maintenance, daemon=True).start()
    print()
    print("  시연 모드입니다. 가짜 주문 %d건을 넣었습니다." % count)
    print(f"  화면 주소: http://127.0.0.1:{cfg.web_port}")
    print("  끄려면 이 창을 닫으세요.")
    print()
    if cfg.open_browser:
        webbrowser.open(f"http://127.0.0.1:{cfg.web_port}")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
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
    parser.add_argument("--demo", action="store_true", help="포스 없이 가짜 주문으로 화면만 확인")
    parser.add_argument("--진단", "--diagnose", dest="diagnose", action="store_true",
                        help="이 PC의 프린터 연결 방식과 주문 데이터 위치를 조사")
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

    if args.replay:
        return replay(cfg, args.replay)

    if args.demo:
        return demo(cfg)

    if not is_admin():
        print(ADMIN_HELP)
        pause()
        return 1

    try:
        return Application(cfg).run()
    except Exception as exc:  # 창이 즉시 닫혀 원인을 못 보는 일을 막는다
        print(f"\n오류가 발생했습니다: {exc}")
        pause()
        return 1


if __name__ == "__main__":
    sys.exit(main())

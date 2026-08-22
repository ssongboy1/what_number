"""설정 파일(config.json). exe 옆에 자동으로 만들어지고, 메모장으로 고칠 수 있다."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .orders import DEFAULT_ORDER_NO_PATTERNS, DEFAULT_TABLE_PATTERNS


def app_dir() -> Path:
    """exe(또는 소스)가 놓인 폴더. 설정과 기록을 이 옆에 둔다."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


@dataclass
class Config:
    web_port: int = 8710
    printer_ports: list[int] = field(default_factory=lambda: [9100, 9101, 9102, 515])
    idle_seconds: float = 2.0
    dedup_window_seconds: float = 120.0
    retention_hours: float = 48.0
    encoding: str = ""  # 비워두면 자동 판별(UTF-8 → CP949)
    open_browser: bool = True
    keep_raw_dumps: int = 50  # 최근 인쇄 원본을 몇 건까지 보관할지(진단용)
    bind_ips: list[str] = field(default_factory=list)  # 비우면 자동 탐색
    table_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_TABLE_PATTERNS))
    order_no_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_ORDER_NO_PATTERNS))

    @property
    def data_dir(self) -> Path:
        return app_dir() / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "orders.db"

    @property
    def dump_dir(self) -> Path:
        return self.data_dir / "dumps"


def config_path() -> Path:
    return app_dir() / "config.json"


def load() -> Config:
    path = config_path()
    config = Config()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        known = {f for f in asdict(config)}
        for key, value in raw.items():
            if key in known:
                setattr(config, key, value)
    else:
        save(config)
    return config


def save(config: Config) -> None:
    try:
        config_path().write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def lan_url(port: int) -> str:
    """주방 폰에서 접속할 주소."""
    from .sniffer import local_ipv4_addresses

    addresses = local_ipv4_addresses()
    host = addresses[0] if addresses else "127.0.0.1"
    return f"http://{host}:{port}"
